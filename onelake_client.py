"""
Stages migrated table data into a Fabric Lakehouse over OneLake.

Why this exists
---------------
Embedding rows directly into model.bim proved the data path works, but it does
not scale: a 632,000-row fact table is ~103 MB of Power Query M, ~137 MB once
base64'd into a single Fabric API request body, and Fabric refuses it. A
Lakehouse has no such ceiling, so large tables are written there as Parquet and
loaded into Delta tables the semantic model reads directly.

Two different tokens
--------------------
This is the detail that catches people. The Fabric Items API
(api.fabric.microsoft.com) takes a token for the Fabric audience. OneLake's DFS
endpoint takes one for the *Storage* audience and nothing else -- the docs are
explicit: "OneLake only supports tokens in the Storage audience". Passing the
Fabric token to OneLake returns 401 with no useful explanation, so both are
required and they are kept separate throughout.

Addressing
----------
    https://onelake.dfs.fabric.microsoft.com/<workspaceGuid>/<itemGuid>/<path>

GUIDs are used for both rather than names: names need an item-type suffix
(`.Lakehouse`), break on spaces, and change when someone renames the workspace.

Upload protocol
---------------
ADLS Gen2 three-step, which OneLake implements:

    PUT   ...<path>?resource=file            create (truncates any existing)
    PATCH ...<path>?action=append&position=N stage bytes at an offset
    PATCH ...<path>?action=flush&position=T  commit, T = total bytes appended

Append only stages; without the final flush the file exists at zero length.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

FABRIC_API = "https://api.fabric.microsoft.com/v1"
ONELAKE_DFS = "https://onelake.dfs.fabric.microsoft.com"

# The OneLake scope a caller must have requested for `storage_token`.
STORAGE_SCOPE = "https://storage.azure.com/.default"

# Appended in chunks so a large table does not have to be held in one request.
# 8 MiB is comfortably inside the service limit and keeps the request count low.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

REQUEST_TIMEOUT_SECONDS = 300
OPERATION_TIMEOUT_SECONDS = 600
OPERATION_POLL_SECONDS = 3

# Fabric's own rule for a Delta table name: letters, digits and underscores,
# and not all digits. Checked before the call so the failure names the table
# rather than arriving as a generic 400.
TABLE_NAME_PATTERN = re.compile(r"^(?=[0-9]*[a-zA-Z_])[a-zA-Z0-9_]{1,256}$")

# Where staged files land inside the lakehouse.
STAGING_DIR = "Files/qlik_migration"


class OneLakeError(RuntimeError):
    """Raised with a message intended to be shown to the user verbatim."""


class OneLakeUnreachable(OneLakeError):
    """
    The request never got an answer -- timeout, refused, DNS.

    Separated from OneLakeError because it says nothing about the operation:
    the work may be running on Fabric's side regardless. Callers that are
    polling can retry it, while a real HTTP failure still stops immediately.
    """


def _request(url, token, method="GET", body=None, content_type="application/json",
             extra_headers=None):
    data = body
    if body is not None and content_type == "application/json":
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer %s" % token)
    if data is not None:
        request.add_header("Content-Type", content_type)
        request.add_header("Content-Length", str(len(data)))
    for key, value in (extra_headers or {}).items():
        request.add_header(key, value)
    return request


def _describe(err, what):
    try:
        raw = err.read().decode("utf-8", "replace").strip()
    except Exception:                       # noqa: BLE001
        raw = ""
    detail = raw[:400]
    try:
        body = json.loads(raw) if raw else None
        if isinstance(body, dict):
            parts = [body.get("errorCode"), body.get("message")]
            detail = " - ".join(p for p in parts if p) or detail
    except ValueError:
        pass

    hint = ""
    if err.code == 401:
        hint = ("\n\nOneLake accepts only tokens issued for the Storage audience "
                "(%s). A token for the Fabric API audience returns exactly this "
                "401." % STORAGE_SCOPE)
    elif err.code == 403:
        hint = ("\n\nThe service principal needs Contributor on the workspace, and "
                "the tenant setting 'Service principals can use Fabric APIs' must "
                "be on.")
    return "%s failed: %s %s%s" % (what, err.code, detail or err.reason, hint)


def _send(request, what):
    """(status, body). Use _send_full when the Location header matters."""
    status, body, _headers = _send_full(request, what)
    return status, body


def _send_full(request, what):
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as err:
        raise OneLakeError(_describe(err, what))
    except urllib.error.URLError as err:
        raise OneLakeUnreachable("Could not reach %s: %s" % (what, err.reason))


# ----------------------------------------------------------------------
# Lakehouse
# ----------------------------------------------------------------------

def _default_schema(item):
    """
    The lakehouse's default schema, or None when it has no schemas.

    This is the difference between Delta tables living at `Tables/<name>` and
    at `Tables/<schema>/<name>`, which decides whether a Direct Lake partition
    must carry a schemaName. Fabric only returns `defaultSchema` for a
    schema-enabled lakehouse, so its absence is the answer, not missing data.
    """
    return ((item or {}).get("properties") or {}).get("defaultSchema") or None


def ensure_lakehouse(workspace_id, fabric_token, display_name, note):
    """
    (id, default_schema) for a lakehouse named `display_name`, creating it if
    absent. `default_schema` is None when the lakehouse is not schema-enabled.

    Reused rather than recreated on every publish: a fresh lakehouse per run
    would litter the workspace and orphan the previous run's tables.
    """
    url = "%s/workspaces/%s/lakehouses" % (FABRIC_API, urllib.parse.quote(str(workspace_id)))
    _status, raw = _send(_request(url, fabric_token), "Listing lakehouses")
    try:
        existing = json.loads(raw or b"{}").get("value") or []
    except ValueError:
        existing = []

    for item in existing:
        if str(item.get("displayName", "")).lower() == display_name.lower():
            schema = _default_schema(item)
            note("[onelake] Reusing lakehouse '%s' (%s, %s)."
                 % (display_name, item.get("id"),
                    "schema '%s'" % schema if schema else "no schemas"))
            return item.get("id"), schema

    note("[onelake] Creating lakehouse '%s'..." % display_name)
    status, raw, headers = _send_full(
        _request(url, fabric_token, "POST", {"displayName": display_name}),
        "Creating the lakehouse",
    )

    # Documented to answer either way: 201 with the lakehouse inline, or 202
    # with a Location header while provisioning continues. Treating 202 as a
    # failure ("returned no id") would break on exactly the slower tenants
    # where provisioning actually takes a moment.
    if status == 202:
        location = headers.get("Location")
        if not location:
            raise OneLakeError(
                "Fabric accepted the lakehouse creation with 202 but gave no "
                "Location header, so the operation cannot be followed."
            )
        note("[onelake] Lakehouse provisioning accepted; waiting...")
        lakehouse_id = _await_operation(location, fabric_token, note,
                                        "creating the lakehouse")
        schema = None
        # Read it back either way: the operation result carries the id but not
        # the properties, and the schema decides where the Delta tables land.
        _s, listed = _send(_request(url, fabric_token), "Listing lakehouses")
        try:
            for item in (json.loads(listed or b"{}").get("value") or []):
                if str(item.get("displayName", "")).lower() == display_name.lower():
                    lakehouse_id = lakehouse_id or item.get("id")
                    schema = _default_schema(item)
                    break
        except ValueError:
            pass
    else:
        try:
            created = json.loads(raw or b"{}")
        except ValueError:
            created = {}
        lakehouse_id = created.get("id")
        schema = _default_schema(created)

    if not lakehouse_id:
        raise OneLakeError(
            "Fabric accepted the lakehouse creation but returned no id, so its "
            "files cannot be addressed."
        )
    note("[onelake] Lakehouse created (%s, %s)."
         % (lakehouse_id, "schema '%s'" % schema if schema else "no schemas"))
    return lakehouse_id, schema


# ----------------------------------------------------------------------
# File upload
# ----------------------------------------------------------------------

def _file_url(workspace_id, lakehouse_id, relative_path):
    return "%s/%s/%s/%s" % (
        ONELAKE_DFS,
        urllib.parse.quote(str(workspace_id)),
        urllib.parse.quote(str(lakehouse_id)),
        urllib.parse.quote(relative_path.lstrip("/")),
    )


def upload_file(workspace_id, lakehouse_id, storage_token, local_path,
                relative_path, note=None):
    """
    Upload one local file to `relative_path` inside the lakehouse.

    Returns the byte count written. Raises rather than half-writing: a file
    created and appended to but never flushed reads back as empty, which would
    look like a successful upload of an empty table.
    """
    note = note or (lambda _text: None)
    url = _file_url(workspace_id, lakehouse_id, relative_path)
    total = os.path.getsize(local_path)

    # `resource=file` creates and truncates, so a re-publish replaces cleanly.
    _send(_request(url + "?resource=file", storage_token, "PUT", b"",
                   content_type="application/octet-stream"),
          "Creating %s in OneLake" % relative_path)

    written = 0
    with open(local_path, "rb") as handle:
        while True:
            chunk = handle.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            _send(
                _request("%s?action=append&position=%d" % (url, written),
                         storage_token, "PATCH", chunk,
                         content_type="application/octet-stream"),
                "Appending to %s (offset %d)" % (relative_path, written),
            )
            written += len(chunk)
            if total > UPLOAD_CHUNK_BYTES:
                note("[onelake] %s: %.1f/%.1f MB"
                     % (relative_path, written / 1048576.0, total / 1048576.0))

    # Without this the file exists at length zero.
    _send(_request("%s?action=flush&position=%d" % (url, written),
                   storage_token, "PATCH"),
          "Committing %s" % relative_path)

    if written != total:
        raise OneLakeError(
            "Uploaded %d of %d bytes of %s; the upload is incomplete and was not "
            "used." % (written, total, relative_path)
        )
    return written


# ----------------------------------------------------------------------
# Files -> Delta table
# ----------------------------------------------------------------------

def load_table(workspace_id, lakehouse_id, fabric_token, table_name,
               relative_path, note, file_format="Parquet"):
    """
    Convert a staged file into a Delta table the semantic model can read.

    A raw file in Files/ is not queryable as a table; the Load Table operation
    is what registers it under Tables/. It is long-running, so the operation is
    followed to completion rather than assumed.
    """
    if not TABLE_NAME_PATTERN.match(table_name or ""):
        raise OneLakeError(
            "Fabric will not accept %r as a Delta table name. It allows letters, "
            "digits and underscores only, and the name cannot be all digits."
            % table_name
        )

    url = "%s/workspaces/%s/lakehouses/%s/tables/%s/load" % (
        FABRIC_API,
        urllib.parse.quote(str(workspace_id)),
        urllib.parse.quote(str(lakehouse_id)),
        urllib.parse.quote(table_name),
    )
    # `header` belongs to the Csv format options only -- the Parquet variant
    # takes nothing but `format`, and sending a foreign property to a typed
    # union is how a request gets rejected for no obvious reason.
    format_options = {"format": file_format}
    if file_format == "Csv":
        format_options.update({"header": True, "delimiter": ","})

    payload = {
        "relativePath": relative_path,
        "pathType": "File",
        "mode": "Overwrite",
        "recursive": False,
        "formatOptions": format_options,
    }
    note("[onelake] Loading %s into a Delta table..." % table_name)

    try:
        request = _request(url, fabric_token, "POST", payload)
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            location = response.headers.get("Location")
            response.read()
    except urllib.error.HTTPError as err:
        raise OneLakeError(_describe(err, "Loading table %s" % table_name))
    except urllib.error.URLError as err:
        raise OneLakeError("Could not reach Fabric to load %s: %s" % (table_name, err.reason))

    if status == 202 and location:
        _await_operation(location, fabric_token, note, "loading table %s" % table_name)
    note("[onelake] %s is now a Delta table." % table_name)


def _await_operation(operation_url, token, note, what):
    """
    Follows a 202 to completion. Returns the created item's id when the
    operation exposes one, or None for operations that create nothing.
    """
    deadline = time.time() + OPERATION_TIMEOUT_SECONDS
    last_reach_error = None
    while time.time() < deadline:
        time.sleep(OPERATION_POLL_SECONDS)
        try:
            _status, raw = _send(_request(operation_url, token), "Polling %s" % what)
        except OneLakeUnreachable as err:
            # Loading a large Delta table takes minutes; one unanswered poll in
            # the middle of that says nothing about the load itself.
            last_reach_error = err
            note("[onelake] %s - lost contact (%s); retrying..." % (what, err))
            continue
        last_reach_error = None
        try:
            state = json.loads(raw or b"{}")
        except ValueError:
            continue
        status = str(state.get("status", "")).lower()
        if status in ("succeeded", "completed"):
            # The id lives on the operation's result, not its status body.
            try:
                _s, result_raw = _send(
                    _request(operation_url.rstrip("/") + "/result", token),
                    "Reading the result of %s" % what)
                result = json.loads(result_raw or b"{}")
                return result.get("id") or result.get("objectId")
            except (OneLakeError, ValueError):
                # Plenty of operations have no /result; that is not a failure.
                return state.get("resourceId") or (state.get("result") or {}).get("id")
        if status == "failed":
            error = state.get("error") or {}
            raise OneLakeError(
                "Fabric could not finish %s: %s"
                % (what, error.get("message") or json.dumps(error)[:300] or "no reason given")
            )
        note("[onelake] %s - %s..." % (what, status or "running"))
    if last_reach_error is not None:
        raise OneLakeError(
            "Lost contact with Fabric while waiting for %s and could not get it "
            "back within %d seconds.\n\n%s\n\nThe operation was still running "
            "when contact was lost, so the table may have loaded anyway -- check "
            "the lakehouse before re-running."
            % (what, OPERATION_TIMEOUT_SECONDS, last_reach_error))
    raise OneLakeError("Fabric did not finish %s within %d seconds."
                       % (what, OPERATION_TIMEOUT_SECONDS))
