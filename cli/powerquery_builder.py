"""
Power Query (M) Builder
=======================
Turns parsed Qlik tables into real Power Query expressions and resolves column
data types from evidence rather than from column names.

Two rules govern this module:

1. No invented rows. A table is either wired to the source the Qlik script
   actually named, or it is emitted with its correct schema and zero rows plus
   a note explaining why. Placeholder data that looks like real data is worse
   than no data, because it fails silently.

2. No type guessing from names. A column is numeric only when Qlik's own
   metadata says so. Falling back to text is safe; falling back to a number
   produces measures that error at refresh.
"""

import re

# Power BI / Tabular data types.
TYPE_STRING = "string"
TYPE_INT64 = "int64"
TYPE_DOUBLE = "double"
TYPE_DATETIME = "dateTime"
TYPE_BOOLEAN = "boolean"

# Tabular type -> Power Query type literal, in *expression* position: the
# {"Column", <type>} pairs handed to Table.TransformColumnTypes. The `type`
# keyword is required there.
_M_TYPES = {
    TYPE_STRING: "type text",
    TYPE_INT64: "Int64.Type",
    TYPE_DOUBLE: "type number",
    TYPE_DATETIME: "type datetime",
    TYPE_BOOLEAN: "type logical",
}

# The same types in *field-specification* position -- the `[Name = <type>]`
# entries of a `type table [...]` row type.
#
# These are NOT interchangeable with the above. A field specification's type is
# parsed as a primary expression, so a bare primitive name (`text`) or a type
# value (`Int64.Type`) is accepted while the `type text` keyword form is not.
# Emitting `type text` here produced a document the mashup engine rejected with
# "Token ',' expected" -- it had failed to parse the field's type and so read
# the field list as unterminated. The message named neither the table nor the
# column, and the same literal is correct one line away in a
# Table.TransformColumnTypes call, which is what made this expensive to find.
_M_FIELD_TYPES = {
    TYPE_STRING: "text",
    TYPE_INT64: "Int64.Type",
    TYPE_DOUBLE: "number",
    TYPE_DATETIME: "datetime",
    TYPE_BOOLEAN: "logical",
}

# Qlik system field tags that carry type information.
_DATE_TAGS = {"$date", "$timestamp"}
_NUMERIC_TAGS = {"$numeric"}
_INTEGER_TAGS = {"$integer"}
_TEXT_TAGS = {"$text", "$ascii"}


def escape_m_string(value: str) -> str:
    """
    Escape a value for embedding in an M string literal or quoted identifier.

    M has exactly one escape mechanism inside quoted text: `#(...)`. Doubling
    the quote character is therefore not sufficient on its own --

      * `#(` opens an escape sequence, so a literal `#` that happens to be
        followed by `(` has to be written `#(#)`. Qlik field names like
        `Revenue #(000s)` otherwise terminate the literal early and the mashup
        parser reports a bare "Token ',' expected" from somewhere further down
        the document.
      * a raw CR, LF or TAB splits the token across lines, with the same result.

    The `#(` substitution runs first so it cannot re-escape the `#(cr)` /
    `#(lf)` / `#(tab)` sequences introduced immediately after it.
    """
    text = str(value).replace('"', '""')
    text = text.replace("#(", "#(#)(")
    return (
        text.replace("\r", "#(cr)")
            .replace("\n", "#(lf)")
            .replace("\t", "#(tab)")
    )


def escape_m_identifier(name: str) -> str:
    """
    Quote an identifier for M (#"Name with spaces").

    Always quoted, to avoid collisions with reserved keywords. An empty name is
    not a legal M identifier even when quoted, so it is replaced rather than
    emitted as `#""` -- a column with no name cannot be referenced anyway, and
    a placeholder keeps the failure visible in the model instead of breaking
    the whole mashup document.
    """
    escaped = escape_m_string(name)
    return '#"%s"' % (escaped if escaped else "Column")


