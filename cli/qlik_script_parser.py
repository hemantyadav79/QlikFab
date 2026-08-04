"""
Qlik Load Script Parser
=======================
Parses a Qlik Sense load script into a structured table/field/source model.

The load script is the only universally reliable description of an app's real
schema. The data-model blob inside a .qvf carries field tags and cardinality,
but only for apps saved by recent Qlik versions -- older apps (e.g. Executive
Dashboard) yield bare field names with no tags at all. The script, by contrast,
always states which tables exist, which fields they carry, and where the rows
actually come from.

Nothing in this module invents data. Where the script is ambiguous the parser
records what it saw and leaves resolution to the caller.
"""

import re
from dataclasses import dataclass, field as dc_field

# Statements that are control flow / configuration rather than table loads.
_NON_LOAD_PREFIXES = (
    "set", "let", "for", "next", "if", "else", "endif", "do", "loop", "exit",
    "sub", "end sub", "call", "rename", "drop", "unqualify", "qualify",
    "tag field", "untag field", "alias", "comment", "star is", "sleep",
    "trace", "declare", "derive", "search", "binary", "directory", "connect",
    "store", "switch", "case", "default", "end switch", "when", "unless",
)

# Format specifiers Qlik writes after a FROM clause, mapped to a coarse kind.
_FORMAT_KINDS = {
    "qvd": "qvd",
    "txt": "delimited",
    "csv": "delimited",
    "biff": "excel",
    "ooxml": "excel",
    "xml": "xml",
    "json": "json",
    "fix": "fixed",
    "dif": "delimited",
    "html": "html",
    "parquet": "parquet",
}

# Extension fallback when no explicit format specifier is present.
_EXT_KINDS = {
    ".qvd": "qvd",
    ".csv": "delimited",
    ".txt": "delimited",
    ".tab": "delimited",
    ".xls": "excel",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xml": "xml",
    ".json": "json",
    ".parquet": "parquet",
}


@dataclass
class QlikSource:
    """Where a table's rows come from."""

    kind: str = "unknown"          # qvd | delimited | excel | inline | resident | sql | unknown
    raw: str = ""                  # the FROM clause exactly as written
    path: str = ""                 # resolved path with $(vars) substituted
    connection: str = ""           # lib:// connection name, if any
    relative_path: str = ""        # path beneath the lib:// connection
    options: dict = dc_field(default_factory=dict)
    resident_table: str = ""
    inline_text: str = ""

    @property
    def is_file(self) -> bool:
        return self.kind in ("qvd", "delimited", "excel", "xml", "json", "parquet")


@dataclass
class QlikField:
    """A single field produced by a LOAD statement."""

    name: str                      # the name the field has after loading (alias wins)
    expression: str = ""           # what appeared before AS, verbatim
    is_derived: bool = False       # True when expression is not a bare column reference
    tags: list = dc_field(default_factory=list)   # from an inline Tagged (...) clause


@dataclass
class QlikTable:
    """A table produced by one LOAD statement."""

    name: str
    fields: list = dc_field(default_factory=list)
    source: QlikSource = dc_field(default_factory=QlikSource)
    is_mapping: bool = False
    is_hidden: bool = False        # leading-underscore helper tables
    statement: str = ""

    @property
    def field_names(self) -> list:
        return [f.name for f in self.fields]


# ----------------------------------------------------------------------
# Lexical cleanup
# ----------------------------------------------------------------------

