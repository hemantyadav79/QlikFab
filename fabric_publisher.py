"""
Publishes a finished PBIP project into a Microsoft Fabric workspace.

The engine writes a PBIP project on this machine; until now the only way to get
it into Fabric was to download the bundle and import it by hand. This module
does the import over the Fabric Items API, server-side, so the project never
travels out to the browser and back.

A PBIP project is two Fabric items, and the order matters:

  1. The **semantic model** (`*.SemanticModel/`) is created first, because the
     report has to point at something that already exists.
  2. The **report** (`*.Report/`) is created second. Its `definition.pbir`
     references the model by relative path on disk, which means nothing in a
     workspace, so it is rewritten to a `byConnection` reference naming the id
     Fabric just assigned to the model.

Item creation is a long-running operation: Fabric answers 202 with an operation
id and the item appears only once that operation reports success. Nothing here
reports a publish as done before Fabric says it is.

Credentials are never stored. The caller passes the access token it already
holds, it is used for these calls, and it is discarded with the request.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"

# Item creation is usually seconds, but a large model can take longer. Polled
# rather than assumed; this is the point at which we give up waiting.
OPERATION_TIMEOUT_SECONDS = 300
OPERATION_POLL_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 60

# Files that describe the project to Power BI Desktop rather than to Fabric.
# `.platform` would collide with the displayName/type sent alongside it, and the
# root-level report.json is the legacy sibling of definition/report.json.
SKIP_ALWAYS = {".platform"}


def _request(url, token, method="GET", body=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer %s" % token)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    return request


def _describe_error(err):
    """Reports what Fabric actually said. Its error envelope names the cause far
    better than the status code does."""
    try:
        raw = err.read().decode("utf-8", "replace").strip()
    except Exception:                      # noqa: BLE001
        raw = ""
    detail = ""
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None

    if isinstance(body, dict):
        parts = [body.get("errorCode"), body.get("message")]
        more = body.get("moreDetails") or []
        for entry in more:
            if isinstance(entry, dict):
                parts.append(entry.get("message"))
        detail = " — ".join(p for p in parts if p)
    if not detail:
        detail = raw[:400] or "no error details returned"

    hint = ""
    if err.code in (401, 403):
        hint = ("\n\nThe service principal needs Contributor (or higher) on this "
                "workspace, and the tenant setting 'Service principals can use "
                "Fabric APIs' must be on.")
    elif err.code == 409:
        hint = "\n\nAn item with that name already exists in the workspace."
    return "Fabric returned %s: %s%s" % (err.code, detail, hint)


def _collect_parts(item_dir, overrides=None):
    """Every file under an item folder, base64'd, keyed by its path relative to
    that folder. `overrides` replaces a file's bytes without touching disk."""
    overrides = overrides or {}
    parts = []
    for root, _dirs, files in os.walk(item_dir):
        for filename in sorted(files):
            absolute = os.path.join(root, filename)
            relative = os.path.relpath(absolute, item_dir).replace(os.sep, "/")
            if relative in SKIP_ALWAYS or filename in SKIP_ALWAYS:
                continue
            if relative in overrides:
                blob = overrides[relative]
            else:
                with open(absolute, "rb") as handle:
                    blob = handle.read()
            parts.append({
                "path": relative,
                "payload": base64.b64encode(blob).decode("ascii"),
                "payloadType": "InlineBase64",
            })
    return parts