# ----------------------------------------------------------------------
# Type resolution
# ----------------------------------------------------------------------

class TypeResolver:
    """
    Resolves a column's data type from the strongest available evidence.

    Priority:
      1. Field tags from the .qvf data model ($numeric, $integer, $date, ...)
      2. The data model's qis_numeric flag
      3. Tagged (...) clauses written in the load script
      4. Text

    Column names are deliberately never consulted. The previous implementation
    matched substrings such as "id" and "count", which typed `show_id` and
    `country` as numeric and generated SUM measures that fail in Power BI.
    """

    def __init__(self, extracted_fields: list = None, script_tables: list = None):
        self._by_name = {}

        for field in extracted_fields or []:
            name = (field.get("name") or "").strip()
            if name:
                self._by_name[name.lower()] = {
                    "tags": [str(t).lower() for t in field.get("tags", [])],
                    "is_numeric": bool(field.get("is_numeric")),
                    "cardinality": field.get("cardinality", 0),
                    "has_metadata": True,
                }

        # Script-level Tagged (...) clauses fill gaps for apps whose data-model
        # blob carries no tags at all (older Qlik versions).
        for table in script_tables or []:
            for field in table.fields:
                key = field.name.lower()
                if not field.tags:
                    continue
                entry = self._by_name.setdefault(
                    key, {"tags": [], "is_numeric": False, "cardinality": 0, "has_metadata": False}
                )
                entry["tags"] = list(
                    {*entry.get("tags", []), *[str(t).lower() for t in field.tags]}
                )

    def has_metadata(self, column_name: str) -> bool:
        entry = self._by_name.get((column_name or "").lower())
        return bool(entry and entry.get("has_metadata"))

    def resolve(self, column_name: str) -> str:
        entry = self._by_name.get((column_name or "").lower())
        if not entry:
            return TYPE_STRING

        tags = set(entry.get("tags", []))

        if tags & _DATE_TAGS:
            return TYPE_DATETIME
        if tags & _TEXT_TAGS and not (tags & _NUMERIC_TAGS):
            return TYPE_STRING
        if tags & _INTEGER_TAGS:
            return TYPE_INT64
        if tags & _NUMERIC_TAGS:
            return TYPE_DOUBLE
        if entry.get("is_numeric"):
            return TYPE_DOUBLE

        return TYPE_STRING

    def is_measurable(self, column_name: str) -> bool:
        """
        True when aggregating the column with SUM/AVERAGE is meaningful.

        Dates and text are excluded. So are columns whose every value is
        distinct, which are keys rather than quantities.
        """
        if self.resolve(column_name) not in (TYPE_INT64, TYPE_DOUBLE):
            return False
        if re.search(r"(^%)|(\bkey\b)|(_key$)|(\bid\b)|(_id$)", column_name or "", re.IGNORECASE):
            return False
        return True


# ----------------------------------------------------------------------
# Power Query generation
# ----------------------------------------------------------------------

# Qlik codepage numbers Power Query understands directly.
_KNOWN_CODEPAGES = {"1252", "65001", "28591", "28592", "1200", "1201", "437", "850"}

ROOT_PARAMETER = "DataSourceRoot"


def build_root_parameter_expression(default_root: str) -> list:
    """
    The single text parameter every file-backed query resolves its path against.

    Qlik lib:// connections are logical names with no filesystem meaning outside
    Qlik, so the migrated model exposes one folder parameter the user points at
    their exported data.
    """
    return [
        '"%s" meta [' % escape_m_string(default_root),
        '    IsParameterQuery = true,',
        '    Type = "Text",',
        '    IsParameterQueryRequired = true',
        ']',
    ]


def _type_transform_list(columns: list, resolver: TypeResolver) -> str:
    parts = []
    for name in columns:
        m_type = _M_TYPES.get(resolver.resolve(name), "type text")
        parts.append('{"%s", %s}' % (escape_m_string(name), m_type))
    return "{" + ", ".join(parts) + "}"


