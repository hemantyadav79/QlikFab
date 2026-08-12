"""
Reads real table data out of a Qlik Cloud app over the QIX engine websocket.

Why this exists
---------------
A migrated model is only as useful as the rows in it. Everything else in this
tool works from the `.qvf`, but a `.qvf` gives up its script, its sheets and its
field metadata -- not its data. The rows live in the app's in-memory model on
the tenant, reachable only through a QIX engine session. Without them a Fabric
report renders correct, well-placed, correctly-typed visuals over nothing,
which is the failure this module removes.

What it does not do
-------------------
It does not invent, sample or infer. If a table cannot be read, the caller is
told which one and why, and that table keeps the empty-schema partition it
would have had anyway. A partial read is reported as partial.

Protocol notes
--------------
QIX is JSON-RPC 2.0 over a websocket at `wss://{tenant}/app/{appId}`. Handles
are integers returned by earlier calls; -1 is the global object. `GetTableData`
is used rather than a hypercube because a hypercube over N dimensions returns
*distinct combinations* -- two identical records in the source would silently
collapse into one, which is a data-fidelity bug that looks like a smaller
dataset rather than an error.
"""
import json
import ssl
import sys
import time
import urllib.parse

# Rows requested per GetTableData call.
#
# Raising this from 2,000 to 10,000 cut round trips fivefold and changed the
# throughput not at all: 1,629 rows/sec over 2,000-row pages, 1,609 rows/sec
# over 10,000-row pages. Round-trip latency was never the constraint. The cost
# is per cell -- roughly 22,500 cells/sec, each arriving as its own JSON object
# -- so it scales with columns, not with calls, and no page size fixes that.
#
# Kept at 10,000 because fewer calls is still marginally better and costs
# nothing. Faster would need several sessions reading different offsets at once,
# which is only worth building if the engine parallelises across sessions.
#
# Safe at any size: the engine limits a response by cell count and returns a
# shorter page than asked for, which read_table handles rather than mistaking
# for the end of the table.
ROWS_PER_CALL = 10000

# How often to report progress while paging through a large table. Without this
# a multi-minute read produces no output at all and looks indistinguishable
# from a hang.
PROGRESS_EVERY_SECONDS = 3.0

# A guard on total volume per table. Embedding rows into model.bim makes the
# file grow roughly linearly, and a multi-million-row fact table would produce
# an artefact too large for the Fabric items API to accept. Tables past this
# are read up to the cap and reported as capped -- never silently trimmed.
DEFAULT_MAX_ROWS = 50000

CONNECT_TIMEOUT_SECONDS = 30
CALL_TIMEOUT_SECONDS = 120


class QlikDataError(RuntimeError):
    """Raised with a message intended to be shown to the user verbatim."""