def _await_operation(operation_url, token, note, what):
    """Follows a 202 to its conclusion. Returns the created item's id."""
    deadline = time.time() + OPERATION_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(OPERATION_POLL_SECONDS)
        try:
            with urllib.request.urlopen(
                _request(operation_url, token), timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                state = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise RuntimeError("While waiting for %s: %s" % (what, _describe_error(err)))

        status = (state.get("status") or "").lower()
        if status in ("succeeded", "completed"):
            # The id lives on the operation result, not the status body.
            result_url = operation_url.rstrip("/") + "/result"
            try:
                with urllib.request.urlopen(
                    _request(result_url, token), timeout=REQUEST_TIMEOUT_SECONDS
                ) as response:
                    result = json.loads(response.read().decode("utf-8"))
                return result.get("id") or result.get("objectId")
            except urllib.error.HTTPError:
                # Some operations carry the id inline instead of exposing /result.
                return state.get("resourceId") or (state.get("result") or {}).get("id")
        if status == "failed":
            error = state.get("error") or {}
            raise RuntimeError(
                "Fabric could not create %s: %s"
                % (what, error.get("message") or json.dumps(error)[:300] or "no reason given")
            )
        note("[publish] %s — Fabric reports '%s'…" % (what, status or "running"))

    raise RuntimeError(
        "Fabric did not finish creating %s within %d seconds."
        % (what, OPERATION_TIMEOUT_SECONDS)
    )


def _create_item(workspace_id, token, display_name, item_type, parts, note):
    url = "%s/workspaces/%s/items" % (FABRIC_API_BASE, urllib.parse.quote(str(workspace_id)))
    payload = {
        "displayName": display_name,
        "type": item_type,
        "definition": {"parts": parts},
    }
    note("[publish] Creating %s '%s' (%d file(s))…" % (item_type, display_name, len(parts)))
    try:
        with urllib.request.urlopen(
            _request(url, token, "POST", payload), timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            status = response.status
            location = response.headers.get("Location")
            raw = response.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as err:
        raise RuntimeError("Creating the %s failed. %s" % (item_type, _describe_error(err)))
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach Fabric to create the %s: %s" % (item_type, err.reason))

    if status == 202 and location:
        return _await_operation(location, token, note, "the %s" % item_type)

    body = json.loads(raw) if raw else {}
    item_id = body.get("id")
    if not item_id:
        raise RuntimeError(
            "Fabric accepted the %s but returned no item id, so the publish "
            "cannot be confirmed." % item_type
        )
    return item_id


def _report_binding(semantic_model_id):
    """A published report references its model by connection, not by the
    on-disk relative path a PBIP uses."""
    return {
        "version": "4.0",
        "datasetReference": {
            "byConnection": {
                "connectionString": None,
                "pbiServiceModelId": None,
                "pbiModelVirtualServerName": "sobe_wowvirtualserver",
                "pbiModelDatabaseName": semantic_model_id,
                "name": "EntityDataSource",
                "connectionType": "pbiServiceXmlaStyleLive",
            }
        },
    }


def find_project(out_dir):
    """Locates the .SemanticModel and .Report folders the engine wrote."""
    model_dir = report_dir = None
    for entry in sorted(os.listdir(out_dir)):
        full = os.path.join(out_dir, entry)
        if not os.path.isdir(full):
            continue
        if entry.endswith(".SemanticModel"):
            model_dir = full
        elif entry.endswith(".Report"):
            report_dir = full
    return model_dir, report_dir


def publish(out_dir, workspace_id, token, display_name, note=None):
    """Publishes one PBIP project into a workspace.

    Returns {semanticModelId, reportId, displayName}. Raises RuntimeError with a
    message worth showing to the user on any failure.
    """
    note = note or (lambda _text: None)

    model_dir, report_dir = find_project(out_dir)
    if not model_dir:
        raise RuntimeError(
            "No .SemanticModel folder in %s, so there is nothing to publish." % out_dir
        )

    model_id = _create_item(
        workspace_id, token, display_name, "SemanticModel",
        _collect_parts(model_dir), note,
    )
    note("[publish] Semantic model created (%s)." % model_id)

    result = {"semanticModelId": model_id, "reportId": None, "displayName": display_name}
    if not report_dir:
        note("[publish] No .Report folder — the semantic model was published on its own.")
        return result

    # The report's on-disk reference points at a sibling folder, which does not
    # exist in a workspace. Rebind it to the model Fabric just created.
    binding = json.dumps(_report_binding(model_id), indent=2).encode("utf-8")
    overrides = {"definition.pbir": binding}
    # definition/report.json is the PBIR file Fabric reads; the root-level
    # report.json is its legacy sibling and confuses the import.
    skip_root_report = os.path.exists(os.path.join(report_dir, "definition", "report.json"))
    parts = [
        part for part in _collect_parts(report_dir, overrides)
        if not (skip_root_report and part["path"] == "report.json")
    ]

    report_id = _create_item(workspace_id, token, display_name, "Report", parts, note)
    note("[publish] Report created (%s)." % report_id)
    result["reportId"] = report_id
    return result
