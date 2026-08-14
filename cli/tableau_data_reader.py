"""
Tableau Extract Reader
======================
Reads rows out of the .hyper extracts bundled inside a .twbx, so a published
model holds real data instead of an empty shell.

This is the Tableau counterpart to `qlik_data_reader.py` and answers the same
contract: `read_workbook_tables(...) -> (data, problems)`, where `data` maps a
table name to {columns, rows, truncated} and `problems` maps a table name to the
reason it could not be read. A table absent from `data` is always present in
`problems`, so a caller can never mistake "not read" for "empty".

Three things make this different from the Qlik side:

1. **The reader is optional.** `tableauhyperapi` is a large native package, and
   a workbook on a live database connection carries no extract to read. It is
   therefore imported lazily, and its absence is reported as a gap rather than
   raised as an error.

2. **Extracts are often denormalised.** Tableau frequently materialises a join
   into one flat table rather than preserving the source tables. When that
   happens the extract's table cannot be matched to the model's tables, and this
   module says so explicitly instead of guessing which columns belong where —
   projecting them apart would duplicate rows wherever the join was one-to-many
   and quietly change every total.

3. **.tde is not readable.** Workbooks saved before Tableau 10.5 carry the older
   .tde format, which has no public reader. It is reported, never approximated.
"""

import os
import zipfile

DEFAULT_MAX_ROWS = 50000


class TableauDataError(RuntimeError):
    """Raised when an extract exists but cannot be read at all."""


def _note(note):
    return note or (lambda _text: None)


def find_extracts(twbx_path):
    """Every extract inside the workbook, as (archive_name, format) pairs."""
    if not zipfile.is_zipfile(twbx_path):
        return []
    found = []
    with zipfile.ZipFile(twbx_path) as archive:
        for name in archive.namelist():
            lowered = name.lower()
            if lowered.endswith(".hyper"):
                found.append((name, "hyper"))
            elif lowered.endswith(".tde"):
                found.append((name, "tde"))
    return found


def _import_hyper_api():
    """Imports tableauhyperapi, or explains precisely why it is unavailable."""
    try:
        from tableauhyperapi import (          # noqa: F401
            Connection, HyperProcess, Telemetry, TableName, CreateMode,
        )
    except ImportError as err:
        raise TableauDataError(
            "The workbook carries a .hyper extract, but the reader for it is not "
            "installed, so no rows were read.\n\n"
            "Install it with:  pip install tableauhyperapi\n\n"
            "It is optional on purpose: workbooks on live database connections "
            "carry no extract, and the package is a large native download that "
            "those migrations never need.\n\n"
            "Underlying import error: %s" % err
        )
    from tableauhyperapi import Connection, HyperProcess, Telemetry, TableName
    return Connection, HyperProcess, Telemetry, TableName