class QixSession:
    """One authenticated QIX session against one app."""

    def __init__(self, tenant: str, app_id: str, authorization: str, note=None):
        self.tenant = str(tenant or "").rstrip("/")
        self.app_id = str(app_id or "")
        self.authorization = authorization
        self.note = note or (lambda _text: None)
        self._ws = None
        self._next_id = 0
        self._doc_handle = None

    # ---------- connection ----------

    def _url(self) -> str:
        host = urllib.parse.urlparse(self.tenant).hostname or self.tenant
        return "wss://%s/app/%s" % (host, urllib.parse.quote(self.app_id))

    def __enter__(self):
        try:
            import websocket  # websocket-client
        except ImportError as err:      # noqa: BLE001
            # The interpreter is named because it is almost never the one the
            # reader is tested from. The engine runs as a subprocess of the dev
            # server, so it inherits *that* server's Python -- and a machine
            # with both a python.org install and the Microsoft Store build will
            # happily have the package in one and not the other. Without the
            # path in this message the obvious next step ("pip install
            # websocket-client") installs it into the wrong one and the error
            # comes back unchanged.
            raise QlikDataError(
                "Reading data from Qlik Cloud needs the 'websocket-client' "
                "package, and this interpreter does not have it:\n\n"
                "    %s\n\n"
                "Install it there specifically:\n\n"
                '    "%s" -m pip install websocket-client\n\n'
                "Without it the migration still runs, but every table will be "
                "empty." % (sys.executable, sys.executable)
            ) from err

        header = ["Authorization: %s" % self.authorization]
        try:
            self._ws = websocket.create_connection(
                self._url(),
                header=header,
                timeout=CALL_TIMEOUT_SECONDS,
                sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                # The engine rejects a session with no origin on some tenants.
                origin=self.tenant,
                suppress_origin=False,
            )
        except Exception as err:        # noqa: BLE001 - surfaced verbatim
            raise QlikDataError(
                "Could not open a QIX session on %s: %s\n\n"
                "The API key needs access to this app, and the tenant must allow "
                "websocket connections from this host." % (self._url(), err)
            ) from err

        # The engine greets a new session before answering anything.
        self._drain_greeting()
        self._doc_handle = self._open_doc()
        return self

    def __exit__(self, *_exc):
        try:
            if self._ws:
                self._ws.close()
        except Exception:               # noqa: BLE001 - nothing useful to do
            pass
        return False

    def _drain_greeting(self):
        try:
            raw = self._ws.recv()
            message = json.loads(raw)
        except Exception:               # noqa: BLE001 - not fatal
            return
        params = message.get("params") or {}
        if params.get("qSessionState"):
            self.note("[qix] Session state: %s" % params["qSessionState"])

    # ---------- rpc ----------

    def _call(self, method: str, handle: int, params):
        self._next_id += 1
        request_id = self._next_id
        self._ws.send(json.dumps({
            "jsonrpc": "2.0", "id": request_id,
            "method": method, "handle": handle, "params": params,
        }))

        # Skip unsolicited notifications (OnConnected, change events) and any
        # reply to an earlier call; only this request's id counts.
        while True:
            try:
                message = json.loads(self._ws.recv())
            except Exception as err:    # noqa: BLE001
                raise QlikDataError(
                    "The QIX session dropped during %s: %s" % (method, err)
                ) from err

            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"] or {}
                raise QlikDataError(
                    "Qlik refused %s: %s (code %s)"
                    % (method, error.get("message", "no message"), error.get("code", "?"))
                )
            return message.get("result") or {}

    def _open_doc(self) -> int:
        result = self._call("OpenDoc", -1, [self.app_id])
        handle = ((result.get("qReturn") or {}).get("qHandle"))
        if handle is None:
            raise QlikDataError(
                "Qlik opened the app but returned no document handle, so no data "
                "can be read from it."
            )
        return handle

    # ---------- data ----------

    def table_layout(self) -> dict:
        """
        Field names and row counts per data-model table.

        `qSyntheticMode` is false so the engine reports the *source* tables --
        the ones the load script created, whose names match what the migration
        already knows -- rather than its internal representation with synthetic
        keys spliced in.

        Response shape (Engine API): {"qtr": [TableRecord], "qk": [...]} where
        a TableRecord carries qName, qNoOfRows and qFields[].qName.
        """
        result = self._call("GetTablesAndKeys", self._doc_handle, {
            "qWindowSize": {"qcx": 10000, "qcy": 10000},
            "qNullSize": {"qcx": 0, "qcy": 0},
            "qCellHeight": 30,
            "qSyntheticMode": False,
            "qIncludeSysVars": False,
        })
        layout = {}
        for table in result.get("qtr") or []:
            name = table.get("qName")
            if not name:
                continue
            layout[name] = {
                "columns": [f.get("qName") for f in (table.get("qFields") or [])
                            if f.get("qName")],
                "rows": table.get("qNoOfRows"),
            }
        return layout

    def read_table(self, table_name: str, columns, max_rows: int = DEFAULT_MAX_ROWS,
                   expected_rows: int = None):
        """
        Read one table's rows.

        `columns` must come from GetTablesAndKeys: GetTableData returns values
        only, with no header row, so the column names and their order are not
        recoverable from the data call itself. `expected_rows` comes from the
        same call and is what tells us the read is finished -- see the loop
        below for why a short page cannot be used for that.

        Returns (rows, truncated). A row is a list of values in column order;
        a null stays None so an empty string can still be told apart from a
        missing value.
        """
        rows, truncated = [], False
        # max_rows None means no cap -- used when staging to a Lakehouse, which
        # has no size ceiling. With no cap and no reported count there is
        # nothing to aim at, so the loop runs until the engine returns nothing.
        cap = float("inf") if max_rows is None else max_rows
        target = cap if expected_rows is None else min(expected_rows, cap)

        started = last_report = time.time()

        while len(rows) < target:
            want = int(min(ROWS_PER_CALL, target - len(rows)))
            result = self._call("GetTableData", self._doc_handle, {
                "qOffset": len(rows),
                "qRows": want,
                "qSyntheticMode": False,
                "qTableName": table_name,
            })

            batch = _rows_from_page(result.get("qData"), len(columns))
            if not batch:
                # A genuinely empty page is the end of the table. A short page
                # is NOT: the engine limits a response by cell count, not rows,
                # so a wide table returns fewer rows than asked for and still
                # has more to give. Treating short as final silently truncated
                # every table wider than a few columns.
                break
            rows.extend(batch)

            # Reading a 632,000-row table takes minutes. Silence for that long
            # is indistinguishable from a hang, so progress is reported with a
            # rate and an estimate rather than left to be guessed at.
            now = time.time()
            if now - last_report >= PROGRESS_EVERY_SECONDS and len(rows) < target:
                last_report = now
                rate = len(rows) / max(now - started, 0.001)
                if target != float("inf"):
                    remaining = (target - len(rows)) / max(rate, 1)
                    self.note("[qix] %s: %s of %s row(s) (%.0f/s, ~%s remaining)"
                              % (table_name, f"{len(rows):,}", f"{int(target):,}",
                                 rate, _humanise(remaining)))
                else:
                    self.note("[qix] %s: %s row(s) so far (%.0f/s)"
                              % (table_name, f"{len(rows):,}", rate))

        if expected_rows is not None and len(rows) < expected_rows:
            # Either the row cap stopped us, or the engine stopped early. Those
            # are different problems and must not be reported as the same one.
            if len(rows) >= cap:
                truncated = True
            else:
                raise QlikDataError(
                    "Qlik reported %s row(s) for %r but stopped returning data after "
                    "%s. A partial table would look complete in the report, so none of "
                    "it was used." % (f"{expected_rows:,}", table_name, f"{len(rows):,}")
                )
        elif expected_rows is None and len(rows) >= cap:
            truncated = True

        return rows, truncated