def strip_comments(script: str) -> str:
    """
    Remove //, /* */ and REM comments without corrupting string literals.

    Walks the text once tracking quote state, because Qlik paths routinely
    contain '//' (as in lib://DataFiles/x.csv) and a naive regex would
    truncate every one of them.
    """
    out = []
    i = 0
    n = len(script)
    quote = None
    while i < n:
        ch = script[i]

        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue

        # Bracketed identifiers may contain anything; pass them through whole.
        if ch == "[":
            end = script.find("]", i)
            if end == -1:
                out.append(ch)
                i += 1
                continue
            out.append(script[i:end + 1])
            i = end + 1
            continue

        if script.startswith("/*", i):
            end = script.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue

        if script.startswith("//", i):
            end = script.find("\n", i)
            i = n if end == -1 else end
            continue

        if (script.startswith("REM ", i) or script.startswith("rem ", i)) and (
            i == 0 or script[i - 1] in "\n\r;"
        ):
            end = script.find(";", i)
            i = n if end == -1 else end + 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def collect_variables(script: str) -> dict:
    """
    Collect SET/LET assignments so $(var) references in paths can be resolved.

    LET evaluates its right-hand side in Qlik; we cannot execute that, so only
    literal assignments are kept. Anything computed is skipped rather than
    guessed at.
    """
    variables = {}
    for match in re.finditer(
        r"^\s*(SET|LET)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]*);",
        script,
        re.IGNORECASE | re.MULTILINE,
    ):
        keyword, name, value = match.group(1), match.group(2), match.group(3).strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Only keep values that are plainly literal.
        if keyword.upper() == "SET" or not re.search(r"[()$]", value):
            variables[name.lower()] = value
    return variables


def expand_variables(text: str, variables: dict, depth: int = 0) -> str:
    """Substitute $(var) references, leaving unknown ones intact."""
    if depth > 5 or "$(" not in text:
        return text

    def replace(match):
        key = match.group(1).strip().lower()
        return variables.get(key, match.group(0))

    expanded = re.sub(r"\$\(([^()]*)\)", replace, text)
    if expanded == text:
        return expanded
    return expand_variables(expanded, variables, depth + 1)


def split_statements(script: str) -> list:
    """Split on semicolons that sit outside quotes and brackets."""
    statements = []
    current = []
    quote = None
    bracket = 0

    for ch in script:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            continue
        if ch == "[":
            bracket += 1
            current.append(ch)
            continue
        if ch == "]":
            bracket = max(0, bracket - 1)
            current.append(ch)
            continue
        if ch == ";" and bracket == 0:
            statements.append("".join(current))
            current = []
            continue
        current.append(ch)

    if current:
        statements.append("".join(current))

    return [s.strip() for s in statements if s.strip()]


def split_top_level(text: str, separator: str = ",") -> list:
    """Split on a separator that is not inside quotes, brackets or parentheses."""
    parts = []
    current = []
    quote = None
    depth = 0

    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            continue
        if ch in "([":
            depth += 1
            current.append(ch)
            continue
        if ch in ")]":
            depth = max(0, depth - 1)
            current.append(ch)
            continue
        if ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)

    if current:
        parts.append("".join(current))

    return [p.strip() for p in parts if p.strip()]


# ----------------------------------------------------------------------
# Field list parsing
# ----------------------------------------------------------------------

def _unquote_identifier(text: str) -> str:
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


def parse_field_list(body: str) -> list:
    """
    Parse the comma-separated field list of a LOAD statement.

    Each entry is either a bare reference ([Sales], Sales) or an expression
    with an alias (Sum(x) AS [Total], Alias AS [__Country]). A trailing
    Tagged ('$date') clause is captured rather than discarded, since those
    tags are a genuine type signal.
    """
    fields = []

    for entry in split_top_level(body, ","):
        entry = entry.strip()
        if not entry or entry == "*":
            continue

        tags = []
        tag_match = re.search(r"\bTagged\s*\(([^)]*)\)\s*$", entry, re.IGNORECASE)
        if tag_match:
            tags = [
                _unquote_identifier(t) for t in split_top_level(tag_match.group(1), ",")
            ]
            entry = entry[: tag_match.start()].strip()

        if not entry:
            continue

        # Find a top-level " AS " separating expression from alias.
        alias = None
        expression = entry
        depth = 0
        quote = None
        for match in re.finditer(r"[\(\)\[\]'\"]|\bAS\b", entry, re.IGNORECASE):
            token = match.group(0)
            if quote:
                if token == quote:
                    quote = None
                continue
            if token in ("'", '"'):
                quote = token
                continue
            if token in "([":
                depth += 1
                continue
            if token in ")]":
                depth = max(0, depth - 1)
                continue
            if depth == 0 and token.upper() == "AS":
                expression = entry[: match.start()].strip()
                alias = entry[match.end():].strip()
                break

        if alias:
            name = _unquote_identifier(alias)
            bare = _unquote_identifier(expression)
            is_derived = not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ .]*", bare or "")
        else:
            name = _unquote_identifier(expression)
            is_derived = False

        if not name:
            continue

        fields.append(
            QlikField(
                name=name,
                expression=expression.strip(),
                is_derived=is_derived,
                tags=tags,
            )
        )

    return fields


