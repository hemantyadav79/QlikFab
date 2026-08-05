"""
Runs the real migration engine as a supervised subprocess and exposes its
progress to the UI.

The web UI used to animate a four-agent "execution" on timers while generating
its own output in the browser. This module replaces that with the actual
pipeline in cli/: `ai_qvf_to_powerbi.py` extracts the .qvf binary, builds the
semantic model and report, and packages the PBIP bundle. Everything the UI shows
is a line this process really printed.

Phase boundaries are derived from markers the engine actually emits (see
PHASE_MARKERS). A phase is only reported as reached once its marker appears, so
a run that never gets that far never claims to have. Nothing here infers,
smooths over, or fills in a phase the engine did not report.

Credentials never reach this module: it operates on an uploaded .qvf only.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import tempfile
import threading
import time
import uuid

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CLI_DIR = os.path.join(PROJECT_ROOT, "cli")
ENGINE_SCRIPT = os.path.join(CLI_DIR, "ai_qvf_to_powerbi.py")

# A .qvf is a binary Qlik app; the largest bundled sample is ~2 MB, so this is
# generous while still refusing anything absurd.
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
# The engine is CPU-bound parsing, not network-bound. A run that has not
# finished by this point is wedged and is killed rather than left behind.
RUN_TIMEOUT_SECONDS = 900

# The four phases the engine genuinely performs, in the order it performs them.
# `key` matches the ids the UI already uses for its four panes.
PHASES = [
    {"key": "extract", "label": "Assessment", "detail": "Reads the .qvf binary: load script, schema and sheet inventory"},
    {"key": "model", "label": "Parsing", "detail": "Builds model.bim — tables, columns, DAX measures"},
    {"key": "report", "label": "Mapping", "detail": "Lays out report pages and visuals"},
    {"key": "package", "label": "Report Generation", "detail": "Writes .pbit, the audit report and the PBIP bundle"},
]
PHASE_ORDER = [p["key"] for p in PHASES]

# Ordered; the first pattern a line matches moves the run into that phase. Lines
# matching nothing belong to whichever phase is currently open, and a phase is
# never re-entered once passed — the engine runs strictly forward.
PHASE_MARKERS = [
    ("extract", re.compile(r"Extracting metadata|QVF|extraction", re.I)),
    ("model", re.compile(r"PBIP directory tree|model\.bim|definition\.pbism|AI Brain Provider", re.I)),
    ("report", re.compile(r"page\.json|visual\.json|Generated Report Page|definition\.pbir", re.I)),
    ("package", re.compile(r"\.pbit|MIGRATION_AUDIT_REPORT|_PBIP\.zip|PROJECT READY", re.I)),
]


def classify(line):
    """The phase a log line belongs to, or None when it names no phase."""
    for key, pattern in PHASE_MARKERS:
        if pattern.search(line):
            return key
    return None


def safe_stem(name):
    """A filesystem-safe stem for an uploaded name, never empty, never a path."""
    base = os.path.basename(name or "")
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "_", base).strip() or "upload"
    if not stem.lower().endswith(".qvf"):
        stem += ".qvf"
    return stem


class MigrationRun:
    """One engine invocation over one .qvf, with its real output captured."""

    def __init__(self, filename, payload=None):
        self.id = uuid.uuid4().hex[:12]
        self.filename = safe_stem(filename)
        self.created_at = time.time()
        self.status = "queued"          # queued | running | completed | failed
        self.exit_code = None
        self.error = None
        self.lines = []                 # [{seq, phase, text}]
        self.phase = None
        self.reached = []               # phases whose marker actually appeared
        self.artifact_path = None
        self.summary = None             # counted from generated files, never estimated
        self._lock = threading.Lock()

        self.work_dir = tempfile.mkdtemp(prefix="qlikmig_%s_" % self.id)
        self.input_path = os.path.join(self.work_dir, self.filename)
        self.output_dir = os.path.join(self.work_dir, "out")
        # A Qlik Cloud run has no bytes yet; they are exported from the tenant
        # before the engine starts.
        if payload is not None:
            with open(self.input_path, "wb") as handle:
                handle.write(payload)

    # ---------- reporting ----------

    def _append(self, text):
        text = text.rstrip("\r\n")
        if not text.strip():
            return
        with self._lock:
            found = classify(text)
            # Only ever move forward, so a stray late mention of an earlier stage
            # cannot make the UI appear to run backwards.
            if found and (self.phase is None or PHASE_ORDER.index(found) >= PHASE_ORDER.index(self.phase)):
                self.phase = found
                if found not in self.reached:
                    self.reached.append(found)
            # Stamped as the line is read, not as the browser collects it. Polling
            # returns lines in batches, so a stamp applied on arrival would show a
            # whole batch as simultaneous when the engine emitted them seconds apart.
            self.lines.append({
                "seq": len(self.lines),
                "phase": self.phase,
                "text": text,
                "t": round(time.time() - self.created_at, 1),
            })

    def note(self, text):
        """Records a line this supervisor produced, marked so it is not mistaken
        for engine output."""
        with self._lock:
            self.lines.append({
                "seq": len(self.lines),
                "phase": self.phase,
                "text": text,
                "source": "runner",
                "t": round(time.time() - self.created_at, 1),
            })

    def snapshot(self, since=0):
        with self._lock:
            return {
                "id": self.id,
                "filename": self.filename,
                "status": self.status,
                "exitCode": self.exit_code,
                "error": self.error,
                "phase": self.phase,
                "reached": list(self.reached),
                "phases": PHASES,
                "lines": self.lines[since:],
                "totalLines": len(self.lines),
                "artifact": os.path.basename(self.artifact_path) if self.artifact_path else None,
                "summary": self.summary,
                "elapsed": round(time.time() - self.created_at, 2),
            }

    # ---------- execution ----------

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def start_from_qlik(self, tenant, app_id, authorization):
        """Exports the app from the tenant, then runs the engine on it.

        The download stays on this machine: the browser asks for a run and polls
        it, but the .qvf itself goes tenant -> here -> engine. Routing tens of
        megabytes back out to the page and in again was the transfer that kept
        dropping.
        """
        threading.Thread(
            target=self._export_then_run,
            args=(tenant, app_id, authorization),
            daemon=True,
        ).start()

    def _export_then_run(self, tenant, app_id, authorization):
        self.status = "exporting"
        try:
            size = download_qlik_app(tenant, app_id, authorization, self.input_path, self.note)
        except Exception as err:      # noqa: BLE001 - reported to the UI verbatim
            self.status = "failed"
            self.error = str(err)
            self.note("[export] %s" % err)
            return
        self.note("[export] Wrote %.2f MB to %s" % (size / 1048576.0, self.filename))
        self._run()

    def _run(self):
        if not os.path.exists(ENGINE_SCRIPT):
            self.status = "failed"
            self.error = "Engine not found at %s" % ENGINE_SCRIPT
            self.note("[runner] %s" % self.error)
            return

        self.status = "running"
        command = [
            sys.executable,
            "-u",                       # unbuffered, so the UI sees output as it happens
            ENGINE_SCRIPT,
            "--qvf", self.input_path,
            "--output", self.output_dir,
        ]
        self.note("[runner] %s" % " ".join(os.path.basename(c) if c.endswith(".py") else c for c in command[1:]))

        # The engine prints em-dashes and box characters. Without this the child
        # encodes them in the Windows console codepage while we decode UTF-8, and
        # the UI shows replacement characters instead.
        child_env = dict(os.environ, PYTHONIOENCODING="utf-8")

        try:
            process = subprocess.Popen(
                command,
                cwd=CLI_DIR,            # the engine imports its siblings by bare name
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as err:        # noqa: BLE001 - surfaced to the UI, not swallowed
            self.status = "failed"
            self.error = "Could not start the engine: %s" % err
            self.note("[runner] %s" % self.error)
            return

        watchdog = threading.Timer(RUN_TIMEOUT_SECONDS, process.kill)
        watchdog.start()
        try:
            for line in process.stdout:
                self._append(line)
            process.wait()
        finally:
            watchdog.cancel()

        self.exit_code = process.returncode
        if process.returncode == 0:
            self.artifact_path = self._find_artifact()
            if self.artifact_path:
                self.summary = self.build_summary()
                self.status = "completed"
            else:
                # Exit 0 with nothing to download is not a success worth claiming.
                self.status = "failed"
                self.error = "The engine exited cleanly but produced no PBIP bundle in %s." % self.output_dir
                self.note("[runner] %s" % self.error)
        else:
            self.status = "failed"
            self.error = "The engine exited with code %s." % process.returncode
            self.note("[runner] %s" % self.error)

    def _find_artifact(self):
        """The PBIP zip the engine wrote, or None. Never fabricated."""
        for root, _dirs, files in os.walk(self.output_dir):
            for name in files:
                if name.lower().endswith(".zip"):
                    return os.path.join(root, name)
        return None

    def _find_file(self, suffix):
        for root, _dirs, files in os.walk(self.output_dir):
            for name in files:
                if name.lower().endswith(suffix):
                    return os.path.join(root, name)
        return None

    def build_summary(self):
        """Reads the semantic model and report the engine actually wrote.

        Every number here is counted from a generated file. Anything that cannot
        be read stays None so the UI can say "not reported" rather than show a
        figure nobody produced — the browser build used to estimate the column
        count from the .qvf's byte size, which is exactly what this replaces.
        """
        summary = {
            "appName": None,
            "tables": [],
            "columnCount": None,
            "measureCount": None,
            "pages": [],
            "visualCount": None,
            "auditReport": None,
        }

        model_path = self._find_file("model.bim")
        if model_path:
            try:
                with open(model_path, "r", encoding="utf-8-sig") as handle:
                    model = json.load(handle)
                tables = model.get("model", {}).get("tables", []) or []
                columns = 0
                measures = 0
                for table in tables:
                    cols = [c.get("name") for c in (table.get("columns") or []) if c.get("name")]
                    meas = [
                        {"name": m.get("name"), "expression": _flatten(m.get("expression"))}
                        for m in (table.get("measures") or []) if m.get("name")
                    ]
                    columns += len(cols)
                    measures += len(meas)
                    summary["tables"].append({"name": table.get("name"), "columns": cols, "measures": meas})
                summary["columnCount"] = columns
                summary["measureCount"] = measures
            except Exception as err:      # noqa: BLE001
                self.note("[runner] Could not read the generated model.bim: %s" % err)

        # Report pages, counted from the definition the engine emitted.
        pages_root = None
        for root, dirs, _files in os.walk(self.output_dir):
            if os.path.basename(root).lower() == "pages" and dirs:
                pages_root = root
                break
        if pages_root:
            total_visuals = 0
            for page_dir in sorted(os.listdir(pages_root)):
                page_json = os.path.join(pages_root, page_dir, "page.json")
                if not os.path.isfile(page_json):
                    continue
                name = page_dir
                try:
                    with open(page_json, "r", encoding="utf-8-sig") as handle:
                        name = json.load(handle).get("displayName") or page_dir
                except Exception:         # noqa: BLE001 - fall back to the folder name
                    pass
                visuals_dir = os.path.join(pages_root, page_dir, "visuals")
                count = len(os.listdir(visuals_dir)) if os.path.isdir(visuals_dir) else 0
                total_visuals += count
                summary["pages"].append({"name": name, "visualCount": count})
            summary["visualCount"] = total_visuals

        pbip = self._find_file(".pbip")
        if pbip:
            summary["appName"] = os.path.splitext(os.path.basename(pbip))[0]

        audit = self._find_file("migration_audit_report.md")
        if audit:
            try:
                with open(audit, "r", encoding="utf-8") as handle:
                    summary["auditReport"] = handle.read()
            except Exception:             # noqa: BLE001
                pass

        return summary


# Qlik Cloud export is two calls: one to materialise the app, one to fetch it.
QLIK_HOST_SUFFIXES = (".qlikcloud.com", ".qlik.com")
EXPORT_TIMEOUT_SECONDS = 600


def _qlik_request(url, authorization, method="GET"):
    request = urllib.request.Request(url, method=method)
    request.add_header("Authorization", authorization)
    request.add_header("Accept", "application/json")
    return request


def _describe_http_error(err, what):
    body = ""
    try:
        body = err.read().decode("utf-8", "replace").strip()[:400]
    except Exception:                 # noqa: BLE001
        pass
    detail = "\n\nTenant said:\n%s" % body if body else "\n\nThe tenant returned no error details (empty body)."
    if err.code == 403:
        detail += ("\n\nA 403 on an export usually means the app lives in a managed space, "
                   "where Qlik does not allow direct export.")
    return "%s failed: %s %s%s" % (what, err.code, err.reason, detail)


def download_qlik_app(tenant, app_id, authorization, dest_path, note):
    """Exports one Qlik Cloud app to dest_path and returns the byte count.

    Streamed to disk rather than buffered: these apps run to tens of megabytes,
    and holding one in memory only to hand it straight to a subprocess is waste
    that also made the transfer fragile.
    """
    base = str(tenant or "").rstrip("/")
    host = urllib.parse.urlparse(base).hostname or ""
    if not host.endswith(QLIK_HOST_SUFFIXES):
        raise ValueError("Refusing to export from %r — only Qlik Cloud tenants are allowed." % (host or tenant))

    export_url = "%s/api/v1/apps/%s/export" % (base, urllib.parse.quote(str(app_id)))
    note("[export] Asking the tenant to materialise the app…")
    try:
        with urllib.request.urlopen(
            _qlik_request(export_url, authorization, "POST"), timeout=EXPORT_TIMEOUT_SECONDS
        ) as response:
            location = response.headers.get("Location")
            response.read()
    except urllib.error.HTTPError as err:
        raise RuntimeError(_describe_http_error(err, "The export request"))
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach the tenant to export the app: %s" % err.reason)

    if not location:
        raise RuntimeError(
            "The tenant accepted the export but returned no Location header, "
            "so there is no file to download."
        )

    download_url = location if location.lower().startswith("http") else base + location
    note("[export] Downloading the .qvf from the tenant…")

    written = 0
    try:
        with urllib.request.urlopen(
            _qlik_request(download_url, authorization), timeout=EXPORT_TIMEOUT_SECONDS
        ) as response, open(dest_path, "wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
    except urllib.error.HTTPError as err:
        raise RuntimeError(_describe_http_error(err, "Downloading the exported app"))
    except urllib.error.URLError as err:
        raise RuntimeError("The export download did not complete: %s" % err.reason)

    if not written:
        raise RuntimeError("The tenant returned an empty export, so there was nothing to migrate.")
    return written


def _flatten(expression):
    """model.bim stores a DAX expression as a string or a list of lines."""
    if isinstance(expression, list):
        return "\n".join(str(part) for part in expression)
    return expression if expression is None else str(expression)

    def cleanup(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)


class RunStore:
    """Keeps recent runs addressable while the server is up. Deliberately
    in-memory: these are dev-server runs, not a durable job history."""

    def __init__(self, keep=24):
        self._runs = {}
        self._order = []
        self._keep = keep
        self._lock = threading.Lock()

    def create(self, filename, payload=None):
        run = MigrationRun(filename, payload)
        with self._lock:
            self._runs[run.id] = run
            self._order.append(run.id)
            while len(self._order) > self._keep:
                stale = self._runs.pop(self._order.pop(0), None)
                if stale:
                    stale.cleanup()
        return run

    def get(self, run_id):
        with self._lock:
            return self._runs.get(run_id)


STORE = RunStore()
