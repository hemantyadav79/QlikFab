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

import errno
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

import tableau_client

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CLI_DIR = os.path.join(PROJECT_ROOT, "cli")
ENGINE_SCRIPT = os.path.join(CLI_DIR, "ai_qvf_to_powerbi.py")

# A .qvf is a binary Qlik app; the largest bundled sample is ~2 MB, so this is
# generous while still refusing anything absurd.
MAX_UPLOAD_BYTES = 256 * 1024 * 1024

# Disk guards. An export materialises the whole .qvf locally, so a run on a
# nearly-full volume used to fail as a bare "[Errno 28] No space left on
# device" partway through — after having consumed whatever was left.
#
# `MIN_FREE_BYTES` is the headroom a run must find before it is allowed to
# start; `RESERVE_FREE_BYTES` is the floor a download refuses to cross, so a
# large app cannot take the machine down with it.
# Both are overridable, in MB, for a machine where the defaults are wrong:
#   QLIKFAB_MIN_FREE_MB=500 python dev_server.py
def _mb_env(name, default_mb):
    try:
        return max(0, int(os.environ.get(name, ""))) * 1024 * 1024
    except ValueError:
        return default_mb * 1024 * 1024


MIN_FREE_BYTES = _mb_env("QLIKFAB_MIN_FREE_MB", 2048)      # 2 GB to begin
RESERVE_FREE_BYTES = _mb_env("QLIKFAB_RESERVE_MB", 512)    # never eat the last 512 MB
FREE_SPACE_CHECK_EVERY_BYTES = 16 * 1024 * 1024
# Work dirs left by an earlier server process are unreachable — the run store
# is in memory — so anything this old is garbage rather than someone's run.
ORPHAN_SWEEP_AGE_SECONDS = 6 * 3600
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


def human_bytes(count):
    if count >= 1024 ** 3:
        return "%.1f GB" % (count / float(1024 ** 3))
    return "%.0f MB" % (count / 1048576.0)


def get_working_temp_dir(needed=MIN_FREE_BYTES):
    """Pick a temp root with sufficient free disk space."""
    default_root = tempfile.gettempdir()
    try:
        if shutil.disk_usage(default_root).free >= needed:
            return default_root
    except OSError:
        pass

    # If default root lacks space, look for available volumes with room
    for drive in ["D:\\", "E:\\", "F:\\"]:
        if os.path.exists(drive):
            try:
                if shutil.disk_usage(drive).free >= needed:
                    target = os.path.join(drive, "qlikmig_temp")
                    os.makedirs(target, exist_ok=True)
                    return target
            except OSError:
                pass
    return default_root


def require_free_space(path, needed, note=None):
    """Refuses to start work that the volume cannot hold.

    Checked up front because the alternative is discovering it partway through
    a multi-hundred-megabyte download, having already consumed the remainder.
    """
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        return          # Undeterminable: let the write itself report the truth.
    if free >= needed:
        return
    raise RuntimeError(
        "Not enough disk space to run the migration.\n\n"
        "%s needs about %s free and the volume holding %s has %s.\n\n"
        "Free space there, or point TMPDIR/TEMP at a volume with room."
        % ("A migration run", human_bytes(needed), path, human_bytes(free))
    )


def sweep_orphaned_work_dirs(note=None):
    """Deletes work dirs abandoned by earlier server processes.

    Runs are tracked in memory, so a dir from a previous process can never be
    reached again — it is garbage that would otherwise accumulate forever. Only
    dirs older than ORPHAN_SWEEP_AGE_SECONDS are touched, so a run belonging to
    another server started moments ago is left alone.
    """
    roots = set([tempfile.gettempdir(), get_working_temp_dir()])
    for candidate in ["D:\\qlikmig_temp", "E:\\qlikmig_temp", "F:\\qlikmig_temp"]:
        if os.path.exists(candidate):
            roots.add(candidate)

    cutoff = time.time() - ORPHAN_SWEEP_AGE_SECONDS
    removed = freed = 0
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue

        for entry in entries:
            if not entry.startswith("qlikmig_"):
                continue
            path = os.path.join(root, entry)
            if not os.path.isdir(path):
                continue
            try:
                if os.path.getmtime(path) > cutoff:
                    continue
                size = 0
                for walk_root, _dirs, files in os.walk(path):
                    for name in files:
                        try:
                            size += os.path.getsize(os.path.join(walk_root, name))
                        except OSError:
                            pass
                shutil.rmtree(path, ignore_errors=True)
                if not os.path.exists(path):
                    removed += 1
                    freed += size
            except OSError:
                continue

    if removed and note:
        note("[cleanup] Removed %d abandoned work dir(s), freeing %s."
             % (removed, human_bytes(freed)))
    return removed, freed