def _schema_type_literal(columns: list, resolver: TypeResolver) -> str:
    parts = []
    for name in columns:
        m_type = _M_FIELD_TYPES.get(resolver.resolve(name), "text")
        parts.append("%s = %s" % (escape_m_identifier(name), m_type))
    return "type table [" + ", ".join(parts) + "]"


def _path_expression(source) -> str:
    """Build the M expression for a file path, relative to the root parameter."""
    relative = (source.relative_path or source.path or "").replace("\\", "/").lstrip("/")
    return '%s & "%s"' % (ROOT_PARAMETER, escape_m_string(relative))


def source_columns(table) -> list:
    """
    Columns that physically exist in the source file.

    Fields computed in the load script (ApplyMap, If, date arithmetic) have no
    counterpart in the file, so selecting them would produce an all-null column.
    They are dropped here and left for the user to rebuild in Power Query.
    """
    physical = [f.name for f in table.fields if not f.is_derived]
    return physical or [f.name for f in table.fields]


def dropped_columns(table) -> list:
    """
    Script-computed fields that could not be carried into Power Query.

    These matter: some are join keys built by concatenation, so losing them
    also loses a relationship. They are reported rather than silently omitted.
    """
    physical = [f.name for f in table.fields if not f.is_derived]
    if not physical:
        return []
    return [
        {"name": f.name, "expression": f.expression}
        for f in table.fields if f.is_derived
    ]


def _build_delimited(table, resolver: TypeResolver) -> list:
    source = table.source
    options = source.options or {}

    # A tab delimiter is emitted as the M escape sequence itself, so it goes
    # into the literal verbatim -- running it through escape_m_string would
    # turn `#(tab)` into the four-character text `#(tab)`. Every other
    # delimiter is user data and is escaped normally.
    delimiter = options.get("delimiter", ",")
    if delimiter is True:
        delimiter = ","
    delimiter = str(delimiter)
    tab_escape = {"\\t": "#(tab)", "tab": "#(tab)", "\t": "#(tab)"}
    delimiter = tab_escape.get(delimiter.lower()) or escape_m_string(delimiter)

    codepage = str(options.get("codepage", "")).strip()
    encoding = ", Encoding = %s" % codepage if codepage in _KNOWN_CODEPAGES else ""

    columns = source_columns(table)
    lines = [
        "let",
        "    /* Migrated from Qlik: %s */" % comment_safe(source.path or source.relative_path, 200),
        "    Source = Csv.Document(",
        "        File.Contents(%s)," % _path_expression(source),
        '        [Delimiter = "%s", QuoteStyle = QuoteStyle.Csv%s]'
        % (delimiter, encoding),
        "    ),",
    ]

    if options.get("embedded_labels"):
        lines.append("    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),")
        current = "Promoted"
    else:
        current = "Source"

    lines.append(
        "    Selected = Table.SelectColumns(%s, {%s}, MissingField.UseNull),"
        % (current, ", ".join('"%s"' % escape_m_string(c) for c in columns))
    )
    lines.append(
        "    Typed = Table.TransformColumnTypes(Selected, %s)"
        % _type_transform_list(columns, resolver)
    )
    lines.append("in")
    lines.append("    Typed")
    return lines