def _humanise(seconds):
    """A rough duration, for progress lines only."""
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return "%ds" % seconds
    return "%dm %02ds" % (seconds // 60, seconds % 60)


def _rows_from_page(page, width):
    """
    Normalise one GetTableData response into rows.

    Documented shape: qData is an array of TableRow, each `{"qValue": [
    FieldValue, ... ]}`, and a FieldValue is `{qText, qIsNumeric, qNumber}`.
    There is no header row and no null flag.

    A row whose length does not match the table's field count is refused rather
    than padded: silently shifting values into the wrong columns produces a
    report that renders perfectly and means nothing.
    """
    if not page:
        return []

    rows = []
    for index, entry in enumerate(page):
        if not isinstance(entry, dict) or "qValue" not in entry:
            raise QlikDataError(
                "Qlik returned row %d in an unrecognised shape (%r); no rows were "
                "read rather than risk misaligned columns."
                % (index, str(entry)[:120])
            )
        row = [_cell_value(cell) for cell in (entry.get("qValue") or [])]
        if len(row) != width:
            raise QlikDataError(
                "Qlik returned a row with %d value(s) for a table with %d field(s); "
                "no rows were read rather than risk misaligned columns."
                % (len(row), width)
            )
        rows.append(row)
    return rows


def _cell_value(cell):
    """
    One FieldValue as text, or None for a null.

    Qlik signals a null by omitting qText entirely; an explicitly empty string
    is a real value and is preserved as one. The numeric form is preferred when
    the engine marks the value numeric and gives no text, so a number never
    arrives as the string 'None'.
    """
    if not isinstance(cell, dict):
        return None if cell is None else str(cell)

    if "qText" in cell:
        return cell["qText"]
    if cell.get("qIsNumeric") and cell.get("qNumber") is not None:
        number = cell["qNumber"]
        # Qlik reports every number as a double; keep integers integral so a
        # key like 1013 does not become "1013.0" and stop matching its lookup.
        if isinstance(number, float) and number.is_integer():
            return str(int(number))
        return str(number)
    return None


def read_app_tables(tenant, app_id, authorization, table_names,
                    max_rows=DEFAULT_MAX_ROWS, note=None):
    """
    Read the named tables from a Qlik Cloud app.

    Returns (data, problems). `data` maps table name -> {columns, rows,
    truncated}; `problems` maps table name -> reason it could not be read.
    A table missing from `data` is always present in `problems`, so a caller
    can never mistake "not read" for "empty".
    """
    note = note or (lambda _text: None)
    data, problems = {}, {}

    with QixSession(tenant, app_id, authorization, note) as session:
        try:
            available = session.table_layout()
        except QlikDataError as err:
            # Without the layout there are no column names, and GetTableData
            # supplies none. Reading on would mean inventing headers.
            raise QlikDataError(
                "Could not list the app's tables (%s), so no column names are "
                "available and no data was read." % err
            ) from err

        note("[qix] The app's data model has %d table(s): %s"
             % (len(available), ", ".join(sorted(available)) or "none"))

        # The engine is the authority on what the app actually holds. Some
        # .qvf files carry no data-model metadata at all -- their field lists
        # were recovered from the load script -- so when the caller cannot name
        # the tables, read everything the engine reports rather than nothing.
        if not table_names:
            table_names = sorted(available)
            note("[qix] The .qvf named no tables; reading all %d from the engine."
                 % len(table_names))

        for name in table_names:
            # The engine keys tables by their script name; match
            # case-insensitively because model.bim holds a Tabular-safe variant.
            actual = name if name in available else next(
                (t for t in available if t.lower() == str(name).lower()), None
            )
            if actual is None:
                problems[name] = ("The app's data model has no table called %r "
                                  "(it has: %s)." % (name, ", ".join(sorted(available)) or "none"))
                continue

            columns = available[actual]["columns"]
            if not columns:
                problems[name] = "Qlik reported no fields for this table."
                continue

            try:
                rows, truncated = session.read_table(
                    actual, columns, max_rows,
                    expected_rows=available[actual].get("rows"),
                )
            except QlikDataError as err:
                problems[name] = str(err)
                continue

            if not rows:
                expected = available[actual].get("rows")
                problems[name] = (
                    "Qlik returned no rows for this table"
                    + (" (it reports %s row(s), so this is unexpected)." % f"{expected:,}"
                       if expected else " (the table is empty in the app).")
                )
                continue

            data[name] = {"columns": list(columns), "rows": rows, "truncated": truncated}
            note("[qix] %s: %d of %s row(s), %d column(s)%s"
                 % (name, len(rows),
                    f"{available[actual].get('rows'):,}" if available[actual].get("rows") else "?",
                    len(columns), " - capped" if truncated else ""))

    return data, problems


def _probe(tenant, app_id, authorization):
    """
    Connect, list the tables, and read a couple of rows -- printing the raw
    engine response for the first one.

    This module's protocol handling is written against Qlik's published Engine
    API, but engine versions differ in ways the spec does not capture. Running
    this first proves the credential, the app id and the response shape in
    seconds, instead of finding out at the end of a full migration.
    """
    print("Connecting to %s (app %s)..." % (tenant, app_id))
    with QixSession(tenant, app_id, authorization, note=lambda t: print("  " + t)) as session:
        print("  Connected; document opened.\n")

        layout = session.table_layout()
        if not layout:
            print("  The engine reported no tables. Either the app has never been "
                  "reloaded, or the key cannot see its data.")
            return 1

        print("  %d table(s):" % len(layout))
        for name, info in sorted(layout.items()):
            print("    %-32s %8s row(s)  %2d column(s)"
                  % (name, f"{info['rows']:,}" if info.get("rows") else "?",
                     len(info["columns"])))

        first = sorted(layout)[0]
        print("\n  Raw GetTableData response for %r (2 rows):" % first)
        raw = session._call("GetTableData", session._doc_handle, {
            "qOffset": 0, "qRows": 2, "qSyntheticMode": False, "qTableName": first,
        })
        print("    keys returned: %s" % list(raw.keys()))
        print("    " + json.dumps(raw, indent=2)[:1500])

        print("\n  Parsed by this module:")
        rows, truncated = session.read_table(first, layout[first]["columns"], max_rows=5)
        print("    columns: %s" % layout[first]["columns"])
        for row in rows:
            print("    %s" % (row,))
    print("\nProbe finished.")
    return 0


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Check that this machine can read a Qlik Cloud app's data over QIX.",
        epilog="The credential is read from QLIK_AUTHORIZATION or QLIK_API_KEY.",
    )
    parser.add_argument("--tenant", required=True, help="e.g. https://yourtenant.us.qlikcloud.com")
    parser.add_argument("--app-id", required=True)
    args = parser.parse_args()

    key = os.environ.get("QLIK_AUTHORIZATION") or os.environ.get("QLIK_API_KEY")
    if not key:
        raise SystemExit("Set QLIK_API_KEY (or QLIK_AUTHORIZATION) first.")
    if not key.lower().startswith("bearer "):
        key = "Bearer %s" % key

    try:
        raise SystemExit(_probe(args.tenant, args.app_id, key))
    except QlikDataError as error:
        raise SystemExit("\nFailed: %s" % error)