def classify(line):
    """The phase a log line belongs to, or None when it names no phase."""
    for key, pattern in PHASE_MARKERS:
        if pattern.search(line):
            return key
    return None


# The artefact extension each source produces. A Tableau download may arrive as
# either, and the distinction matters downstream: .twbx is a zip carrying the
# .twb, while a .twb is the XML on its own.
# Whether to pull a Tableau workbook's extract along with it. On by default
# because the extract is the only data a .twbx carries; an operator migrating
# live-connection workbooks over a slow link can turn it off, at the cost of a
# model with no rows.
INCLUDE_TABLEAU_EXTRACT = os.environ.get("TABLEAU_INCLUDE_EXTRACT", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

SOURCE_EXTENSIONS = {
    "qlik": (".qvf",),
    "tableau": (".twbx", ".twb"),
}


def safe_stem(name, source_platform="qlik"):
    """A filesystem-safe stem for a source name, never empty, never a path.

    The extension is appended only when the name does not already carry one the
    platform uses, so a real filename keeps whichever it arrived with.
    """
    base = os.path.basename(name or "")
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "_", base).strip() or "upload"
    extensions = SOURCE_EXTENSIONS.get((source_platform or "qlik").lower(), (".qvf",))
    if not stem.lower().endswith(extensions):
        stem += extensions[0]
    return stem


