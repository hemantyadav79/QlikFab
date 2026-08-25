"""
Reads real table data out of Snowflake, for Tableau workbooks on a live
connection.

Why this exists
---------------
A .twbx bundles its rows only when the workbook uses an extract. A workbook on
a live connection packages nothing but XML -- the rows stay in the database --
so the migration had no data to stage and every such table reached Fabric with
its schema and no rows. That is the same dead end the Qlik path avoids by
reading the app over QIX; this module is the equivalent for Tableau.

Why a credential is needed when Tableau already has one
-------------------------------------------------------
It cannot be recovered. Tableau stores embedded connection credentials
server-side and encrypted, the REST API does not return them, and the password
attribute is stripped from the workbook XML on export. Everything else about
the connection *is* in the XML -- class, server, database, schema, warehouse,
username -- so this module is handed all of that and asks only for the secret.

What it does not do
-------------------
It does not invent, sample or infer, and it matches the Qlik reader's contract
exactly: `read_tables` returns (data, problems), a table missing from `data` is
always present in `problems`, and a partial read is reported as partial.
"""
import os
import re

# A guard on total volume per table, mirroring the Qlik reader's. Lifted by the
# caller when staging to a Lakehouse, which has no size ceiling.
DEFAULT_MAX_ROWS = 50000

# Rows per fetch. Large enough that a wide table is not paced by round trips,
# small enough that a fact table does not arrive as one allocation.
FETCH_SIZE = 50000

LOGIN_TIMEOUT_SECONDS = 30

# Connection classes this module can read. Tableau names them in the workbook
# XML; anything else is reported by name rather than attempted, because a
# wrong-dialect query fails at the point where its error is least legible.
SUPPORTED_CLASSES = ("snowflake",)


class SnowflakeError(RuntimeError):
    """Raised with a message intended to be shown to the user verbatim."""


def account_from_server(server: str) -> str:
    """
    The account identifier the connector wants, from the host Tableau recorded.

    Tableau stores a full host -- 'xy12345.ap-south-1.aws.snowflakecomputing.com'
    -- while the connector wants the account part of it. Passing the whole host
    as the account produces a DNS name with the domain in it twice and an error
    that names neither problem.
    """
    host = (server or "").strip().lower()
    host = re.sub(r"^https?://", "", host).strip("/")
    if not host:
        return ""
    for suffix in (".snowflakecomputing.com", ".snowflakecomputing.cn"):
        if host.endswith(suffix):
            return host[: -len(suffix)]
    return host


def credentials_from_environment():
    """
    The secret half of the connection, which the workbook cannot supply.

    Read from the environment rather than argv for the same reason the Qlik key
    is: argv is readable by any process on the machine.
    """
    user = os.environ.get("SNOWFLAKE_USER", "").strip()
    password = os.environ.get("SNOWFLAKE_PASSWORD", "")
    if not password:
        return None
    return {
        "user": user,
        "password": password,
        "account": os.environ.get("SNOWFLAKE_ACCOUNT", "").strip(),
        "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", "").strip(),
        "role": os.environ.get("SNOWFLAKE_ROLE", "").strip(),
        "database": os.environ.get("SNOWFLAKE_DATABASE", "").strip(),
        "schema": os.environ.get("SNOWFLAKE_SCHEMA", "").strip(),
    }


def _connector():
    try:
        import snowflake.connector as connector       # noqa: WPS433
    except ImportError as err:
        import sys
        raise SnowflakeError(
            "Reading a live Snowflake connection needs the "
            "'snowflake-connector-python' package, and this interpreter does "
            "not have it:\n\n"
            "    %s\n\n"
            "Install it there specifically:\n\n"
            '    "%s" -m pip install snowflake-connector-python\n\n'
            "Without it the migration still runs, but a workbook with no "
            "extract migrates with its structure and no rows."
            % (sys.executable, sys.executable)
        ) from err
    return connector


def _quote(identifier: str) -> str:
    """
    One SQL identifier, quoted.

    Snowflake folds unquoted identifiers to upper case, and Tableau records
    them as the database actually spells them. Quoting preserves that spelling;
    the doubled quote guards the one character that could otherwise close the
    identifier early.
    """
    return '"%s"' % str(identifier).replace('"', '""')


def qualified_name(database: str, schema: str, table: str) -> str:
    """The fully-qualified table name, skipping the parts that are not known."""
    parts = [part for part in (database, schema, table) if part]
    return ".".join(_quote(part) for part in parts)


def split_table_reference(reference: str):
    """
    '[PUBLIC].[CUSTOMERS]' -> ('PUBLIC', 'CUSTOMERS').

    Tableau writes a relation's table either bare or schema-qualified, in
    brackets. The schema in the reference wins over the connection's default,
    because a workbook that names one is naming it for a reason.
    """
    text = str(reference or "").strip()
    if not text:
        return "", ""
    parts = re.findall(r"\[([^\]]+)\]", text)
    if not parts:
        # The extractor has already stripped the outermost brackets by the time
        # a relation reaches here, so a schema-qualified name arrives as the
        # half-bracketed 'PUBLIC].[CUSTOMERS'. Splitting on the dot and
        # discarding the stray brackets is what reads that correctly; without
        # it the schema keeps a ']' and the table gains a '[', and Snowflake
        # rejects a name that looks almost right.
        parts = text.split(".")
    parts = [part.strip().strip('"').strip("[]").strip()
             for part in parts if part.strip()]
    parts = [part for part in parts if part]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[-1] if parts else ""