def _build_excel(table, resolver: TypeResolver) -> list:
    source = table.source
    options = source.options or {}

    sheet = options.get("table", "")
    if sheet is True:
        sheet = ""
    sheet = str(sheet).strip().rstrip("$")

    columns = source_columns(table)
    lines = [
        "let",
        "    /* Migrated from Qlik: %s */" % comment_safe(source.path or source.relative_path, 200),
        "    Workbook = Excel.Workbook(File.Contents(%s), null, true),"
        % _path_expression(source),
    ]

    if sheet:
        lines.append(
            '    Sheet = Workbook{[Item = "%s", Kind = "Sheet"]}[Data],' % escape_m_string(sheet)
        )
    else:
        lines.append('    Sheet = Workbook{0}[Data],')

    if options.get("embedded_labels"):
        lines.append("    Promoted = Table.PromoteHeaders(Sheet, [PromoteAllScalars = true]),")
        current = "Promoted"
    else:
        current = "Sheet"

    lines.append(
        "    Selected = Table.SelectColumns(%s, {%s}, MissingField.UseNull),"
        % (current, ", ".join('"%s"' % escape_m_string(c) for c in columns))
    )
    lines.append(
        "    Typed = Table.TransformColumnTypes(Selected, %s)"
        % _type_transform_list(columns, resolver)
    )
    lines.append("in")
    lines.append("    Typed")
    return lines


def _build_resident(table, resolver: TypeResolver) -> list:
    source = table.source
    columns = table.field_names
    return [
        "let",
        "    /* Qlik RESIDENT load from table: %s */" % comment_safe(source.resident_table, 200),
        "    Source = %s," % escape_m_identifier(source.resident_table),
        "    Selected = Table.SelectColumns(Source, {%s}, MissingField.UseNull)"
        % ", ".join('"%s"' % escape_m_string(c) for c in columns if not _is_derived(table, c)),
        "in",
        "    Selected",
    ]


def _is_derived(table, column_name: str) -> bool:
    for f in table.fields:
        if f.name == column_name:
            return f.is_derived
    return False


def _inline_rows(table) -> list:
    """Split an INLINE block into a list of cell lists."""
    source = table.source
    raw = (source.inline_text or "").strip("\r\n")
    delimiter = (source.options or {}).get("delimiter", ",")
    if delimiter is True or not delimiter:
        delimiter = ","
    delimiter = str(delimiter)

    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        rows.append([c.strip().strip("'\"") for c in line.split(delimiter)])
    return rows


def _inline_header(table) -> list:
    rows = _inline_rows(table)
    return rows[0] if rows else []


def _build_inline(table, resolver: TypeResolver) -> list:
    """INLINE data is real data written into the script, so it migrates verbatim."""
    source = table.source
    raw = (source.inline_text or "").strip("\r\n")
    delimiter = str((source.options or {}).get("delimiter", ",")) or ","
    if delimiter is True:
        delimiter = ","

    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        cells = [c.strip().strip("'\"") for c in line.split(delimiter)]
        rows.append(cells)

    if not rows:
        return _build_unavailable(table, resolver, "INLINE block contained no rows.")

    header = [h for h in rows[0]]
    body = rows[1:]

    row_literals = []
    for row in body:
        padded = (row + [""] * len(header))[: len(header)]
        row_literals.append("{" + ", ".join('"%s"' % escape_m_string(c) for c in padded) + "}")

    return [
        "let",
        "    /* Qlik INLINE data, migrated verbatim (%d rows) */" % len(body),
        "    Source = #table({%s}, {%s}),"
        % (
            ", ".join('"%s"' % escape_m_string(h) for h in header),
            ", ".join(row_literals),
        ),
        "    Typed = Table.TransformColumnTypes(Source, %s)"
        % _type_transform_list(header, resolver),
        "in",
        "    Typed",
    ]


def comment_safe(value: str, limit: int = 500) -> str:
    """
    Flatten a value so it cannot escape the `//` comment it is written into.

    An M line comment ends at the newline. Interpolating a multi-line value --
    a native SQL SELECT, a Tableau custom-SQL relation, a wrapped error message
    -- therefore ends the comment early and leaves the remaining lines to be
    parsed as code. The mashup engine reports that as a bare "Token ',' expected"
    pointing at a line the author never wrote, which is close to impossible to
    diagnose from the message alone.

    Length is capped as well: these values can be a whole query, and a comment
    that runs for pages helps nobody reading the generated M.
    """
    flattened = " ".join(str(value).split())
    if len(flattened) > limit:
        flattened = flattened[:limit].rstrip() + " ... (truncated)"
    return flattened or "(unknown)"


