"""
Tableau Server / Tableau Cloud REST client
==========================================
Signs in, lists workbooks, and downloads one as a .twbx — the three calls the
migration needs from a Tableau environment.

This is the Tableau counterpart to `download_qlik_app` in engine_runner.py, and
it is deliberately shaped like it: credentials arrive per-call, are used, and are
never stored or logged. Nothing here writes to disk except the workbook download,
which streams.

Three things differ from the Qlik path and drive the design:

1. **Auth is two-step.** Qlik takes a bearer token straight on every request.
   Tableau exchanges a Personal Access Token for a short-lived *credentials
   token* plus a *site id*, and both are needed for every later call. `SignIn`
   holds that pair.

2. **The API is versioned in the URL.** `/api/3.19/...` — and a version the
   server does not implement is rejected outright, so the version is negotiated
   from `/api/serverinfo` rather than hardcoded and hoped for.

3. **The host is arbitrary.** Tableau Cloud lives under known domains, but
   Tableau Server is customer-hosted on any hostname at all. A suffix allowlist
   alone therefore cannot be the whole story, so self-hosted Servers must be
   named by the operator in TABLEAU_ALLOWED_HOSTS. The browser can never
   introduce a new host on its own.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Tableau Cloud pods. Tableau Server is customer-hosted and cannot be covered by
# a suffix, which is what TABLEAU_ALLOWED_HOSTS below is for.
TABLEAU_CLOUD_SUFFIXES = (".online.tableau.com", ".tableau.com")

# Exact hostnames an operator has vouched for, comma-separated. This is the only
# way a self-hosted Tableau Server becomes reachable: it is read from the
# server's environment, never from the request.
ALLOWED_HOSTS_ENV = "TABLEAU_ALLOWED_HOSTS"

# Falls back to a version old enough to be present on any supported Server, used
# only when serverinfo cannot be read. 3.4 ships with Tableau Server 2019.1.
FALLBACK_API_VERSION = "3.4"

SIGNIN_TIMEOUT_SECONDS = 60
METADATA_TIMEOUT_SECONDS = 120
DOWNLOAD_TIMEOUT_SECONDS = 1800

# The REST API namespaces its XML. Every find() below needs it.
NS = {"t": "http://tableau.com/api"}


class TableauError(RuntimeError):
    """Anything the Tableau environment refused, phrased for the operator."""


# ----------------------------------------------------------------------
# Host policy
# ----------------------------------------------------------------------

def operator_allowed_hosts():
    """Exact hostnames the operator vouched for, lowercased."""
    raw = os.environ.get(ALLOWED_HOSTS_ENV, "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def validate_host(base_url):
    """Returns the normalised base URL, or raises if the host is not allowed.

    Tableau Cloud is allowed by suffix. A self-hosted Server must be named
    exactly in TABLEAU_ALLOWED_HOSTS — the error says so, because an operator
    hitting this needs to know the fix is an env var and not a typo.
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise TableauError("No Tableau server URL was supplied.")

    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https":
        raise TableauError(
            "Only https is supported for Tableau (got %r). A Personal Access "
            "Token must never cross an unencrypted connection."
            % (parsed.scheme or "no scheme")
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise TableauError("Could not read a hostname out of %r." % base_url)

    if host.endswith(TABLEAU_CLOUD_SUFFIXES) or host in operator_allowed_hosts():
        return "%s://%s%s" % (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"))

    raise TableauError(
        "Host %r is not an allowed Tableau environment.\n\n"
        "Tableau Cloud (%s) is allowed automatically. A self-hosted Tableau "
        "Server is not, because its hostname could be anything — add it to the "
        "%s environment variable on the machine running this server, as a "
        "comma-separated list of exact hostnames, then restart."
        % (host, " or ".join(TABLEAU_CLOUD_SUFFIXES), ALLOWED_HOSTS_ENV)
    )


# ----------------------------------------------------------------------
# HTTP plumbing
# ----------------------------------------------------------------------

def _request(url, token=None, method="GET", body=None, accept="application/xml"):
    request = urllib.request.Request(url, data=body, method=method)
    # The metadata endpoints answer XML. The download endpoint does NOT accept
    # "application/octet-stream" and answers 406 Not Acceptable to it, so it
    # asks for */* instead — the file's real content type is whatever Tableau
    # decides to send, and constraining it only ever rejects the request.
    request.add_header("Accept", accept)
    if body is not None:
        request.add_header("Content-Type", "application/xml")
    if token:
        request.add_header("X-Tableau-Auth", token)
    return request


def _describe_http_error(err, what):
    """Tableau states the real reason in an XML error body; surface it.

    Without this the operator sees "401 Unauthorized" and has no way to tell a
    wrong PAT from a PAT that is fine but scoped to a different site.
    """
    raw = ""
    try:
        raw = err.read().decode("utf-8", "replace").strip()
    except Exception:                                    # noqa: BLE001
        pass

    detail = ""
    if raw:
        try:
            error = ET.fromstring(raw).find("t:error", NS)
            if error is not None:
                summary = error.findtext("t:summary", "", NS).strip()
                message = error.findtext("t:detail", "", NS).strip()
                code = error.get("code", "")
                detail = "\n\nTableau said:\n%s%s%s" % (
                    summary or "(no summary)",
                    " [%s]" % code if code else "",
                    "\n%s" % message if message else "",
                )
        except ET.ParseError:
            # Not the REST API's XML error — this is the web server's own error
            # page. Dumping the HTML fills the screen with stylesheet noise and
            # buries the one useful line, so only the page's title is kept.
            stripped = raw.lstrip()
            if stripped[:9].lower() in ("<!doctype", "<html") or "<html" in stripped[:200].lower():
                title = re.search(r"<title[^>]*>(.*?)</title>", raw,
                                  re.IGNORECASE | re.DOTALL)
                detail = ("\n\nThe server returned an HTML error page rather than a "
                          "REST response%s. That usually means the request never "
                          "reached the REST API — check the server URL points at the "
                          "Tableau server root, with no trailing path."
                          % (": %s" % " ".join(title.group(1).split()) if title else ""))
            else:
                detail = "\n\nTableau said:\n%s" % raw[:400]

    if not detail:
        detail = "\n\nTableau returned no error details (empty body)."

    if err.code == 401:
        detail += ("\n\nA 401 on sign-in usually means the Personal Access Token "
                   "name or secret is wrong, the token has expired (they lapse "
                   "after 15 consecutive days unused), or it belongs to a "
                   "different site than the one requested.")
    elif err.code == 403:
        detail += ("\n\nA 403 usually means the token's user lacks permission on "
                   "this content — downloading a workbook needs at least the "
                   "Download/Save As capability on it.")
    elif err.code == 406:
        detail += ("\n\nA 406 means the server would not answer in the content type "
                   "the request asked for. Downloads must ask for */* — Tableau "
                   "rejects an explicit 'application/octet-stream'.")
    elif err.code == 404:
        detail += ("\n\nA 404 here often means the site content URL is wrong. "
                   "The Default site is addressed as an empty string, not the "
                   "word 'Default'.")

    return "%s failed: %s %s%s" % (what, err.code, err.reason, detail)


def _open(request, timeout, what):
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as err:
        raise TableauError(_describe_http_error(err, what))
    except urllib.error.URLError as err:
        raise TableauError(
            "Could not reach the Tableau server: %s\n\nCheck the server URL, and "
            "that this machine can route to it." % err.reason
        )


# ----------------------------------------------------------------------
# API version
# ----------------------------------------------------------------------

def negotiate_api_version(base_url, note=None):
    """Asks the server which REST API version it speaks.

    Unauthenticated by design — /api/serverinfo is the one endpoint that answers
    before sign-in, which is exactly why the version can be settled first.
    """
    url = "%s/api/%s/serverinfo" % (base_url, FALLBACK_API_VERSION)
    try:
        with _open(_request(url), SIGNIN_TIMEOUT_SECONDS, "Reading server info") as response:
            root = ET.fromstring(response.read())
        version = (root.findtext(".//t:restApiVersion", "", NS) or "").strip()
        if version:
            if note:
                note("[tableau] Server speaks REST API %s." % version)
            return version
    except (TableauError, ET.ParseError) as err:
        if note:
            note("[tableau] Could not read the API version (%s); using %s."
                 % (err, FALLBACK_API_VERSION))
    return FALLBACK_API_VERSION


# ----------------------------------------------------------------------
# Sign in
# ----------------------------------------------------------------------

class SignIn:
    """A signed-in Tableau session: credentials token plus the site it is on."""

    def __init__(self, base_url, api_version, token, site_id, site_content_url, user_id=""):
        self.base_url = base_url
        self.api_version = api_version
        self.token = token
        self.site_id = site_id
        self.site_content_url = site_content_url
        self.user_id = user_id

    @property
    def site_url(self):
        return "%s/api/%s/sites/%s" % (self.base_url, self.api_version, self.site_id)

    def __repr__(self):                                   # pragma: no cover
        # No token in the repr: this object reaches log lines and tracebacks.
        return "<SignIn site=%r api=%s>" % (self.site_content_url, self.api_version)


def sign_in(base_url, pat_name, pat_secret, site_content_url="", api_version=None, note=None):
    """Exchanges a Personal Access Token for a credentials token and site id.

    `site_content_url` is the site's URL segment, not its display name. The
    Default site is the empty string.
    """
    base = validate_host(base_url)

    missing = [label for label, value in
               (("a token name", pat_name), ("a token secret", pat_secret))
               if not str(value or "").strip()]
    if missing:
        raise TableauError("Tableau sign-in needs %s." % " and ".join(missing))

    version = api_version or negotiate_api_version(base, note)
    site = str(site_content_url or "").strip()

    # Built with ElementTree rather than string formatting so a token containing
    # &, < or > is escaped correctly instead of producing malformed XML.
    request_root = ET.Element("tsRequest")
    credentials = ET.SubElement(request_root, "credentials", {
        "personalAccessTokenName": str(pat_name).strip(),
        "personalAccessTokenSecret": str(pat_secret).strip(),
    })
    ET.SubElement(credentials, "site", {"contentUrl": site})
    body = ET.tostring(request_root, encoding="utf-8")

    if note:
        note("[tableau] Signing in to %s (site %r)…" % (base, site or "Default"))

    url = "%s/api/%s/auth/signin" % (base, version)
    with _open(_request(url, method="POST", body=body),
               SIGNIN_TIMEOUT_SECONDS, "Tableau sign-in") as response:
        root = ET.fromstring(response.read())

    creds = root.find("t:credentials", NS)
    if creds is None or not creds.get("token"):
        raise TableauError("Tableau accepted the sign-in but returned no token.")

    site_element = creds.find("t:site", NS)
    user_element = creds.find("t:user", NS)
    session = SignIn(
        base_url=base,
        api_version=version,
        token=creds.get("token"),
        site_id=(site_element.get("id") if site_element is not None else ""),
        site_content_url=(site_element.get("contentUrl", "") if site_element is not None else site),
        user_id=(user_element.get("id", "") if user_element is not None else ""),
    )
    if not session.site_id:
        raise TableauError("Tableau signed in but named no site, so no content can be addressed.")
    if note:
        note("[tableau] Signed in.")
    return session


def sign_out(session, note=None):
    """Best-effort session teardown.

    A failure here is deliberately swallowed: the token expires on its own, and
    a migration that already succeeded must not be reported as failed because
    the courtesy sign-out did not land.
    """
    if not session or not session.token:
        return
    url = "%s/api/%s/auth/signout" % (session.base_url, session.api_version)
    try:
        with _open(_request(url, session.token, method="POST", body=b""),
                   SIGNIN_TIMEOUT_SECONDS, "Tableau sign-out"):
            pass
    except TableauError as err:
        if note:
            note("[tableau] Sign-out did not complete (%s); the token expires on its own." % err)


# ----------------------------------------------------------------------
# Content
# ----------------------------------------------------------------------

def list_workbooks(session, page_size=100, max_pages=100, note=None):
    """Every workbook visible to the token's user on the signed-in site.

    Paginated: Tableau caps a page at 1000 and a real site runs to thousands,
    so a single unpaged call would silently return a truncated list — which
    would look to the user like workbooks had gone missing.
    """
    workbooks = []
    page = 1
    while page <= max_pages:
        url = "%s/workbooks?pageSize=%d&pageNumber=%d" % (session.site_url, page_size, page)
        with _open(_request(url, session.token),
                   METADATA_TIMEOUT_SECONDS, "Listing workbooks") as response:
            root = ET.fromstring(response.read())

        found = root.findall(".//t:workbooks/t:workbook", NS)
        for wb in found:
            project = wb.find("t:project", NS)
            owner = wb.find("t:owner", NS)
            workbooks.append({
                "id": wb.get("id", ""),
                "name": wb.get("name", ""),
                "contentUrl": wb.get("contentUrl", ""),
                "createdAt": wb.get("createdAt", ""),
                "updatedAt": wb.get("updatedAt", ""),
                "size": wb.get("size", ""),          # megabytes, as Tableau reports it
                "webpageUrl": wb.get("webpageUrl", ""),
                "project": project.get("name", "") if project is not None else "",
                "owner": owner.get("id", "") if owner is not None else "",
            })

        pagination = root.find(".//t:pagination", NS)
        if pagination is None:
            break
        try:
            total = int(pagination.get("totalAvailable", "0"))
        except ValueError:
            break
        if page * page_size >= total or not found:
            break
        page += 1

    if note:
        note("[tableau] Found %d workbook%s." % (len(workbooks), "" if len(workbooks) == 1 else "s"))
    return workbooks


def download_workbook(session, workbook_id, dest_path, include_extract=False,
                      note=None, free_space_check=None):
    """Downloads one workbook to dest_path; returns (bytes_written, filename).

    Streamed rather than buffered, for the same reason the Qlik export is: a
    workbook with an extract runs to gigabytes, and holding one in memory to
    hand it straight to a parser is waste that also makes the transfer fragile.

    `include_extract` defaults to False — the migration reads *structure* from
    the workbook XML, and bundled extracts multiply the download size without
    contributing to it. Row data is fetched separately, when it is wanted.
    """
    if not workbook_id:
        raise TableauError("No workbook id was supplied.")

    url = "%s/workbooks/%s/content?includeExtract=%s" % (
        session.site_url,
        urllib.parse.quote(str(workbook_id)),
        "true" if include_extract else "false",
    )

    if note:
        note("[tableau] Downloading workbook %s%s…"
             % (workbook_id, " with its extract" if include_extract else ""))

    # Refuse to start rather than discover halfway down that there is no room.
    if free_space_check:
        free_space_check(os.path.dirname(dest_path))

    request = _request(url, session.token, accept="*/*")
    written = 0
    with _open(request, DOWNLOAD_TIMEOUT_SECONDS, "Downloading the workbook") as response:
        # Tableau names the file — and its extension tells us whether this is a
        # packaged .twbx or a bare .twb, which the parser must know.
        filename = _filename_from(response.headers.get("Content-Disposition"), workbook_id)
        try:
            with open(dest_path, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
        except OSError as err:
            raise TableauError("Could not write the downloaded workbook: %s" % err)

    if written == 0:
        raise TableauError("Tableau returned an empty workbook file.")

    if note:
        note("[tableau] Downloaded %s (%.1f MB)." % (filename, written / 1048576.0))
    return written, filename


def list_datasources(session, page_size=100, max_pages=100, note=None):
    """Every published datasource visible on the signed-in site.

    A workbook built on a published datasource packages no extract of its own --
    the rows live in the datasource item. Migrating such a workbook without
    these would produce a model with the right schema and no data.
    """
    datasources = []
    page = 1
    while page <= max_pages:
        url = "%s/datasources?pageSize=%d&pageNumber=%d" % (session.site_url, page_size, page)
        with _open(_request(url, session.token),
                   METADATA_TIMEOUT_SECONDS, "Listing datasources") as response:
            root = ET.fromstring(response.read())

        found = root.findall(".//t:datasources/t:datasource", NS)
        for datasource in found:
            project = datasource.find("t:project", NS)
            datasources.append({
                "id": datasource.get("id", ""),
                "name": datasource.get("name", ""),
                "contentUrl": datasource.get("contentUrl", ""),
                "type": datasource.get("type", ""),
                "hasExtracts": (datasource.get("hasExtracts", "") or "").lower() == "true",
                "project": project.get("name", "") if project is not None else "",
            })

        pagination = root.find(".//t:pagination", NS)
        if pagination is None:
            break
        try:
            total = int(pagination.get("totalAvailable", "0"))
        except ValueError:
            break
        if page * page_size >= total or not found:
            break
        page += 1

    if note:
        note("[tableau] Site has %d published datasource(s)." % len(datasources))
    return datasources


def download_datasource(session, datasource_id, dest_path, include_extract=True,
                        note=None, free_space_check=None):
    """Downloads one published datasource (.tdsx) to dest_path.

    A .tdsx is a zip like a .twbx and carries the datasource's .hyper extract,
    which is where a workbook bound to it keeps its rows.
    """
    if not datasource_id:
        raise TableauError("No datasource id was supplied.")

    url = "%s/datasources/%s/content?includeExtract=%s" % (
        session.site_url,
        urllib.parse.quote(str(datasource_id)),
        "true" if include_extract else "false",
    )
    if note:
        note("[tableau] Downloading published datasource %s…" % datasource_id)
    if free_space_check:
        free_space_check(os.path.dirname(dest_path))

    written = 0
    with _open(_request(url, session.token, accept="*/*"),
               DOWNLOAD_TIMEOUT_SECONDS, "Downloading the datasource") as response:
        filename = _filename_from(response.headers.get("Content-Disposition"),
                                  datasource_id)
        try:
            with open(dest_path, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
        except OSError as err:
            raise TableauError("Could not write the downloaded datasource: %s" % err)

    if note:
        note("[tableau] Downloaded %s (%.1f MB)." % (filename, written / 1048576.0))
    return written, filename


def _filename_from(content_disposition, fallback_id):
    """Pulls the filename out of a Content-Disposition header.

    The extension is the point: .twbx and .twb are parsed differently, and
    guessing wrong means the parser opens a zip that is not one.
    """
    default = "workbook_%s.twbx" % fallback_id
    if not content_disposition:
        return default

    for part in content_disposition.split(";"):
        part = part.strip()
        for prefix in ("filename*=UTF-8''", "filename="):
            if part.lower().startswith(prefix.lower()):
                name = part[len(prefix):].strip().strip('"')
                if prefix.endswith("''"):
                    name = urllib.parse.unquote(name)
                name = os.path.basename(name.replace("\\", "/")).strip()
                if name:
                    return name
    return default


# ----------------------------------------------------------------------
# One-shot convenience
# ----------------------------------------------------------------------

def fetch_workbook(base_url, pat_name, pat_secret, workbook_id, dest_path,
                   site_content_url="", include_extract=False, note=None,
                   free_space_check=None):
    """Sign in, download one workbook, sign out. Returns (bytes, filename)."""
    session = sign_in(base_url, pat_name, pat_secret, site_content_url, note=note)
    try:
        return download_workbook(session, workbook_id, dest_path,
                                 include_extract=include_extract, note=note,
                                 free_space_check=free_space_check)
    finally:
        sign_out(session, note)