def _extract_to_disk(twbx_path, archive_name, work_dir, note):
    """Unpacks one extract beside the workbook so the Hyper API can open it.

    The API takes a filesystem path, not a stream, so the extract has to be
    materialised. It is written under `work_dir`, which the caller owns and
    cleans up with the rest of the run.
    """
    destination = os.path.join(work_dir, os.path.basename(archive_name))
    with zipfile.ZipFile(twbx_path) as archive:
        with archive.open(archive_name) as source, open(destination, "wb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
    note("[extract] Unpacked %s (%.1f MB)."
         % (os.path.basename(archive_name), os.path.getsize(destination) / 1048576.0))
    return destination


def _read_hyper(hyper_path, max_rows, note):
    """Reads every table in one .hyper file.

    Returns {table_name: {columns, rows, truncated}}.
    """
    Connection, HyperProcess, Telemetry, TableName = _import_hyper_api()

    tables = {}
    # DO_NOT_SEND_USAGE_DATA_TO_TABLEAU: this runs inside someone's migration of
    # their own data, which is not ours to report on.
    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        with Connection(endpoint=hyper.endpoint, database=hyper_path) as connection:
            schemas = connection.catalog.get_schema_names()
            note("[extract] %d schema(s): %s"
                 % (len(schemas), ", ".join(str(s) for s in schemas) or "none"))

            for schema in schemas:
                for table_name in connection.catalog.get_table_names(schema):
                    definition = connection.catalog.get_table_definition(table_name)
                    columns = [str(column.name).strip('"') for column in definition.columns]
                    if not columns:
                        continue

                    # A row cap keeps embedded data inside the request body the
                    # Fabric API accepts. One extra row is requested so a
                    # truncated table can be reported as truncated rather than
                    # silently presented as complete.
                    limit = "" if max_rows is None else " LIMIT %d" % (max_rows + 1)
                    query = "SELECT * FROM %s%s" % (table_name, limit)

                    try:
                        result = connection.execute_list_query(query)
                    except Exception as err:              # noqa: BLE001
                        note("[extract] Could not read %s: %s" % (table_name, err))
                        continue

                    truncated = max_rows is not None and len(result) > max_rows
                    if truncated:
                        result = result[:max_rows]

                    rows = [[_coerce(value) for value in row] for row in result]
                    plain = str(getattr(table_name, "name", table_name)).strip('"')
                    tables[plain] = {
                        "columns": columns,
                        "rows": rows,
                        "truncated": truncated,
                    }
                    note("[extract] %s: %d row(s)%s, %d column(s)"
                         % (plain, len(rows), " (truncated)" if truncated else "",
                            len(columns)))
    return tables


def _coerce(value):
    """Makes a Hyper value safe to serialise into the model's embedded rows."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    # date, datetime, Interval, bytes and the rest carry no JSON form. Their
    # string form is what the extract itself states, so nothing is invented.
    return str(value)


def read_workbook_tables(twbx_path, table_names=None, max_rows=DEFAULT_MAX_ROWS,
                         work_dir=None, note=None):
    """Reads rows for the named tables out of a workbook's extracts.

    Returns (data, problems), matching the Qlik reader's contract exactly so the
    engine consumes both the same way.
    """
    note = _note(note)
    data, problems = {}, {}
    wanted = [str(n) for n in (table_names or []) if n]

    if not os.path.exists(twbx_path):
        return {}, {"(all tables)": "The workbook file is no longer on disk."}

    extracts = find_extracts(twbx_path)
    if not extracts:
        # Not an error: a live-connection workbook is a normal, supported case.
        reason = ("This workbook carries no extract, so it holds no rows — its "
                  "data lives in the connected database. The model was built "
                  "with its structure only.")
        note("[extract] %s" % reason)
        return {}, {name: reason for name in (wanted or ["(all tables)"])}

    hyper_files = [name for name, kind in extracts if kind == "hyper"]
    tde_files = [name for name, kind in extracts if kind == "tde"]

    if tde_files and not hyper_files:
        reason = ("This workbook's extract is in the legacy .tde format (Tableau "
                  "10.4 and earlier), which has no public reader. Re-save the "
                  "workbook in a current Tableau version to convert it to .hyper, "
                  "then migrate again. No rows were read.")
        note("[extract] %s" % reason)
        return {}, {name: reason for name in (wanted or ["(all tables)"])}

    work_dir = work_dir or os.path.dirname(os.path.abspath(twbx_path))
    found = {}

    for archive_name in hyper_files:
        try:
            hyper_path = _extract_to_disk(twbx_path, archive_name, work_dir, note)
        except (OSError, zipfile.BadZipFile) as err:
            note("[extract] Could not unpack %s: %s" % (archive_name, err))
            continue

        try:
            found.update(_read_hyper(hyper_path, max_rows, note))
        except TableauDataError as err:
            # The reader is missing entirely; no later extract will fare better.
            note("[extract] %s" % err)
            return {}, {name: str(err) for name in (wanted or ["(all tables)"])}
        except Exception as err:                          # noqa: BLE001
            note("[extract] Failed reading %s: %s" % (archive_name, err))
        finally:
            try:
                os.remove(hyper_path)
            except OSError:
                pass

    if not found:
        reason = "The workbook's extract could not be read, so no rows are available."
        return {}, {name: reason for name in (wanted or ["(all tables)"])}

    # Match the extract's tables to the model's, case-insensitively.
    by_lower = {name.lower(): name for name in found}

    # A single-table workbook whose extract holds a single table is that table,
    # whatever the two happen to be called. Tableau names an extract's table
    # after the extract ("Extract"), not after the source relation, so a
    # one-to-one workbook otherwise matched nothing and migrated with no rows at
    # all. This is identity, not guesswork: with one table on each side there is
    # no other pairing to make. Anything less clear-cut is still reported.
    if len(found) == 1 and len(wanted) == 1 and wanted[0].lower() not in by_lower:
        only_extract = next(iter(found))
        note("[extract] The extract's only table is %r and the model's only table "
             "is %r; reading one as the other." % (only_extract, wanted[0]))
        return {wanted[0]: found[only_extract]}, {}

    for name in (wanted or list(found)):
        actual = by_lower.get(name.lower())
        if actual:
            data[name] = found[actual]
            continue

        # The usual reason for a miss: Tableau materialised the join into one
        # flat table. Splitting it back apart would duplicate rows wherever the
        # join was one-to-many, so it is reported rather than attempted.
        problems[name] = (
            "The extract carries no table named %r. It holds: %s. Tableau often "
            "materialises a join into a single flat table, in which case the "
            "source tables are not present in it individually. Those rows were "
            "left out rather than split apart by guesswork, which would multiply "
            "rows wherever the join was one-to-many and change every total."
            % (name, ", ".join(sorted(found)) or "nothing")
        )

    unused = [name for name in found if name.lower() not in {w.lower() for w in (wanted or [])}]
    if wanted and unused:
        note("[extract] The extract also holds %d table(s) the model does not "
             "name: %s" % (len(unused), ", ".join(sorted(unused))))

    return data, problems