def unavailable_description(table) -> str:
    """The explanation for a schema-only table, as prose for its description.

    This used to be a fourteen-line `//` comment banner inside the query
    itself. It was moved out because a mashup document is assembled from every
    partition's M, and anything that disturbs a line boundary turns a `//`
    comment into a swallowed query -- a failure Fabric reports only as
    "Token ',' expected", naming neither the table nor the line.

    A table description carries the same information where the user is more
    likely to read it (Power BI shows it in the field list), and cannot break
    the parser no matter what it contains.
    """
    source = table.source
    original = source.path or source.relative_path or source.raw or "(unknown)"
    # Derived here rather than passed in, so the reason table stays private to
    # this module and callers cannot drift out of step with it.
    reason = _UNAVAILABLE_REASONS.get(source.kind, _UNAVAILABLE_REASONS["unknown"])
    return (
        "SOURCE NOT AUTOMATICALLY MIGRATABLE. %s Original source: %s. "
        "The schema is the real schema read from the source app; no sample rows "
        "are generated, because fabricated data would render charts that look "
        "correct but mean nothing. To finish this table, replace the Source step "
        "with a connector for the upstream system, or export the source to CSV "
        "and point the %s parameter at it."
        % (comment_safe(reason, 300), comment_safe(original, 200), ROOT_PARAMETER)
    )


def _build_unavailable(table, resolver: TypeResolver, reason: str) -> list:
    """
    Emit the correct schema with zero rows.

    Used when the source cannot be reached from Power Query -- overwhelmingly
    QVD files, which are a closed Qlik format. The table loads, the model is
    structurally complete, and the report opens; the visuals are simply empty
    until the user re-points the query. That is the honest outcome.

    The query itself is deliberately minimal and carries no comments: see
    `unavailable_description` for where the explanation went, and why.
    """
    return [
        "let",
        "    Schema = #table(",
        "        %s," % _schema_type_literal(table.field_names, resolver),
        "        {}",
        "    )",
        "in",
        "    Schema",
    ]


_UNAVAILABLE_REASONS = {
    "qvd": "Qlik QVD is a proprietary format that Power Query cannot read.",
    "sql": "The Qlik script used a native SQL SELECT against a Qlik data connection; "
           "the connection string is not stored in the .qvf.",
    "unknown": "The Qlik script did not state a source this tool could identify.",
    "xml": "XML sources need a per-file navigation path that cannot be inferred.",
    "json": "JSON sources need a per-file navigation path that cannot be inferred.",
}


def build_partition_expression(table, resolver: TypeResolver) -> tuple:
    """
    Build the M expression for one table.

    Returns (expression_lines, status, columns). The column list is what the
    query genuinely emits, so the caller can declare exactly those columns in
    model.bim -- declaring a column the partition does not produce makes the
    model fail to load.

    status is 'connected' when the query reads the real source, 'schema-only'
    when the source is unreachable and the table is emitted empty.
    """
    kind = table.source.kind

    if kind == "delimited":
        return _build_delimited(table, resolver), "connected", source_columns(table)
    if kind == "excel":
        return _build_excel(table, resolver), "connected", source_columns(table)
    if kind == "resident":
        return _build_resident(table, resolver), "connected", source_columns(table)
    if kind == "inline":
        columns = _inline_header(table) or table.field_names
        return _build_inline(table, resolver), "connected", columns

    reason = _UNAVAILABLE_REASONS.get(kind, _UNAVAILABLE_REASONS["unknown"])
    return _build_unavailable(table, resolver, reason), "schema-only", table.field_names


# ----------------------------------------------------------------------
# Rows read from the Qlik engine, carried in the model itself
# ----------------------------------------------------------------------

