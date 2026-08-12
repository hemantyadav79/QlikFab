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
import re
import sys
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
# `.platform` would collide with the displayName/type sent alongside it.
SKIP_ALWAYS = {".platform"}

# Matched against the path relative to the item folder, never the bare filename.
#
# The root-level report.json is the legacy sibling of definition/report.json: it
# exists so the .pbip opens in Desktop without preview features, and it is built
# by a different code path that binds every visual to a single table alias.
# Publishing it alongside the PBIR definition/ folder is a way to get
# "Something's wrong with one or more fields -- Missing_References" on visuals
# that are perfectly correct in definition/.
#
# Matching on the relative path matters: `report.json` as a bare filename would
# also discard definition/report.json, which is the real report.
SKIP_AT_ITEM_ROOT = {"report.json"}


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
            if relative in SKIP_AT_ITEM_ROOT:
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
    """
    Follows a 202 to its conclusion. Returns the created item's id.

    A failed poll is not a failed operation. The work is running on Fabric's
    side and is unaffected by whether this machine can reach it right now, so a
    dropped connection is retried until the deadline rather than aborting the
    publish. Giving up on the first blip surfaced as a bare
    "<urlopen error [WinError 10060]>" while the semantic model was in fact
    being created, leaving an item in the workspace the run never reported.
    """
    deadline = time.time() + OPERATION_TIMEOUT_SECONDS
    last_reach_error = None
    while time.time() < deadline:
        time.sleep(OPERATION_POLL_SECONDS)
        try:
            with urllib.request.urlopen(
                _request(operation_url, token), timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                state = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise RuntimeError("While waiting for %s: %s" % (what, _describe_error(err)))
        except urllib.error.URLError as err:
            # Includes socket timeouts and refused connections. Reported so a
            # persistent outage is visible in the log rather than looking like
            # a hang, but not fatal until the deadline.
            last_reach_error = err.reason
            note("[publish] Could not reach Fabric while waiting for %s (%s); "
                 "retrying..." % (what, err.reason))
            continue

        last_reach_error = None
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
            except urllib.error.URLError as err:
                # The operation succeeded; only reading back its id failed.
                inline = state.get("resourceId") or (state.get("result") or {}).get("id")
                if inline:
                    return inline
                raise RuntimeError(
                    "Fabric finished %s but the result could not be read back (%s), "
                    "so its id is unknown. The item was created -- check the "
                    "workspace before publishing again, or you will get a "
                    "duplicate." % (what, err.reason))
        if status == "failed":
            error = state.get("error") or {}
            raise RuntimeError(
                "Fabric could not create %s: %s"
                % (what, error.get("message") or json.dumps(error)[:300] or "no reason given")
            )
        note("[publish] %s — Fabric reports '%s'…" % (what, status or "running"))

    if last_reach_error is not None:
        raise RuntimeError(
            "Lost contact with Fabric while waiting for %s and could not get it "
            "back within %d seconds (%s).\n\n"
            "The operation was still running when contact was lost, so it may "
            "well have finished. Check the workspace before publishing again -- "
            "publishing a second time creates a duplicate item rather than "
            "replacing the first."
            % (what, OPERATION_TIMEOUT_SECONDS, last_reach_error)
        )
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


def _preflight(out_dir, note):
    """
    Refuse to upload a project the verifier already knows Fabric will reject.

    Fabric answers a malformed mashup document with a single opaque line --
    "Analysis Services error ... M Engine error ... Token ',' expected" -- that
    names neither the table nor the column, after a round trip that can take a
    minute. The same faults are detectable here in milliseconds, and the
    verifier says exactly where they are.

    Only structural faults block the publish. The verifier's warnings are
    reported but never fatal: they cover the Desktop-only copy of the report,
    which is not uploaded at all.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli"))
    try:
        import verify_pbip
    except Exception as err:                    # noqa: BLE001 - never block on this
        note("[publish] Could not load the verifier (%s); publishing unchecked." % err)
        return

    try:
        fails, _warns = verify_pbip.check(out_dir)
    except Exception as err:                    # noqa: BLE001
        note("[publish] The verifier could not read the project (%s); "
             "publishing unchecked." % err)
        return

    if fails:
        raise RuntimeError(
            "The generated project would be rejected by Fabric, or would publish "
            "and then fail to render. Stopped before uploading, because Fabric's "
            "own error names the symptom once per file and never the cause.\n\n%s"
            % "\n".join("  - %s" % f for f in fails[:15])
        )


def _stage_to_lakehouse(out_dir, workspace_id, token, storage_token, display_name, note):
    """
    Push staged Parquet into a Lakehouse and return the model.bim overrides.

    Runs before the semantic model is created, because a Direct Lake model whose
    Delta tables do not exist yet has nothing to bind to. Returns None when the
    engine did not stage anything, which is the normal embedded-rows path.
    """
    stage_dir = os.path.join(out_dir, "lakehouse")
    manifest_path = os.path.join(stage_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return None

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import onelake_client as onelake

    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    tables = manifest.get("tables") or []
    if not tables:
        return None

    if not storage_token:
        raise RuntimeError(
            "This project stages its data into a Lakehouse, which needs a second "
            "access token issued for the Storage audience (%s). OneLake rejects "
            "the Fabric API token with a bare 401.\n\nRe-run Test Fabric "
            "Connection so the app can obtain both tokens."
            % onelake.STORAGE_SCOPE
        )

    lakehouse_name = re.sub(r"[^A-Za-z0-9_]", "_", display_name).strip("_") or "QlikMigration"
    lakehouse_id, default_schema = onelake.ensure_lakehouse(
        workspace_id, token, lakehouse_name, note)

    total_rows = 0
    for entry in tables:
        local = os.path.join(stage_dir, entry["file"])
        if not os.path.exists(local):
            raise RuntimeError(
                "The manifest lists %s but the file is missing, so the staged data "
                "is incomplete and was not uploaded." % entry["file"])
        relative = "%s/%s" % (onelake.STAGING_DIR, entry["file"])
        onelake.upload_file(workspace_id, lakehouse_id, storage_token, local, relative, note)
        onelake.load_table(workspace_id, lakehouse_id, token, entry["table"], relative, note)
        total_rows += int(entry.get("rows") or 0)

    note("[publish] Staged %s row(s) across %d Delta table(s) in lakehouse '%s'."
         % (f"{total_rows:,}", len(tables), lakehouse_name))

    # The engine could not know these ids -- the lakehouse did not exist when it
    # wrote the model -- so the placeholders are resolved here, in flight.
    return {
        "text": {
            manifest.get("workspacePlaceholder", "{{QLIKFAB_WORKSPACE_ID}}"): str(workspace_id),
            manifest.get("lakehousePlaceholder", "{{QLIKFAB_LAKEHOUSE_ID}}"): str(lakehouse_id),
        },
        "schema": default_schema,
    }


def _apply_schema(model, default_schema, note):
    """
    Point every Direct Lake partition at where its Delta table actually lives.

    A schema-enabled lakehouse stores tables at `Tables/<schema>/<name>`; a
    plain one stores them at `Tables/<name>`. Naming a schema that does not
    exist does not error -- the partition simply resolves to nothing, and every
    measure in the report returns BLANK over a table that visibly has data.
    That is the least debuggable failure of the lot, so the lakehouse itself
    decides.
    """
    changed = 0
    for table in model.get("model", {}).get("tables", []):
        for partition in table.get("partitions", []):
            source = partition.get("source") or {}
            if source.get("type") != "entity":
                continue
            if default_schema:
                if source.get("schemaName") != default_schema:
                    source["schemaName"] = default_schema
                    changed += 1
            elif "schemaName" in source:
                del source["schemaName"]
                changed += 1
    if changed:
        note("[publish] Adjusted %d Direct Lake partition(s) to %s."
             % (changed,
                "schema '%s'" % default_schema if default_schema
                else "the lakehouse's schema-less table layout"))
    return model


def _resolve_placeholders(model_dir, substitutions, note):
    """
    model.bim bound to the lakehouse Fabric just gave us, as bytes for upload.

    Done in flight rather than on disk so the artefact the user downloads keeps
    its placeholders and stays publishable to any workspace.
    """
    path = os.path.join(model_dir, "model.bim")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    for placeholder, value in (substitutions.get("text") or {}).items():
        text = text.replace(placeholder, value)
    leftover = [p for p in (substitutions.get("text") or {}) if p in text]
    if leftover:
        raise RuntimeError(
            "Could not resolve %s in model.bim, so the Direct Lake tables would "
            "point nowhere." % ", ".join(leftover))

    model = json.loads(text)
    _apply_schema(model, substitutions.get("schema"), note)
    return {"model.bim": json.dumps(model, indent=2).encode("utf-8")}


# Written by the engine into a Direct Lake model's source URL, because the
# lakehouse does not exist until publish time. Publishing one unsubstituted is
# the single worst outcome this tool can produce, so the text is matched
# directly rather than inferred from the model's shape.
_PLACEHOLDER = re.compile(r"\{\{QLIKFAB_[A-Z_]+\}\}")


def _refuse_unresolved_placeholders(model_dir, model_overrides):
    """
    Stop a Direct Lake model whose source still names a placeholder.

    _resolve_placeholders raises on leftovers, but it only runs when there was
    something to stage against. When staging is skipped -- no manifest, an empty
    manifest, or a publisher loaded into memory before staging existed -- the
    model is uploaded verbatim and every table points at the literal string
    "{{QLIKFAB_WORKSPACE_ID}}".

    Fabric accepts that model. It creates the semantic model and the report, the
    workspace looks like a successful publish, and then every visual on every
    page renders "Something's wrong with one or more fields" -- because no table
    resolves, so no column or measure it declares exists. There is nothing in
    that error naming the cause, so it is caught here instead.
    """
    blob = model_overrides.get("model.bim")
    if blob is None:
        path = os.path.join(model_dir, "model.bim")
        if not os.path.exists(path):
            return
        with open(path, "rb") as handle:
            blob = handle.read()

    found = sorted(set(_PLACEHOLDER.findall(blob.decode("utf-8", "replace"))))
    if not found:
        return

    raise RuntimeError(
        "This is a Direct Lake model and its tables still point at %s instead of "
        "a real lakehouse, so nothing was published.\n\n"
        "Published as-is it would look like it worked: the semantic model and "
        "report appear in the workspace, then every visual fails with "
        "\"Something's wrong with one or more fields\" because no table resolves.\n\n"
        "The staged data was not picked up. Check that the migration wrote "
        "lakehouse/manifest.json into its output folder, and that the dev server "
        "was restarted after fabric_publisher.py last changed -- a publisher "
        "loaded before Lakehouse staging existed skips it silently."
        % ", ".join(found)
    )


def publish(out_dir, workspace_id, token, display_name, note=None, storage_token=None):
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

    _preflight(out_dir, note)

    substitutions = _stage_to_lakehouse(
        out_dir, workspace_id, token, storage_token, display_name, note
    )
    model_overrides = _resolve_placeholders(model_dir, substitutions, note) if substitutions else {}
    _refuse_unresolved_placeholders(model_dir, model_overrides)

    model_id = _create_item(
        workspace_id, token, display_name, "SemanticModel",
        _collect_parts(model_dir, model_overrides), note,
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