def describe_target(table, connection):
    """
    Where one table's rows live, or ("", reason) if this table is not readable.

    Returns (fully-qualified name, "") when it is.
    """
    kind = (connection or {}).get("class", "")
    if not connection:
        return "", "The workbook records no connection for this table."
    if kind not in SUPPORTED_CLASSES:
        return "", ("This table is on a %s connection, which this migration "
                    "cannot read directly. Only %s is supported so far."
                    % (kind or "unnamed", ", ".join(SUPPORTED_CLASSES)))

    schema, name = split_table_reference(table.get("origin") or table.get("name"))
    if not name:
        return "", "The workbook names no source table for this relation."
    return qualified_name(connection.get("database", ""),
                          schema or connection.get("schema", ""),
                          name), ""


def read_tables(tables, credentials, max_rows=DEFAULT_MAX_ROWS, note=None):
    """
    Read the given tables from Snowflake.

    `tables` are the extractor's table records, each carrying the `connection`
    block the workbook declared. `credentials` supplies what the workbook
    cannot -- at minimum a user and password.

    Returns (data, problems), the same contract as the Qlik and extract
    readers: `data` maps table name -> {columns, rows, truncated}, `problems`
    maps table name -> the reason it could not be read, and no table appears in
    neither.
    """
    note = note or (lambda _text: None)
    data, problems = {}, {}

    readable = []
    for table in tables:
        name = table.get("name")
        if not name:
            continue
        target, reason = describe_target(table, table.get("connection") or {})
        if reason:
            problems[name] = reason
            continue
        readable.append((name, target, table.get("connection") or {}))

    if not readable:
        return data, problems

    first_connection = readable[0][2]
    account = (credentials.get("account")
               or account_from_server(first_connection.get("server", "")))
    if not account:
        reason = ("The workbook does not say which Snowflake account it "
                  "connects to, and none was supplied.")
        return {}, dict(problems, **{name: reason for name, _t, _c in readable})

    connector = _connector()
    settings = {
        "account": account,
        "user": credentials.get("user") or first_connection.get("username", ""),
        "password": credentials.get("password"),
        "login_timeout": LOGIN_TIMEOUT_SECONDS,
        "client_session_keep_alive": True,
    }
    for key, fallback_key in (("warehouse", "warehouse"), ("role", "role"),
                              ("database", "database"), ("schema", "schema")):
        value = credentials.get(key) or first_connection.get(fallback_key, "")
        if value:
            settings[key] = value

    note("[snowflake] Connecting to account %r as %r…"
         % (account, settings.get("user") or "(no user)"))
    try:
        connection = connector.connect(**settings)
    except Exception as err:            # noqa: BLE001 - surfaced verbatim
        raise SnowflakeError(
            "Could not sign in to Snowflake account %r as %r: %s\n\n"
            "The user needs SELECT on the tables the workbook reads, and a "
            "warehouse it is allowed to use."
            % (account, settings.get("user"), err)
        ) from err

    try:
        for name, target, _connection_info in readable:
            try:
                columns, rows, truncated = _read_one(
                    connection, target, max_rows, name, note)
            except Exception as err:    # noqa: BLE001 - one table, not the run
                problems[name] = "Snowflake refused %s: %s" % (target, err)
                continue

            if not rows:
                problems[name] = ("Snowflake returned no rows for %s (the table "
                                  "is empty)." % target)
                continue

            data[name] = {"columns": columns, "rows": rows, "truncated": truncated}
            note("[snowflake] %s: %s row(s), %d column(s)%s"
                 % (name, f"{len(rows):,}", len(columns),
                    " - capped" if truncated else ""))
    finally:
        try:
            connection.close()
        except Exception:               # noqa: BLE001 - nothing useful to do
            pass

    return data, problems


def _read_one(connection, target, max_rows, table_name, note):
    """One table's columns and rows. Returns (columns, rows, truncated)."""
    # LIMIT is asked for one row past the cap so a table that lands exactly on
    # it is not reported as truncated when it is complete.
    statement = "SELECT * FROM %s" % target
    if max_rows is not None:
        statement += " LIMIT %d" % (int(max_rows) + 1)

    note("[snowflake] Reading %s…" % target)
    cursor = connection.cursor()
    try:
        cursor.execute(statement)
        columns = [description[0] for description in (cursor.description or [])]
        rows = []
        while True:
            batch = cursor.fetchmany(FETCH_SIZE)
            if not batch:
                break
            rows.extend([_row_values(record) for record in batch])
            if max_rows is not None and len(rows) > max_rows:
                break
    finally:
        try:
            cursor.close()
        except Exception:               # noqa: BLE001
            pass

    truncated = max_rows is not None and len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    return columns, rows, truncated


def _row_values(record):
    """
    One row as the model's readers expect it: text, with None left as None.

    Everything downstream types columns from evidence, and does so from
    strings, so a Decimal or a datetime is rendered here rather than left as a
    Python object the JSON writer would refuse.
    """
    return [_cell_value(value) for value in record]


def _cell_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        # A key like 1013 must not become '1013.0' and stop matching its lookup.
        return str(int(value))
    return str(value)