def _parses_as(value, tabular_type) -> bool:
    """Whether one non-null value can be written as `tabular_type` losslessly."""
    text = str(value).strip()
    try:
        if tabular_type == TYPE_INT64:
            int(text)
        elif tabular_type == TYPE_DOUBLE:
            float(text)
        else:
            return True
    except (TypeError, ValueError):
        return False
    return True


def embedded_column_types(columns: list, rows: list, resolver: TypeResolver) -> dict:
    """
    The type each embedded column can actually be written as.

    The resolver says what the .qvf claimed. This says what the rows support.
    A column the migration typed numeric but which holds one unparseable value
    is written as text for its whole length, because the alternative -- coercing
    that value to null -- turns a visible wrong value into invisible missing
    data. The disagreement is the caller's to report.

    QIX hands every value over as text (Qlik's own formatted representation),
    so a thousands separator or a currency symbol is exactly how a genuinely
    numeric column ends up here as text. That is a fidelity loss worth naming
    rather than papering over.
    """
    effective = {}
    for index, name in enumerate(columns):
        declared = resolver.resolve(name)
        if declared not in (TYPE_INT64, TYPE_DOUBLE):
            effective[name] = TYPE_STRING if declared != TYPE_STRING else TYPE_STRING
            continue
        for row in rows:
            value = row[index] if index < len(row) else None
            if value is None or value == "":
                continue
            if not _parses_as(value, declared):
                declared = TYPE_STRING
                break
        effective[name] = declared
    return effective


def _m_value(value, tabular_type) -> str:
    """One cell as an M literal."""
    if value is None or value == "":
        return "null"
    if tabular_type == TYPE_INT64:
        return str(int(str(value).strip()))
    if tabular_type == TYPE_DOUBLE:
        return repr(float(str(value).strip()))
    return '"%s"' % escape_m_string(value)


def build_embedded_expression(table_name: str, columns: list, rows: list,
                              resolver: TypeResolver, truncated: bool = False) -> list:
    """
    An M query that carries the rows themselves, as a #table literal.

    Why the rows travel inside the model at all: the Fabric service cannot open
    a file path on the machine that ran the migration, so a query pointing at
    the original CSV publishes as an empty table. Rows read from the Qlik engine
    are the only thing that survives the trip.

    The declared type literal is built from `embedded_column_types`, not from
    the resolver directly, so the table's declared schema always matches the
    values written beneath it. Declaring Int64 over a value written as text is
    not a cosmetic mismatch -- the mashup engine fails the whole document.
    """
    types = embedded_column_types(columns, rows, resolver)
    schema = "type table [" + ", ".join(
        "%s = %s" % (escape_m_identifier(name), _M_FIELD_TYPES.get(types[name], "text"))
        for name in columns
    ) + "]"

    # Block comments, not `//`. These sit ahead of `let`, so if anything ever
    # collapses a line boundary a line comment would swallow the entire query
    # rather than one line of it.
    lines = [
        "/* %d row(s) read from the Qlik engine for %s. */"
        % (len(rows), comment_safe(table_name, 120)),
    ]
    if truncated:
        lines.append(
            "/* Capped by the embedded-size budget; this is not the whole table. */")
    lines += ["let", "    Source = #table("]
    lines.append("        %s," % schema)
    if not rows:
        lines.append("        {}")
    else:
        lines.append("        {")
        for position, row in enumerate(rows):
            cells = ", ".join(
                _m_value(row[i] if i < len(row) else None, types[name])
                for i, name in enumerate(columns)
            )
            comma = "" if position == len(rows) - 1 else ","
            lines.append("            {%s}%s" % (cells, comma))
        lines.append("        }")
    lines += ["    )", "in", "    Source"]
    return lines


def default_root_for(script_tables: list) -> str:
    """Pick a sensible default for the DataSourceRoot parameter."""
    for table in script_tables:
        if table.source.is_file and table.source.connection:
            return "C:\\QlikData\\%s\\" % table.source.connection
    return "C:\\QlikData\\"