# ----------------------------------------------------------------------
# Source parsing
# ----------------------------------------------------------------------

def _parse_format_options(spec: str) -> dict:
    """Turn '(txt, codepage is 28591, embedded labels, delimiter is ',', msq)' into a dict."""
    options = {}
    if not spec:
        return options

    for part in split_top_level(spec, ","):
        part = part.strip()
        if not part:
            continue
        kv = re.match(r"(.+?)\s+is\s+(.+)", part, re.IGNORECASE)
        if kv:
            key = kv.group(1).strip().lower().replace(" ", "_")
            options[key] = _unquote_identifier(kv.group(2))
        else:
            options[part.lower().replace(" ", "_")] = True

    return options


def parse_source(tail: str, variables: dict) -> QlikSource:
    """Parse everything after the field list: FROM / RESIDENT / INLINE / SQL."""
    tail = tail.strip()
    source = QlikSource(raw=tail)

    if not tail:
        return source

    inline = re.match(r"INLINE\s*\[(.*)\]\s*(?:\(([^)]*)\))?\s*$", tail, re.IGNORECASE | re.DOTALL)
    if inline:
        source.kind = "inline"
        source.inline_text = inline.group(1)
        source.options = _parse_format_options(inline.group(2) or "")
        return source

    resident = re.match(r"RESIDENT\s+(.+)$", tail, re.IGNORECASE | re.DOTALL)
    if resident:
        source.kind = "resident"
        source.resident_table = _unquote_identifier(resident.group(1).strip())
        return source

    if re.match(r"(SQL\s+)?SELECT\b", tail, re.IGNORECASE):
        source.kind = "sql"
        return source

    from_match = re.match(r"FROM\s+(.+)$", tail, re.IGNORECASE | re.DOTALL)
    if not from_match:
        return source

    remainder = from_match.group(1).strip()

    # A LOAD may end with WHERE / GROUP BY / ORDER BY / WHILE after the format
    # specifier; drop it so the specifier is the last thing on the line.
    filter_match = re.search(
        r"\b(WHERE|GROUP\s+BY|ORDER\s+BY|WHILE)\b", remainder, re.IGNORECASE
    )
    if filter_match:
        source.options["filter"] = remainder[filter_match.start():].strip()
        remainder = remainder[: filter_match.start()].strip()

    # Trailing "(qvd)" / "(txt, delimiter is ',')" format specifier.
    spec = ""
    spec_match = re.search(r"\(([^()]*)\)\s*$", remainder)
    if spec_match:
        candidate = spec_match.group(1)
        first_token = candidate.split(",")[0].strip().lower()
        if first_token in _FORMAT_KINDS:
            spec = candidate
            remainder = remainder[: spec_match.start()].strip()

    path = _unquote_identifier(remainder.strip())
    path = expand_variables(path, variables).strip()

    source.path = path
    source.options.update(_parse_format_options(spec))

    if spec:
        first_token = spec.split(",")[0].strip().lower()
        source.kind = _FORMAT_KINDS.get(first_token, "unknown")
    else:
        ext = re.search(r"(\.[A-Za-z0-9]+)\s*$", path)
        source.kind = _EXT_KINDS.get(ext.group(1).lower(), "unknown") if ext else "unknown"

    lib_match = re.match(r"lib://([^/\\]+)[/\\](.*)$", path, re.IGNORECASE)
    if lib_match:
        source.connection = lib_match.group(1).strip()
        source.relative_path = lib_match.group(2).strip()
    else:
        source.relative_path = path

    return source


# ----------------------------------------------------------------------
# Statement parsing
# ----------------------------------------------------------------------

