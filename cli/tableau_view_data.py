"""
Reads rows from Tableau itself, for workbooks whose data is nowhere local.

Why this exists
---------------
A workbook on a live connection packages no rows, and the database behind it
may not be reachable from this machine -- or its credential may not be to hand.
Tableau, though, already holds that credential and will run the query itself:
`GET /views/{id}/data` returns what a worksheet shows, as CSV, authorised by
the same Personal Access Token the workbook was downloaded with.

What this is and is not
-----------------------
It is the *worksheet's* data: only the fields placed on that sheet, aggregated
the way the sheet aggregates them, with the sheet's filters applied. It is not
the underlying table, and Tableau publishes no endpoint that returns one for a
live connection. So this is the last resort, tried only after a direct read of
the source has been ruled out, and every table it fills is recorded as
view-level in the audit report rather than passed off as the table itself.
"""
import csv
import io
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NS = {"t": "http://tableau.com/api"}

FALLBACK_API_VERSION = "3.4"
TIMEOUT_SECONDS = 120

# Tableau wraps a field in its aggregation when it writes a header: a column
# placed as a sum arrives as 'SUM(Annual Income)'. The inner name is what the
# model knows the field by, so it is what the match below has to see.
_AGGREGATION = re.compile(
    r"^(?:SUM|AVG|MIN|MAX|CNT|CNTD|COUNT|COUNTD|ATTR|AGG|MEDIAN|STDEV|STDEVP|"
    r"VAR|VARP|YEAR|QUARTER|MONTH|WEEK|DAY|HOUR|MINUTE|SECOND)\((.+)\)$",
    re.IGNORECASE,
)


class TableauViewDataError(RuntimeError):
    """Raised with a message intended to be shown to the user verbatim."""


def field_name_of(header: str) -> str:
    """'SUM(Annual Income)' -> 'Annual Income'; anything else unchanged."""
    text = str(header or "").strip()
    match = _AGGREGATION.match(text)
    return match.group(1).strip() if match else text


def _request(url, token=None, method="GET", body=None, accept="application/xml"):
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", accept)
    if body is not None:
        request.add_header("Content-Type", "application/xml")
    if token:
        request.add_header("X-Tableau-Auth", token)
    return request


def _open(request, what):
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            root = ET.fromstring(err.read())
            element = root.find(".//t:error", NS)
            if element is not None:
                detail = " — %s: %s" % (
                    (element.findtext("t:summary", "", NS) or "").strip(),
                    (element.findtext("t:detail", "", NS) or "").strip())
        except Exception:               # noqa: BLE001 - the status still stands
            pass
        raise TableauViewDataError("%s failed: HTTP %s%s" % (what, err.code, detail))
    except urllib.error.URLError as err:
        raise TableauViewDataError("%s failed: %s" % (what, err.reason))


class _Session:
    def __init__(self, base_url, version, token, site_id):
        self.base_url = base_url
        self.version = version
        self.token = token
        self.site_id = site_id

    @property
    def site_url(self):
        return "%s/api/%s/sites/%s" % (self.base_url, self.version, self.site_id)


def _sign_in(base_url, pat_name, pat_secret, site):
    version = FALLBACK_API_VERSION
    try:
        with _open(_request("%s/api/%s/serverinfo" % (base_url, FALLBACK_API_VERSION)),
                   "Reading server info") as response:
            found = (ET.fromstring(response.read())
                     .findtext(".//t:restApiVersion", "", NS) or "").strip()
            version = found or version
    except (TableauViewDataError, ET.ParseError):
        pass                            # the fallback version answers too

    body = (
        '<tsRequest><credentials personalAccessTokenName="%s" '
        'personalAccessTokenSecret="%s"><site contentUrl="%s" /></credentials>'
        "</tsRequest>" % (_xml_escape(pat_name), _xml_escape(pat_secret),
                          _xml_escape(site or ""))
    ).encode("utf-8")

    with _open(_request("%s/api/%s/auth/signin" % (base_url, version),
                        method="POST", body=body), "Signing in to Tableau") as response:
        root = ET.fromstring(response.read())
    credentials = root.find(".//t:credentials", NS)
    site_element = root.find(".//t:site", NS)
    if credentials is None or site_element is None:
        raise TableauViewDataError("Tableau accepted the sign-in but returned no session.")
    return _Session(base_url, version, credentials.get("token"), site_element.get("id"))