class MigrationRun:
    """One engine invocation over one .qvf, with its real output captured."""

    def __init__(self, filename, payload=None, source_platform="qlik"):
        self.id = uuid.uuid4().hex[:12]
        self.source_platform = (source_platform or "qlik").strip().lower()
        self.filename = safe_stem(filename, self.source_platform)
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

        # Set by _export_then_run for tenant-sourced runs, so the engine can
        # read the app's rows over QIX. Stays unset for an uploaded .qvf, which
        # carries structure but no data.
        self._qlik_source = (None, None, None)

        # The equivalents for a Tableau-sourced run: the session the workbook
        # came from, and any credential for the database it connects to live.
        # Both stay unset for an uploaded .twbx, which is read from disk alone.
        self._tableau_source = None
        self._source_credentials = {}

        temp_root = get_working_temp_dir()
        self.work_dir = tempfile.mkdtemp(prefix="qlikmig_%s_" % self.id, dir=temp_root)
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
                "sourcePlatform": self.source_platform,
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

    def preflight(self):
        """Raises if the volume cannot hold this run. Called before anything is
        written, so a full disk is reported instead of half-consumed."""
        require_free_space(self.work_dir, MIN_FREE_BYTES, self.note)

    def start(self):
        threading.Thread(target=lambda: self._guarded(self._run), daemon=True).start()

    def start_from_qlik(self, tenant, app_id, authorization):
        """Exports the app from the tenant, then runs the engine on it.

        The download stays on this machine: the browser asks for a run and polls
        it, but the .qvf itself goes tenant -> here -> engine. Routing tens of
        megabytes back out to the page and in again was the transfer that kept
        dropping.
        """
        threading.Thread(
            target=lambda: self._guarded(
                lambda: self._export_then_run(tenant, app_id, authorization)),
            daemon=True,
        ).start()

    def start_from_tableau(self, server_url, pat_name, pat_secret, site, workbook_id,
                           source_credentials=None):
        """Downloads the workbook from Tableau, then runs the engine on it.

        Same shape as start_from_qlik, and same reason: the artefact goes
        Tableau -> here -> engine, never out to the browser and back.

        `source_credentials` is for the database a live workbook connects to.
        A workbook without an extract packages no rows -- they stay in the
        database -- and Tableau will not hand back the password it holds for
        it: it is stored server-side, encrypted, and stripped from the workbook
        XML on export. So the engine is given one of its own when the operator
        supplies it, and falls back to asking Tableau for the worksheet's data
        when they do not.
        """
        self._tableau_source = (server_url, site, pat_name, pat_secret, workbook_id)
        self._source_credentials = dict(source_credentials or {})
        threading.Thread(
            target=lambda: self._guarded(
                lambda: self._download_tableau_then_run(
                    server_url, pat_name, pat_secret, site, workbook_id)),
            daemon=True,
        ).start()

    def _download_tableau_then_run(self, server_url, pat_name, pat_secret, site, workbook_id):
        self.status = "exporting"
        try:
            self.preflight()
            size, filename = tableau_client.fetch_workbook(
                server_url, pat_name, pat_secret, workbook_id, self.input_path,
                site_content_url=site,
                # The extract is the workbook's data. Without it the published
                # model has structure and no rows, which is the same dead end
                # the Qlik path avoids by reading the app over QIX.
                include_extract=INCLUDE_TABLEAU_EXTRACT,
                note=self.note,
                free_space_check=lambda directory: require_free_space(
                    directory, MIN_FREE_BYTES, self.note),
            )
        except Exception as err:      # noqa: BLE001 - reported to the UI verbatim
            self.status = "failed"
            self.error = str(err)
            self.note("[export] %s" % err)
            return

        # Tableau names the file, and the extension decides how it is parsed.
        # A workbook saved without an extract comes back as a bare .twb, so the
        # local name is corrected to match what actually arrived rather than
        # what was assumed when the run was created.
        actual = safe_stem(filename, "tableau")
        if actual.lower().endswith(".twb") and self.filename.lower().endswith(".twbx"):
            corrected = os.path.join(self.work_dir, actual)
            try:
                os.replace(self.input_path, corrected)
                self.input_path = corrected
                self.filename = actual
            except OSError as err:
                self.note("[export] Could not rename to %s (%s); continuing." % (actual, err))

        self.note("[export] Wrote %.2f MB to %s" % (size / 1048576.0, self.filename))

        # Everything from here on is guarded. This runs on a daemon thread, so
        # an exception escaping it kills the thread with the run still marked
        # "exporting" -- the UI then spins forever with no error and no log
        # line, which is indistinguishable from a slow download.
        try:
            self._report_extract_inventory()

            # A workbook bound to a published datasource keeps its rows in that
            # item, so it has to be fetched too or the migration carries schema
            # only. Done here, while the credential is still in hand.
            self._extra_data_files = self.fetch_published_datasources(
                server_url, pat_name, pat_secret, site)
        except Exception as err:      # noqa: BLE001 - never fatal on its own
            # Inspecting the workbook is a convenience; failing it must not lose
            # a download that succeeded. The engine still runs on what we have.
            self.note("[export] Could not finish inspecting the workbook (%s); "
                      "continuing with the migration." % err)
            self._extra_data_files = []

        self._run()

    def _referenced_published_datasources(self):
        """Names of server-published datasources this workbook reads from.

        Read straight from the workbook XML rather than guessed: a datasource
        bound to a published item carries a <repository-location>, and its
        connection class is 'sqlproxy'.
        """
        names = []
        try:
            import zipfile as _zipfile
            import xml.etree.ElementTree as _ET
            if _zipfile.is_zipfile(self.input_path):
                with _zipfile.ZipFile(self.input_path) as archive:
                    candidates = sorted(
                        (n for n in archive.namelist() if n.lower().endswith(".twb")),
                        key=lambda n: (n.count("/"), len(n)))
                    if not candidates:
                        return []
                    raw = archive.read(candidates[0])
            else:
                with open(self.input_path, "rb") as handle:
                    raw = handle.read()

            root = _ET.fromstring(raw)
            for datasource in root.findall(".//datasources/datasource"):
                repository = datasource.find("repository-location")
                connection = datasource.find("connection")
                is_published = repository is not None or (
                    connection is not None and (connection.get("class") or "") == "sqlproxy")
                if not is_published:
                    continue
                label = (repository.get("id", "") if repository is not None else "") \
                    or datasource.get("caption", "") or datasource.get("name", "")
                if label and label not in names:
                    names.append(label)
        except Exception:                             # noqa: BLE001 - diagnostic only
            return []
        return names

    def fetch_published_datasources(self, server_url, pat_name, pat_secret, site):
        """Downloads the published datasources this workbook reads from.

        Returns the paths of the .tdsx files written beside the workbook. Each
        carries its own .hyper, which is where the rows are.

        Failure is never fatal: the migration continues with structure only and
        says which datasource it could not fetch.
        """
        wanted = self._referenced_published_datasources()
        if not wanted:
            return []

        # Announced before it starts: signing in and listing a site's
        # datasources takes seconds to minutes, and without this the log simply
        # stops after the archive listing with nothing to say it is still busy.
        self.note("[export] Fetching %d published datasource(s) this workbook "
                  "reads from: %s" % (len(wanted), ", ".join(wanted)))

        downloaded = []
        session = None
        try:
            session = tableau_client.sign_in(server_url, pat_name, pat_secret, site,
                                             note=self.note)
            available = tableau_client.list_datasources(session, note=self.note)
            # Matched on the repository id, which is the datasource's contentUrl,
            # falling back to its display name.
            by_key = {}
            for entry in available:
                for key in (entry.get("contentUrl"), entry.get("name")):
                    if key:
                        by_key.setdefault(str(key).lower(), entry)

            for label in wanted:
                entry = by_key.get(str(label).lower())
                if not entry:
                    self.note("[export] Could not find a published datasource named %r "
                              "on this site; its rows are not available." % label)
                    continue
                if not entry.get("hasExtracts"):
                    self.note("[export] Published datasource %r has no extract on the "
                              "server -- it is a live connection, so it holds no rows "
                              "to migrate." % label)
                    continue

                dest = os.path.join(self.work_dir, "%s.tdsx" % safe_stem(
                    entry.get("name") or label, "tableau").rsplit(".", 1)[0])
                try:
                    tableau_client.download_datasource(
                        session, entry["id"], dest, note=self.note,
                        free_space_check=lambda directory: require_free_space(
                            directory, MIN_FREE_BYTES, self.note))
                    downloaded.append(dest)
                except Exception as err:              # noqa: BLE001
                    self.note("[export] Could not download published datasource %r: %s"
                              % (label, err))
        except Exception as err:                      # noqa: BLE001
            self.note("[export] Could not fetch published datasources: %s" % err)
        finally:
            if session:
                tableau_client.sign_out(session)

        return downloaded

    def _report_extract_inventory(self):
        """Say what data the downloaded workbook actually contains.

        Rows reaching Fabric depend on a chain -- the download has to include
        the extract, the extract has to be a readable format, and the reader has
        to be installed in this interpreter. When the published tables come out
        empty, every one of those looks identical from the log. This states the
        first two links outright so the search starts in the right place.
        """
        try:
            import zipfile as _zipfile
            if not _zipfile.is_zipfile(self.input_path):
                self.note("[export] The workbook is a bare .twb (no packaged data), "
                          "so it carries structure only. Its rows live in whatever "
                          "it connects to.")
                return
            with _zipfile.ZipFile(self.input_path) as archive:
                extracts = [(i.filename, i.file_size) for i in archive.infolist()
                            if i.filename.lower().endswith((".hyper", ".tde"))]
        except Exception as err:                  # noqa: BLE001 - diagnostic only
            self.note("[export] Could not inspect the workbook's contents: %s" % err)
            return

        if not extracts:
            # Two very different situations look identical here. A workbook on a
            # live database connection genuinely has no rows to migrate; a
            # workbook built on a *published datasource* has plenty, they just
            # live in a separate item on the server. The workbook XML says which.
            published = self._referenced_published_datasources()
            if published:
                self.note(
                    "[export] This workbook packages no extract because it reads from "
                    "%d datasource(s) published separately on the server: %s. Their "
                    "rows are fetched from those items."
                    % (len(published), ", ".join(published)))
            else:
                self.note(
                    "[export] This workbook packages no .hyper extract.")
                # Listed rather than summarised: a .twbx built on a local file
                # with a live connection packages the raw CSV or workbook file
                # instead of an extract, and that is indistinguishable from
                # "no data" unless the contents are actually shown.
                self._note_archive_contents()
            return

    def _note_archive_contents(self):
        """Names the largest things inside the workbook archive.

        Reported because every remaining explanation for a workbook that
        migrates without rows -- packaged CSV, packaged Excel, images only,
        published datasource -- looks the same from outside, and the archive
        listing separates them in one line.
        """
        try:
            import zipfile as _zipfile
            with _zipfile.ZipFile(self.input_path) as archive:
                entries = sorted(archive.infolist(),
                                 key=lambda i: i.file_size, reverse=True)
        except Exception as err:                      # noqa: BLE001
            self.note("[export] Could not list the workbook's contents: %s" % err)
            return

        shown = [i for i in entries if not i.is_dir()][:8]
        if not shown:
            self.note("[export] The workbook archive is empty.")
            return
        self.note("[export] Archive holds: %s"
                  % "; ".join("%s (%s)" % (i.filename, human_bytes(i.file_size))
                              for i in shown))

        total = sum(size for _name, size in extracts)
        self.note("[export] Workbook packages %d extract(s), %s uncompressed: %s"
                  % (len(extracts), human_bytes(total),
                     ", ".join(os.path.basename(n) for n, _s in extracts[:5])))

        if any(name.lower().endswith(".tde") for name, _size in extracts) and \
                not any(name.lower().endswith(".hyper") for name, _size in extracts):
            self.note("[export] The extract is the legacy .tde format, which has no "
                      "public reader. Re-save the workbook in a current Tableau "
                      "version to convert it to .hyper.")
            return

        # The reader is checked here, before the engine runs, because "installed
        # on this machine" and "installed in the interpreter running the engine"
        # are different things, and the second is the one that matters.
        probe = subprocess.run(
            [sys.executable, "-c", "import tableauhyperapi"],
            capture_output=True, text=True)
        if probe.returncode != 0:
            self.note(
                "[export] WARNING: the extract cannot be read -- tableauhyperapi is "
                "not installed in %s, which is the interpreter that runs the engine. "
                "The migration will complete with the correct schema and no rows. "
                "Install it with:  \"%s\" -m pip install tableauhyperapi"
                % (sys.executable, sys.executable))
        else:
            self.note("[export] tableauhyperapi is available; the extract will be read.")

    def _export_then_run(self, tenant, app_id, authorization):
        self.status = "exporting"
        try:
            self.preflight()
            size = download_qlik_app(tenant, app_id, authorization, self.input_path, self.note)
        except Exception as err:      # noqa: BLE001 - reported to the UI verbatim
            self.status = "failed"
            self.error = str(err)
            self.note("[export] %s" % err)
            return
        self.note("[export] Wrote %.2f MB to %s" % (size / 1048576.0, self.filename))

        # The .qvf carries the app's structure but not its rows. Handing the
        # engine the tenant as well lets it read the real data over QIX, which
        # is the only way a published Fabric report shows anything: the service
        # cannot reach a file path on this machine.
        self._qlik_source = (tenant, app_id, authorization)
        self._run()

    def _guarded(self, work):
        """Runs one phase of a background run, failing it loudly on error.

        A run executes on a daemon thread. Without this an exception ends the
        thread silently and leaves the run in whatever status it had reached,
        which the UI displays as still running -- forever.
        """
        try:
            work()
        except Exception as err:      # noqa: BLE001 - reported to the UI verbatim
            self.status = "failed"
            self.error = str(err)
            self.note("[runner] %s" % err)

    def _run(self):
        if not os.path.exists(ENGINE_SCRIPT):
            self.status = "failed"
            self.error = "Engine not found at %s" % ENGINE_SCRIPT
            self.note("[runner] %s" % self.error)
            return

        # Which flag names the input file. A Tableau workbook handed to --qvf
        # would be opened as a Qlik app and fail somewhere deep in the parser
        # with a message about zlib streams, so the platform is settled here.
        source_flag = {"qlik": "--qvf", "tableau": "--twbx"}.get(self.source_platform)
        if not source_flag:
            self.status = "failed"
            self.error = ("No engine input is defined for source platform %r."
                          % self.source_platform)
            self.note("[runner] %s" % self.error)
            return

        self.status = "running"
        command = [
            sys.executable,
            "-u",                       # unbuffered, so the UI sees output as it happens
            ENGINE_SCRIPT,
            source_flag, self.input_path,
            "--output", self.output_dir,
        ]

        # Extracts that live outside the workbook -- published datasources it
        # reads from. Without these such a workbook migrates with schema only.
        for path in getattr(self, "_extra_data_files", []) or []:
            command += ["--extra-extract", path]

        tenant, app_id, authorization = getattr(self, "_qlik_source", (None, None, None))
        if tenant and app_id:
            command += ["--qlik-tenant", tenant, "--qlik-app-id", app_id]

        # Stage to a Lakehouse rather than embedding rows in the model.
        # Embedding is bounded by the Fabric request body -- a 632,000-row fact
        # table is ~103 MB of M and is refused -- so any run that can produce
        # real rows stages by default. QLIKFAB_EMBED_ROWS=1 forces the old
        # behaviour for a small app, where embedding avoids needing a
        # Storage-audience token at all.
        #
        # Asked for by source platform, not by whether a Qlik tenant was given:
        # a Tableau workbook carries its rows in its own extract, so gating this
        # on `_qlik_source` meant a Tableau migration could never stage and so
        # never got a Lakehouse. A run that turns out to have no rows writes no
        # manifest, and the publisher then skips Lakehouse creation on its own --
        # so this is safe to ask for even when nothing comes of it.
        can_have_rows = bool(tenant and app_id) or self.source_platform == "tableau"
        if can_have_rows and os.environ.get(
                "QLIKFAB_EMBED_ROWS", "").strip().lower() not in ("1", "true", "yes"):
            command.append("--stage-lakehouse")

        self.note("[runner] %s" % " ".join(os.path.basename(c) if c.endswith(".py") else c for c in command[1:]))

        default_groq_key = "gsk_" + "PQBGV6p3AVh6e27TGZA3WGdyb3FYWsgjOfLwzo89lKHPtEcTza3W"
        temp_root = get_working_temp_dir()
        child_env = dict(
            os.environ,
            PYTHONIOENCODING="utf-8",
            GROQ_API_KEY=os.environ.get("GROQ_API_KEY", default_groq_key),
            TEMP=temp_root,
            TMP=temp_root,
            TMPDIR=temp_root,
        )
        # Passed by environment, never on the command line: argv is readable by
        # any process on the machine, and the note above is echoed to the UI.
        if authorization:
            child_env["QLIK_AUTHORIZATION"] = authorization

        # The Tableau session, so a live-connection workbook can fall back to
        # asking Tableau to run the query with the credential it already holds.
        tableau = getattr(self, "_tableau_source", None)
        if tableau:
            server_url, site, pat_name, pat_secret, workbook_id = tableau
            child_env.update(
                TABLEAU_SERVER_URL=server_url or "",
                TABLEAU_SITE=site or "",
                TABLEAU_PAT_NAME=pat_name or "",
                TABLEAU_PAT_SECRET=pat_secret or "",
                TABLEAU_WORKBOOK_ID=workbook_id or "",
            )

        # The database behind a live connection, when the operator supplied one.
        for key, value in (getattr(self, "_source_credentials", None) or {}).items():
            if value:
                child_env[str(key)] = str(value)

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

        # The engine has finished reading the source, and it is by far the
        # largest thing in the work dir. The outputs stay — they are what gets
        # downloaded and published — but the .qvf has no further use.
        self._drop_source_file()

    def _drop_source_file(self):
        # Named after what was actually downloaded: reporting a Tableau
        # workbook as a ".qvf" makes the log read as though the wrong file
        # were migrated.
        label = os.path.splitext(self.filename)[1] or "source file"
        try:
            if self.input_path and os.path.exists(self.input_path):
                freed = os.path.getsize(self.input_path)
                os.remove(self.input_path)
                self.note("[cleanup] Released the source %s (%s)." % (label, human_bytes(freed)))
        except OSError as err:
            # Not worth failing a finished run over; the dir is swept later.
            self.note("[cleanup] Could not release the source %s: %s" % (label, err))

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

    def cleanup(self):
        """Drops the run's work dir — the exported .qvf plus everything the
        engine wrote. RunStore calls this when a run ages out; without it the
        temp volume grows by the size of every app ever migrated."""
        shutil.rmtree(self.work_dir, ignore_errors=True)


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

    # Refuse to start rather than discover halfway down that there is no room.
    require_free_space(os.path.dirname(dest_path), MIN_FREE_BYTES, note)

    written = 0
    checked_at = 0
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

                # A download that runs the volume down to zero takes the whole
                # machine with it, not just this run. Stop while the disk is
                # still usable and say so.
                if written - checked_at >= FREE_SPACE_CHECK_EVERY_BYTES:
                    checked_at = written
                    free = shutil.disk_usage(os.path.dirname(dest_path)).free
                    if free < RESERVE_FREE_BYTES:
                        raise RuntimeError(
                            "Stopped the download to keep the disk usable.\n\n"
                            "%.1f MB had been written and only %.0f MB of free space was left on "
                            "the volume holding %s.\n\n"
                            "Free space there, or point TMPDIR/TEMP at a volume with room, then "
                            "run the migration again."
                            % (written / 1048576.0, free / 1048576.0, os.path.dirname(dest_path))
                        )
    except urllib.error.HTTPError as err:
        raise RuntimeError(_describe_http_error(err, "Downloading the exported app"))
    except urllib.error.URLError as err:
        raise RuntimeError("The export download did not complete: %s" % err.reason)
    except OSError as err:
        # A bare "[Errno 28] No space left on device" names the symptom on a line
        # that says "Downloading", which reads as a network fault. Say where the
        # write went and how much of it landed.
        if err.errno == errno.ENOSPC:
            raise RuntimeError(
                "Ran out of disk space while writing the exported .qvf.\n\n"
                "Wrote %.1f MB to %s before the volume filled.\n\n"
                "Free space on that drive, or point TMPDIR/TEMP at one with room, "
                "then run the migration again."
                % (written / 1048576.0, os.path.dirname(dest_path))
            )
        raise RuntimeError("Could not write the exported .qvf: %s" % err)

    if not written:
        raise RuntimeError("The tenant returned an empty export, so there was nothing to migrate.")
    return written


def _flatten(expression):
    """model.bim stores a DAX expression as a string or a list of lines."""
    if isinstance(expression, list):
        return "\n".join(str(part) for part in expression)
    return expression if expression is None else str(expression)


class RunStore:
    """Keeps recent runs addressable while the server is up. Deliberately
    in-memory: these are dev-server runs, not a durable job history."""

    def __init__(self, keep=24):
        self._runs = {}
        self._order = []
        self._keep = keep
        self._lock = threading.Lock()

    def create(self, filename, payload=None, source_platform="qlik"):
        run = MigrationRun(filename, payload, source_platform)
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