_LOAD_SPLIT = re.compile(r"\b(FROM|RESIDENT|INLINE|SQL\s+SELECT|SELECT)\b", re.IGNORECASE)


def _parse_load_statement(statement: str, variables: dict, fallback_index: int) -> QlikTable | None:
    """Parse a single '[Label]: LOAD field, field FROM source' statement."""
    text = statement.strip()

    label = ""
    label_match = re.match(r"^\s*(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_ .]*)\s*:\s*", text)
    if label_match:
        label = _unquote_identifier(label_match.group(1))
        text = text[label_match.end():].strip()

    load_match = re.match(
        r"^(?:(MAPPING|ADD|REPLACE|BUFFER|CONCATENATE(?:\s*\([^)]*\))?)\s+)*LOAD\b(.*)$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not load_match:
        return None

    is_mapping = bool(re.match(r"^\s*MAPPING\b", text, re.IGNORECASE))
    body = load_match.group(2)

    # "LOAD DISTINCT field, ..." -- the qualifier is not a field.
    body = re.sub(r"^\s*DISTINCT\b", "", body, count=1, flags=re.IGNORECASE)

    # Split the field list from the source clause at the first top-level keyword.
    field_text = body
    tail = ""
    depth = 0
    quote = None
    for match in _LOAD_SPLIT.finditer(body):
        prefix = body[: match.start()]
        depth = prefix.count("(") - prefix.count(")")
        if depth <= 0:
            field_text = body[: match.start()]
            tail = body[match.start():]
            break

    fields = parse_field_list(field_text)
    source = parse_source(tail, variables)

    # An unlabelled LOAD takes its name from the file it reads, which is what
    # Qlik itself does. This also lets repeated loads of one master file merge
    # into a single table instead of becoming Table_20, Table_22, ...
    name = label
    if not name and source.is_file and source.relative_path:
        stem = re.split(r"[/\\]", source.relative_path)[-1]
        name = re.sub(r"\.[A-Za-z0-9]+$", "", stem).strip()
    if not name:
        name = source.resident_table or f"Table_{fallback_index}"

    return QlikTable(
        name=name,
        fields=fields,
        source=source,
        is_mapping=is_mapping,
        is_hidden=name.startswith("__"),
        statement=statement.strip(),
    )


def parse_load_script(script: str) -> list:
    """
    Parse a Qlik load script into QlikTable objects.

    Mapping tables and leading-underscore helper tables are returned too, but
    flagged, so callers can decide whether they belong in the target model.
    """
    if not script:
        return []

    cleaned = strip_comments(script)
    variables = collect_variables(cleaned)

    tables = []
    for index, statement in enumerate(split_statements(cleaned)):
        stripped = statement.strip().lower()

        # Skip obvious non-LOAD statements early.
        if any(stripped.startswith(prefix) for prefix in _NON_LOAD_PREFIXES):
            continue
        if not re.search(r"\bLOAD\b", statement, re.IGNORECASE):
            continue
        # DECLARE FIELD DEFINITION blocks describe derived calendar fields,
        # not a table of rows.
        if re.search(r"\bDECLARE\s+FIELD\s+DEFINITION\b", statement, re.IGNORECASE):
            continue

        table = _parse_load_statement(statement, variables, index)
        if table and table.fields:
            tables.append(table)

    return _merge_concatenated(tables)


def _merge_concatenated(tables: list) -> list:
    """
    Fold repeated loads into the same table.

    Qlik auto-concatenates consecutive loads that share a table name, and
    scripts frequently reload the same master table from several files.
    Emitting duplicates would produce duplicate Power BI tables.
    """
    merged = {}
    order = []

    for table in tables:
        key = table.name.lower()
        if key not in merged:
            merged[key] = table
            order.append(key)
            continue

        existing = merged[key]
        known = {f.name.lower() for f in existing.fields}
        for f in table.fields:
            if f.name.lower() not in known:
                existing.fields.append(f)
                known.add(f.name.lower())
        # Prefer a concrete file source over an unknown one.
        if existing.source.kind == "unknown" and table.source.kind != "unknown":
            existing.source = table.source

    return [merged[k] for k in order]