def _sign_out(session):
    try:
        _open(_request("%s/api/%s/auth/signout" % (session.base_url, session.version),
                       session.token, method="POST", body=b""), "Signing out").close()
    except TableauViewDataError:
        pass                            # the token expires on its own


def _xml_escape(value):
    return (str(value or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _views_of(session, workbook_id):
    url = "%s/workbooks/%s/views" % (session.site_url,
                                     urllib.parse.quote(str(workbook_id)))
    with _open(_request(url, session.token), "Listing the workbook's views") as response:
        root = ET.fromstring(response.read())
    views = []
    for element in root.findall(".//t:view", NS):
        if element.get("id"):
            views.append({"id": element.get("id"),
                          "name": element.get("name") or element.get("id")})
    return views


def _view_data(session, view_id):
    """One view's summary data as (columns, rows)."""
    # maxAge=0 asks Tableau not to serve a stale cached result. The query runs
    # against the live connection, so a cached answer could predate the very
    # data the migration is being run to capture.
    url = "%s/views/%s/data?maxAge=0" % (session.site_url,
                                         urllib.parse.quote(str(view_id)))
    with _open(_request(url, session.token, accept="text/csv"),
               "Reading the view's data") as response:
        payload = response.read()

    text = payload.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], []
    columns = [field_name_of(cell) for cell in header]
    rows = [[None if cell == "" else cell for cell in record]
            for record in reader if record]
    return columns, rows


def read_view_tables(server_url, site, pat_name, pat_secret, workbook_id, tables,
                     max_rows=None, note=None):
    """
    Fill what tables can be filled from the workbook's own worksheets.

    Returns (data, problems, notes). `data` and `problems` follow the same
    contract as the other readers; `notes` states, per table, that the rows are
    a worksheet's data rather than the table's, which the audit report repeats
    verbatim. Nothing here is silent: a table filled this way is never
    presented as a complete read of its source.
    """
    note = note or (lambda _text: None)
    data, problems, notes = {}, {}, []

    wanted = [table for table in tables if table.get("name")]
    if not wanted:
        return data, problems, notes

    session = _sign_in(server_url, pat_name, pat_secret, site)
    try:
        views = _views_of(session, workbook_id)
        if not views:
            return data, {t["name"]: "The workbook publishes no views to read data from."
                          for t in wanted}, notes

        note("[tableau] Reading data from %d worksheet(s) as a fallback…" % len(views))
        harvested = []
        for view in views:
            try:
                columns, rows = _view_data(session, view["id"])
            except TableauViewDataError as err:
                note("[tableau] %s: %s" % (view["name"], err))
                continue
            if columns and rows:
                harvested.append((view["name"], columns, rows))

        if not harvested:
            return data, {t["name"]: ("Tableau returned no data for this workbook's "
                                      "views, so there was nothing to read.")
                          for t in wanted}, notes

        for table in wanted:
            name = table["name"]
            known = {str(f.get("name", "")).lower()
                     for f in (table.get("fields") or []) if f.get("name")}

            # The best view for a table is the one that carries most of its
            # fields. A single-table model with a single worksheet matches on
            # the first test; a federated one needs the scoring.
            best, best_score = None, 0
            for view_name, columns, rows in harvested:
                score = sum(1 for column in columns if column.lower() in known)
                if score > best_score:
                    best, best_score = (view_name, columns, rows), score
            if best is None:
                if len(harvested) == 1 and len(wanted) == 1:
                    best = harvested[0]         # one table, one sheet, no ambiguity
                else:
                    problems[name] = ("No worksheet in this workbook carries this "
                                      "table's fields, so none of its rows could "
                                      "be read.")
                    continue

            view_name, columns, rows = best
            if max_rows is not None and len(rows) > max_rows:
                rows = rows[:max_rows]
                truncated = True
            else:
                truncated = False

            data[name] = {"columns": list(columns), "rows": rows,
                          "truncated": truncated}
            missing = sorted(field for field in known
                             if field not in {c.lower() for c in columns})
            notes.append(
                "%s: rows came from the worksheet %r, not from the source table. "
                "They are that sheet's data — its fields, at its aggregation, with "
                "its filters applied.%s"
                % (name, view_name,
                   (" %d field(s) the model declares are not on that sheet and so "
                    "carry no data: %s." % (len(missing), ", ".join(missing[:12])))
                   if missing else ""))
            note("[tableau] %s: %s row(s), %d column(s) from worksheet %r"
                 % (name, f"{len(rows):,}", len(columns), view_name))
    finally:
        _sign_out(session)

    return data, problems, notes
