"""
Universal AI-Powered QVF to Power BI Converter
=================================================
Converts ANY Qlik Sense (.qvf) file into a Power BI / Microsoft Fabric project
(.pbip) and a standalone template (.pbit).

What actually gets migrated:
  * REAL DATA    - the rows loaded in the Qlik app are decoded straight out of
                   the .qvf (see qvf_data_reader) and embedded in the semantic
                   model, so the Power BI report shows the same numbers the
                   Qlik dashboard showed. No mock rows.
  * REAL SCHEMA  - every Qlik table becomes a Power BI table with the original
                   field names and inferred data types.
  * REAL VISUALS - every chart on every Qlik sheet is mapped to the closest
                   Power BI visual, keeping its title, its dimensions, its
                   measures, and its position on the sheet.

Qlik expressions are translated to DAX by rule, with an optional local LLM
(Ollama) consulted only for expressions the rules do not cover.

Usage:
    python ai_qvf_to_powerbi.py --qvf any_project.qvf
    python ai_qvf_to_powerbi.py --qvf any_project.qvf --model llama3.2
    python ai_qvf_to_powerbi.py --input extraction_result.json
"""

import argparse
import json
import math
import os
import re
import sys
import urllib.request
import urllib.error
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

# Configure Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# Power BI report canvas, in the units page.json / Layout use.
CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720

# How many real rows to embed in the semantic model by default. Power BI
# handles far more, but the project file has to stay a reasonable size.
DEFAULT_MAX_ROWS = 5000

# The Power Query tokenizer will not accept an arbitrarily long text literal;
# past a certain size it abandons the token and the parse fails with a
# misleading syntax error. Real field values are never anywhere near this.
MAX_TEXT_LENGTH = 8000


# ============================================================
# MODULAR AI BRAIN (OLLAMA / LLM INTERFACE)
# ============================================================

class AIConverterBrain:
    """
    Translates Qlik expressions into DAX.

    Standard aggregations are handled by rule - they are unambiguous and a
    model round-trip would only add latency and risk. Anything the rules do
    not recognise is handed to a local Ollama model when one is running.
    """

    def __init__(self, provider="ollama", model="llama3.2",
                 ollama_url="http://localhost:11434/api/generate"):
        self.provider = provider.lower()
        self.model = model
        self.ollama_url = ollama_url
        self.is_available = self._check_availability()
        self.cache = {}

    def _check_availability(self) -> bool:
        """Check if Ollama server is running locally."""
        if self.provider != "ollama":
            return True
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def translate(self, qlik_expr: str, resolver) -> tuple:
        """
        Translate one Qlik expression.

        `resolver` maps a Qlik field name to ("Table", "Column"); it is what
        keeps the generated DAX pointing at the table that actually holds the
        field, which matters as soon as an app has more than one table.

        Returns (measure_name, dax_expression).
        """
        expr = as_text(qlik_expr)
        if not expr:
            table = resolver.default_table()
            return (f"Row Count of {table}", f"COUNTROWS('{table}')")

        if expr in self.cache:
            return self.cache[expr]

        result = self._translate_by_rule(expr, resolver)
        if result is None:
            result = self._translate_by_ai(expr, resolver)
        if result is None:
            # Rather than silently reporting a row count for an expression that
            # plainly aggregates something else, fall back to the first
            # aggregation it contains - the quantity the chart is really about.
            result = QlikExpressionTranslator(resolver).first_aggregation(expr)
        if result is None:
            table = resolver.default_table()
            result = (f"Row Count of {table}", f"COUNTROWS('{table}')")

        self.cache[expr] = result
        return result

    def _translate_by_rule(self, expr: str, resolver):
        """Handle the aggregations that make up the bulk of any Qlik app."""
        translator = QlikExpressionTranslator(resolver)
        dax = translator.translate(expr)
        if not dax:
            return None
        return (translator.suggest_name(expr) or self._safe_name(expr), dax)

    def _translate_by_ai(self, expr: str, resolver):
        """Ask the local model, when one is available."""
        if not self.is_available or self.provider != "ollama":
            return None

        prompt = (
            "You are a Power BI DAX expert. Translate this Qlik Sense expression "
            "into a single DAX measure.\n"
            f"Tables and columns available:\n{resolver.describe()}\n"
            f"Qlik expression: {expr}\n\n"
            "Return ONLY a JSON object, no markdown and no explanation:\n"
            '{"measure_name": "Short descriptive name", "dax_expression": "VALID DAX"}'
        )
        try:
            print(f"  [AI] Ollama ({self.model}) translating: {expr}")
            res = self._call_ollama_json(prompt)
            if res and res.get("measure_name") and res.get("dax_expression"):
                dax = res["dax_expression"].strip().strip("=").strip()
                if resolver.validate_dax(dax):
                    return (res["measure_name"].strip(), dax)
                print("  [AI] rejected translation - it referenced unknown columns")
        except Exception as e:
            print(f"  [AI] translation unavailable for '{expr}': {e}")
        return None

    def _call_ollama_json(self, prompt: str) -> dict:
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }).encode("utf-8")
        req = urllib.request.Request(
            self.ollama_url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return json.loads(data.get("response", "{}"))

    @staticmethod
    def _safe_name(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9 ]+", " ", text).strip()[:40] or "Value"


# ============================================================
# QLIK EXPRESSION -> DAX
# ============================================================

class QlikExpressionTranslator:
    """
    Rewrites a Qlik chart expression into DAX.

    Real Qlik expressions are rarely a bare `Sum(Field)`. They compose
    aggregations with arithmetic, wrap fields in brackets, and qualify
    aggregations with set analysis. This walks the expression, rewrites each
    aggregation in place, and leaves the surrounding arithmetic intact - which
    keeps composite measures like `Sum(A) / Sum(B)` meaningful instead of
    collapsing them to a row count.
    """

    # Qlik aggregation -> (plain DAX form, row-wise DAX form, name prefix)
    AGGREGATIONS = {
        "sum": ("SUM", "SUMX", "Total"),
        "avg": ("AVERAGE", "AVERAGEX", "Average"),
        "average": ("AVERAGE", "AVERAGEX", "Average"),
        "min": ("MIN", "MINX", "Min"),
        "max": ("MAX", "MAXX", "Max"),
        "count": ("COUNTA", "COUNTX", "Count"),
        "median": ("MEDIAN", "MEDIANX", "Median"),
        "stdev": ("STDEV.P", "STDEVX.P", "Std Dev"),
        "only": ("MIN", "MINX", ""),
    }

    # Scalar functions that carry across unchanged.
    PASSTHROUGH = {
        "if": "IF", "abs": "ABS", "round": "ROUND", "ceil": "CEILING",
        "floor": "FLOOR", "sqrt": "SQRT", "len": "LEN", "upper": "UPPER",
        "lower": "LOWER", "trim": "TRIM", "year": "YEAR", "month": "MONTH",
        "day": "DAY", "date": "DATE", "rangesum": "SUM",
    }

    def __init__(self, resolver):
        self.resolver = resolver
        self._used_aggregation = None
        self._used_field = None

    # -- public ----------------------------------------------------------
    def translate(self, expr):
        body = self._prepare(as_text(expr))
        if body is None:
            return None
        try:
            dax = self._rewrite(body)
        except ValueError:
            return None
        if not dax or not self._used_aggregation:
            return None
        return dax.strip()

    def first_aggregation(self, expr):
        """
        Pull the first plain aggregation out of an expression we cannot fully
        translate. Qlik apps often wrap a real measure in display formatting -
        `If(Sum(X) >= 1e9, '$' & Num(Sum(X)/1e9, ...), ...)` - and the useful
        part is the `Sum(X)` inside. Returns (name, dax) or None.
        """
        body = self._prepare(as_text(expr))
        if body is None:
            body = self._strip_set_analysis(re.sub(r"^\s*=", "", as_text(expr)))
        names = "|".join(self.AGGREGATIONS)
        for m in re.finditer(rf"(?i)\b({names})\s*\(\s*(\[[^\]]+\]|[\w.&#/%]+)\s*\)", body):
            fn, operand = m.group(1).lower(), m.group(2)
            ref = self.resolver.resolve(self._unbracket(operand))
            if not ref:
                continue
            plain, _rowwise, prefix = self.AGGREGATIONS[fn]
            return (f"{prefix} {ref[1]}".strip(),
                    f"{plain}('{ref[0]}'[{ref[1]}])")
        return None

    def suggest_name(self, expr):
        """A readable measure name derived from what the expression aggregates."""
        if not self._used_field:
            return None
        prefix = self.AGGREGATIONS.get(self._used_aggregation, ("", "", ""))[2]
        return f"{prefix} {self._used_field}".strip()

    # -- preparation -----------------------------------------------------
    def _prepare(self, expr):
        """Drop the Qlik-only decoration that has no DAX counterpart."""
        body = re.sub(r"^\s*=", "", expr).strip()
        body = self._strip_set_analysis(body)
        if not body:
            return None
        # A dollar expansion resolves against app variables at runtime; there
        # is nothing faithful we can emit for it.
        if "$(" in body:
            return None
        return body

    @staticmethod
    def _strip_set_analysis(expr):
        """Remove `{<...>}` selection modifiers, braces nest included."""
        out = []
        depth = 0
        for ch in expr:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            elif depth == 0:
                out.append(ch)
        return "".join(out).strip()

    # -- rewriting -------------------------------------------------------
    def _rewrite(self, expr):
        out = []
        i = 0
        n = len(expr)
        while i < n:
            ch = expr[i]

            if ch == "[":                                  # [Bracketed Field]
                end = expr.find("]", i)
                if end == -1:
                    raise ValueError("unbalanced bracket")
                out.append(self._field_ref(expr[i + 1:end]))
                i = end + 1
                continue

            if ch.isalpha() or ch == "_":                  # identifier
                j = i
                while j < n and (expr[j].isalnum() or expr[j] in "_.#&/%"):
                    j += 1
                word = expr[i:j]
                k = j
                while k < n and expr[k].isspace():
                    k += 1
                if k < n and expr[k] == "(":               # function call
                    close = self._matching_paren(expr, k)
                    args = expr[k + 1:close]
                    out.append(self._rewrite_call(word, args))
                    i = close + 1
                else:                                      # bare field
                    out.append(self._field_ref(word))
                    i = j
                continue

            if ch in "+-*/(),<>=&" or ch.isdigit() or ch.isspace() or ch in "'\".":
                out.append(ch)
                i += 1
                continue

            raise ValueError(f"unsupported character {ch!r}")

        return "".join(out)

    def _rewrite_call(self, name, args):
        key = name.lower()

        if key in self.AGGREGATIONS:
            plain, rowwise, _prefix = self.AGGREGATIONS[key]
            inner = args.strip()

            distinct = re.match(r"(?i)^\s*distinct\s+(.*)$", inner)
            if distinct:
                ref = self.resolver.resolve(self._unbracket(distinct.group(1)))
                if not ref:
                    raise ValueError("unknown distinct field")
                self._remember(key, ref[1])
                return f"DISTINCTCOUNT('{ref[0]}'[{ref[1]}])"

            ref = self.resolver.resolve(self._unbracket(inner))
            if ref:
                self._remember(key, ref[1])
                return f"{plain}('{ref[0]}'[{ref[1]}])"

            # An expression inside the aggregation has to be evaluated row by
            # row, which is exactly what the DAX X-functions do.
            inner_dax = self._rewrite(inner)
            table = self._table_of(inner_dax)
            if not table:
                raise ValueError("cannot resolve aggregation operand")
            self._remember(key, None)
            return f"{rowwise}('{table}', {inner_dax})"

        if key == "num":
            # Num(value) converts, Num(value, format) formats.
            parts = [self._rewrite(a) for a in self._split_args(args)]
            if len(parts) >= 2:
                return f"FORMAT({parts[0]}, {parts[1]})"
            return f"VALUE({parts[0]})"

        if key in self.PASSTHROUGH:
            parts = [self._rewrite(a) for a in self._split_args(args)]
            return f"{self.PASSTHROUGH[key]}({', '.join(parts)})"

        raise ValueError(f"unsupported function {name}")

    def _field_ref(self, name):
        ref = self.resolver.resolve(self._unbracket(name))
        if not ref:
            raise ValueError(f"unknown field {name}")
        return f"'{ref[0]}'[{ref[1]}]"

    def _remember(self, aggregation, field):
        if self._used_aggregation is None:
            self._used_aggregation = aggregation
        if field and self._used_field is None:
            self._used_field = field

    @staticmethod
    def _table_of(dax):
        m = re.search(r"'([^']+)'\[", dax or "")
        return m.group(1) if m else None

    @staticmethod
    def _unbracket(name):
        return (name or "").strip().strip("[]").strip()

    @staticmethod
    def _matching_paren(expr, start):
        depth = 0
        for i in range(start, len(expr)):
            if expr[i] == "(":
                depth += 1
            elif expr[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
        raise ValueError("unbalanced parentheses")

    @staticmethod
    def _split_args(args):
        parts = []
        depth = 0
        current = []
        for ch in args:
            if ch == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            current.append(ch)
        parts.append("".join(current))
        return [p for p in parts if p.strip()]


# ============================================================
# THE QLIK MODEL - schema, real rows, sheets and charts
# ============================================================

class QlikModel:
    """
    Everything we know about the source app, in one place: the tables with
    their real rows, and the sheets with their real charts.
    """

    def __init__(self, extraction: dict, data_tables: dict, max_rows=DEFAULT_MAX_ROWS):
        self.extraction = extraction
        self.max_rows = max_rows
        self.title = as_text(extraction.get("app_properties", {}).get("title")) or "Qlik_Project"
        self.tables = self._build_tables(extraction, data_tables)
        self.sheets = extraction.get("sheets", [])
        self._column_index = self._index_columns()

    # -- schema ----------------------------------------------------------
    def _build_tables(self, extraction: dict, data_tables: dict):
        """
        Prefer the decoded tables, which carry both schema and rows. Fall back
        to the declared schema when the app was saved without its data, so the
        report still comes out with the right fields and visuals.
        """
        tables = []
        if data_tables:
            for name, t in data_tables.items():
                tables.append({
                    "name": sanitize_identifier(name),
                    "source_name": name,
                    "columns": dedupe_columns([{"name": c["name"], "type": c["type"]}
                                               for c in t["columns"]]),
                    "rows": t["rows"][:self.max_rows],
                    "total_rows": len(t["rows"]),
                })
            return tables

        model = extraction.get("data_model", {})
        declared = model.get("tables", []) or [{"name": self.title}]
        fields = model.get("fields", [])
        by_table = {}
        for f in fields:
            owners = f.get("source_table") or [declared[0].get("name", self.title)]
            by_table.setdefault(owners[0], []).append(f)

        for t in declared:
            tname = t.get("name") or self.title
            cols = by_table.get(tname, fields if len(declared) == 1 else [])
            tables.append({
                "name": sanitize_identifier(tname),
                "source_name": tname,
                "columns": dedupe_columns([
                    {"name": f["name"],
                     "type": "double" if f.get("is_numeric") else "string"}
                    for f in cols]),
                "rows": [],
                "total_rows": t.get("rows", 0),
            })
        return [t for t in tables if t["columns"]] or [{
            "name": sanitize_identifier(self.title), "source_name": self.title,
            "columns": [{"name": "Value", "type": "string"}],
            "rows": [], "total_rows": 0,
        }]

    def _index_columns(self):
        """Map a lower-cased field name to (table, column) for expression resolution."""
        index = {}
        for t in self.tables:
            for c in t["columns"]:
                index.setdefault(c["name"].lower(), (t["name"], c["name"]))
        return index

    @property
    def has_real_data(self):
        return any(t["rows"] for t in self.tables)

    # -- charts ----------------------------------------------------------
    def sheet_charts(self, sheet: dict):
        """
        The charts on one sheet, each with the position Qlik gave it.

        Qlik records a cell per top-level object with bounds in percent of the
        sheet; objects nested inside a container share the container's cell, so
        they are spread across it here.
        """
        cells = {c.get("name"): c for c in sheet.get("cells", [])}
        charts = [c for c in sheet.get("charts", []) if is_renderable(c)]

        placed = []
        nested = {}
        for chart in charts:
            parent = chart.get("parent")
            if parent:
                nested.setdefault(parent, []).append(chart)
            elif chart.get("id") in cells:
                placed.append((chart, cell_bounds(cells[chart["id"]])))
            else:
                placed.append((chart, None))

        # Children of a container get an equal share of its width.
        for parent_id, children in nested.items():
            box = cell_bounds(cells[parent_id]) if parent_id in cells else None
            if box is None:
                placed.extend((c, None) for c in children)
                continue
            x, y, w, h = box
            share = w / max(len(children), 1)
            for i, child in enumerate(children):
                placed.append((child, (x + i * share, y, share, h)))

        # Anything Qlik did not give a cell for is tiled under the rest.
        unplaced = [c for c, box in placed if box is None]
        if unplaced:
            used_bottom = max((box[1] + box[3] for _c, box in placed if box), default=0)
            top = min(used_bottom, CANVAS_HEIGHT - 160)
            width = CANVAS_WIDTH / len(unplaced)
            fill = {id(c): (i * width, top, width, CANVAS_HEIGHT - top)
                    for i, c in enumerate(unplaced)}
            placed = [(c, box if box else fill[id(c)]) for c, box in placed]

        return placed


def cell_bounds(cell: dict):
    """Convert one Qlik cell's percentage bounds to Power BI canvas units."""
    b = cell.get("bounds") or {}
    if not b:
        return None
    x = float(b.get("x", 0)) / 100.0 * CANVAS_WIDTH
    y = float(b.get("y", 0)) / 100.0 * CANVAS_HEIGHT
    w = float(b.get("width", 25)) / 100.0 * CANVAS_WIDTH
    h = float(b.get("height", 30)) / 100.0 * CANVAS_HEIGHT
    # Keep every visual on the canvas and big enough for Power BI to render.
    w = max(80.0, min(w, CANVAS_WIDTH))
    h = max(60.0, min(h, CANVAS_HEIGHT))
    x = max(0.0, min(x, CANVAS_WIDTH - w))
    y = max(0.0, min(y, CANVAS_HEIGHT - h))
    return (x, y, w, h)


# Qlik object types that hold no data of their own.
DECORATIVE_TYPES = {
    "sn-layout-container", "sn-tabbed-container", "container", "text-image",
    "sn-nav-menu", "qlik-sheet", "sheet", "sn-shape", "action-button",
    "sn-action-button", "sn-video-player", "sn-image",
}


def is_renderable(chart: dict) -> bool:
    """Skip containers and decorations - only objects that show data migrate."""
    ctype = as_text(chart.get("type")).lower()
    if ctype in DECORATIVE_TYPES:
        return False
    return bool(chart.get("dimensions") or chart.get("measures"))


# ============================================================
# EXPRESSION RESOLUTION
# ============================================================

class FieldResolver:
    """Resolves Qlik field references against the real migrated schema."""

    def __init__(self, model: QlikModel, preferred_table=None):
        self.model = model
        self.preferred_table = preferred_table

    def default_table(self):
        if self.preferred_table:
            return self.preferred_table
        return self.model.tables[0]["name"]

    def resolve(self, field: str):
        """Return (table, column) for a Qlik field reference, or None."""
        if not field:
            return None
        name = field.strip().strip("[]").strip('"').strip("'").strip()
        if not name or not re.fullmatch(r"[\w \-\.&#/%]+", name):
            return None
        hit = self.model._column_index.get(name.lower())
        if not hit:
            return None
        # Prefer the table this visual is already using, when it has the field.
        if self.preferred_table:
            for t in self.model.tables:
                if t["name"] == self.preferred_table:
                    for c in t["columns"]:
                        if c["name"].lower() == name.lower():
                            return (t["name"], c["name"])
        return hit

    def table_for_expression(self, expr: str):
        for token in re.findall(r"[\w\.&#/%]+", expr or ""):
            ref = self.resolve(token)
            if ref:
                return ref[0]
        return None

    def describe(self):
        return "\n".join(
            f"  '{t['name']}': " + ", ".join(c["name"] for c in t["columns"][:25])
            for t in self.model.tables)

    def validate_dax(self, dax: str) -> bool:
        """Only accept generated DAX that references columns we actually have."""
        refs = re.findall(r"'([^']+)'\[([^\]]+)\]", dax or "")
        if not refs:
            return False
        for table, col in refs:
            match = next((t for t in self.model.tables if t["name"] == table), None)
            if not match or not any(c["name"] == col for c in match["columns"]):
                return False
        return True


# ============================================================
# HELPER UTILITIES
# ============================================================

def new_guid():
    return str(uuid.uuid4())


def as_text(value) -> str:
    """
    Coerce a Qlik property to plain text.

    Titles, labels and measure definitions are usually strings, but Qlik stores
    a calculated one as an object such as {"qStringExpression": {"qExpr": ...}}.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("qStringExpression", "qValueExpression"):
            if key in value:
                return as_text(value[key])
        for key in ("qExpr", "qExpression", "value", "text"):
            if key in value:
                return as_text(value[key])
        return ""
    if isinstance(value, (list, tuple)):
        return as_text(value[0]) if value else ""
    return str(value).strip()


def dax_literal(text: str) -> str:
    """Quote a title for a Power BI literal expression."""
    return "'" + str(text).replace("'", "''") + "'"


def sanitize_identifier(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_ ]", "_", str(name or "Table")).strip()
    return cleaned or "Table"


def dedupe_columns(columns):
    """
    Give every column a distinct name.

    A Power Query table cannot carry two columns of the same name, and a Qlik
    app can legitimately expose one field through more than one table.
    """
    seen = {}
    out = []
    for col in columns:
        name = str(col.get("name") or "Column").strip() or "Column"
        key = name.lower()
        if key in seen:
            seen[key] += 1
            name = f"{name} {seen[key]}"
        else:
            seen[key] = 1
        out.append({**col, "name": name})
    return out


def unique_name(name: str, taken: set) -> str:
    candidate = name
    n = 2
    while candidate.lower() in taken:
        candidate = f"{name} {n}"
        n += 1
    taken.add(candidate.lower())
    return candidate


def sanitize_measure(name: str) -> str:
    cleaned = re.sub(r"[\[\]']", "", str(name or "Measure")).strip()
    return cleaned or "Measure"


def make_column_ref(table_name, column_name):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table_name}},
                       "Property": column_name}}


def make_measure_ref(table_name, measure_name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": table_name}},
                        "Property": measure_name}}


def make_projection(field_ref, query_ref, native_ref=None, active=False):
    proj = {"field": field_ref, "queryRef": query_ref}
    if native_ref:
        proj["nativeQueryRef"] = native_ref
    if active:
        proj["active"] = True
    return proj


# ============================================================
# M (POWER QUERY) GENERATION - REAL ROWS
# ============================================================

M_TYPES = {
    "double": "type number",
    "int64": "Int64.Type",
    "dateTime": "type datetime",
    "string": "type text",
    "boolean": "type logical",
}


def m_escape(text: str) -> str:
    """Escape a string for an M literal ('#' starts an M escape sequence)."""
    return str(text).replace("#", "#(#)").replace('"', '""')


def m_text(value) -> str:
    """Render a text value, keeping the literal within what M will tokenise."""
    text = str(value)
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "..."
    return f'"{m_escape(text)}"'


def m_literal(value, col_type: str) -> str:
    """Render one real value as a native M literal."""
    if value is None or value == "":
        return "null"

    if col_type in ("double", "int64"):
        try:
            num = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return m_text(value)
        if not math.isfinite(num):
            return "null"
        if num.is_integer() and abs(num) < 1e15:
            return str(int(num))
        return repr(num)

    if col_type == "dateTime":
        parsed = parse_date(value)
        if parsed:
            return (f"#datetime({parsed.year}, {parsed.month}, {parsed.day}, "
                    f"{parsed.hour}, {parsed.minute}, {parsed.second})")
        return m_text(value)

    return m_text(value)


DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d")


def parse_date(value):
    text = str(value).strip().rstrip("Z")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def build_m_expression(table: dict) -> list:
    """
    Build the Power Query script for one table.

    The real rows are written inline as an M `#table`, which is what makes the
    generated .pbit / .pbip open and render without any data source, gateway or
    credentials - the data travels with the file.
    """
    columns = table["columns"]
    if not columns:
        columns = [{"name": "Value", "type": "string"}]

    # Column names go in as text literals rather than quoted identifiers, and
    # the types are applied afterwards - the plainest form the M parser has to
    # deal with, and the one Power BI itself writes.
    header = "{" + ", ".join(f'"{m_escape(c["name"])}"' for c in columns) + "}"
    type_pairs = "{" + ", ".join(
        f'{{"{m_escape(c["name"])}", {M_TYPES.get(c["type"], "type text")}}}'
        for c in columns) + "}"

    lines = ["let"]
    rows = table.get("rows") or []
    if rows:
        lines.append(f"    Source = #table({header}, {{")
        last = len(rows) - 1
        for i, row in enumerate(rows):
            cells = ", ".join(m_literal(row[j] if j < len(row) else None, c["type"])
                              for j, c in enumerate(columns))
            lines.append(f"        {{{cells}}}" + ("," if i < last else ""))
        lines.append("    }),")
    else:
        # The app carried no data; ship the schema so the report still opens.
        lines.append(f"    Source = #table({header}, {{}}),")

    lines.append(f"    Typed = Table.TransformColumnTypes(Source, {type_pairs})")
    lines.append("in")
    lines.append("    Typed")
    return lines


# ============================================================
# SEMANTIC MODEL (model.bim)
# ============================================================

class ModelBuilder:
    """Builds model.bim: every real table, its real rows, and the DAX measures."""

    def __init__(self, model: QlikModel, ai: AIConverterBrain):
        self.model = model
        self.ai = ai
        self.measures_by_table = {}
        self.measure_lookup = {}      # qlik expression -> (table, measure name)
        self._build_measures()

    def _build_measures(self):
        taken = set()
        for table in self.model.tables:
            name = f"Row Count of {table['name']}"
            self.measures_by_table.setdefault(table["name"], []).append({
                "name": unique_name(name, taken),
                "expression": f"COUNTROWS('{table['name']}')",
            })

        # One measure per distinct Qlik expression used anywhere in the app.
        for sheet in self.model.sheets:
            for chart in sheet.get("charts", []):
                table = self._table_for_chart(chart)
                resolver = FieldResolver(self.model, table)
                for meas in chart.get("measures", []):
                    expr = as_text(meas.get("expression"))
                    if not expr or expr in self.measure_lookup:
                        continue
                    label = as_text(meas.get("label"))
                    name, dax = self.ai.translate(expr, resolver)
                    owner = self._table_of_dax(dax) or table
                    final = unique_name(sanitize_measure(label or name), taken)
                    self.measures_by_table.setdefault(owner, []).append({
                        "name": final, "expression": dax,
                    })
                    self.measure_lookup[expr] = (owner, final)

    def _table_of_dax(self, dax: str):
        m = re.search(r"'([^']+)'\[", dax or "")
        return m.group(1) if m else None

    def _table_for_chart(self, chart: dict):
        """Whichever table holds the fields this chart uses."""
        resolver = FieldResolver(self.model)
        for dim in chart.get("dimensions", []):
            ref = resolver.resolve(as_text(dim.get("field")))
            if ref:
                return ref[0]
        for meas in chart.get("measures", []):
            table = resolver.table_for_expression(as_text(meas.get("expression")))
            if table:
                return table
        return self.model.tables[0]["name"]

    def build(self) -> dict:
        tables = []
        for t in self.model.tables:
            columns = [{
                "name": c["name"],
                "dataType": c["type"],
                "sourceColumn": c["name"],
                "lineageTag": new_guid(),
            } for c in t["columns"]]
            if not columns:
                continue
            tables.append({
                "name": t["name"],
                "lineageTag": new_guid(),
                "columns": columns,
                "measures": [{**m, "lineageTag": new_guid()}
                             for m in self.measures_by_table.get(t["name"], [])],
                "partitions": [{
                    "name": f"{t['name']}-partition",
                    "mode": "import",
                    "source": {"type": "m", "expression": build_m_expression(t)},
                }],
            })

        return {
            "name": "SemanticModel",
            "compatibilityLevel": 1606,
            "model": {
                "culture": "en-US",
                "dataAccessOptions": {
                    "legacyRedirects": True,
                    "returnErrorValuesAsNull": True,
                },
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "sourceQueryCulture": "en-US",
                "tables": tables,
                "annotations": [
                    {"name": "PBI_QueryOrder",
                     "value": json.dumps([t["name"] for t in tables])},
                    {"name": "PBIDesktopVersion", "value": "2.138.1004.0 (24.10)"},
                ],
            },
        }


# ============================================================
# VISUALS
# ============================================================

# Qlik visualization type -> Power BI visual type.
VISUAL_MAP = {
    "barchart": "clusteredColumnChart",
    "sn-bar-chart": "clusteredColumnChart",
    "linechart": "lineChart",
    "sn-line-chart": "lineChart",
    "areachart": "areaChart",
    "piechart": "pieChart",
    "sn-pie-chart": "pieChart",
    "donutchart": "donutChart",
    "distributionplot": "clusteredColumnChart",
    "boxplot": "clusteredColumnChart",
    "histogram": "columnChart",
    "waterfallchart": "waterfallChart",
    "combochart": "lineClusteredColumnComboChart",
    "scatterplot": "scatterChart",
    "bubblechart": "scatterChart",
    "treemap": "treemap",
    "map": "map",
    "sn-map": "map",
    "gauge": "gauge",
    "bulletchart": "gauge",
    "kpi": "card",
    "sn-kpi": "card",
    "text-image": "card",
    "table": "tableEx",
    "sn-table": "tableEx",
    "pivot-table": "pivotTable",
    "sn-pivot-table": "pivotTable",
    "filterpane": "slicer",
    "listbox": "slicer",
    "sn-filter-pane": "slicer",
    "funnelchart": "funnel",
    "sn-funnel-chart": "funnel",
}

CARD_VISUALS = {"card", "gauge", "kpi"}
TABLE_VISUALS = {"tableEx", "pivotTable"}


class VisualBuilder:
    """Turns one Qlik chart into a Power BI visual, in both project formats."""

    SCHEMA = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
              "definition/visualContainer/1.4.0/schema.json")

    def __init__(self, model: QlikModel, builder: ModelBuilder):
        self.model = model
        self.builder = builder
        self.counter = 0

    # -- shared field selection ------------------------------------------
    def _fields_for(self, chart: dict):
        """The real columns and measures this chart binds to."""
        table = self.builder._table_for_chart(chart)
        resolver = FieldResolver(self.model, table)

        dimensions = []
        for dim in chart.get("dimensions", []):
            ref = resolver.resolve(as_text(dim.get("field")))
            if ref:
                dimensions.append(ref)

        measures = []
        for meas in chart.get("measures", []):
            expr = as_text(meas.get("expression"))
            hit = self.builder.measure_lookup.get(expr)
            if hit:
                measures.append(hit)

        if not dimensions and not measures:
            measures.append((table, self.builder.measures_by_table[table][0]["name"]))
        return table, dimensions, measures

    def _visual_type(self, chart: dict, dimensions, measures) -> str:
        qtype = as_text(chart.get("visualization") or chart.get("type")).lower()
        pbi = VISUAL_MAP.get(qtype)
        if pbi:
            return pbi
        # An unmapped object still migrates as whatever its fields support.
        if not dimensions:
            return "card"
        if not measures:
            return "slicer"
        return "clusteredColumnChart"

    def _title(self, chart: dict) -> str:
        title = as_text(chart.get("title"))
        if title:
            return title
        for meas in chart.get("measures", []):
            label = as_text(meas.get("label"))
            if label:
                return label
        return as_text(chart.get("type") or "Visual").replace("-", " ").title()

    # -- PBIR (.pbip) format ---------------------------------------------
    def build_pbir(self, chart: dict, box) -> dict:
        self.counter += 1
        table, dimensions, measures = self._fields_for(chart)
        vtype = self._visual_type(chart, dimensions, measures)
        x, y, w, h = box

        query_state = {}
        if vtype == "slicer":
            source = dimensions or [(table, self.model.tables[0]["columns"][0]["name"])]
            t, col = source[0]
            query_state["Values"] = {"projections": [make_projection(
                make_column_ref(t, col), f"{t}.{col}", col, active=True)]}
        elif vtype in CARD_VISUALS:
            projections = [make_projection(make_measure_ref(t, mname),
                                           f"{t}.{mname}", mname)
                           for t, mname in measures[:1]]
            query_state["Values"] = {"projections": projections}
        elif vtype in TABLE_VISUALS:
            projections = [make_projection(make_column_ref(t, col),
                                           f"{t}.{col}", col, active=True)
                           for t, col in dimensions]
            projections += [make_projection(make_measure_ref(t, mname),
                                            f"{t}.{mname}", mname)
                            for t, mname in measures]
            query_state["Values"] = {"projections": projections}
        else:
            if dimensions:
                t, col = dimensions[0]
                query_state["Category"] = {"projections": [make_projection(
                    make_column_ref(t, col), f"{t}.{col}", col, active=True)]}
            if measures:
                query_state["Y"] = {"projections": [
                    make_projection(make_measure_ref(t, mname), f"{t}.{mname}", mname)
                    for t, mname in measures]}
            if len(dimensions) > 1:
                t, col = dimensions[1]
                query_state["Series"] = {"projections": [make_projection(
                    make_column_ref(t, col), f"{t}.{col}", col, active=True)]}

        return {
            "$schema": self.SCHEMA,
            "name": f"visual{new_guid().replace('-', '')[:20]}",
            "position": {
                "x": round(x, 2), "y": round(y, 2),
                "z": self.counter * 1000,
                "width": round(w, 2), "height": round(h, 2),
                "tabOrder": self.counter,
            },
            "visual": {
                "visualType": vtype,
                "query": {"queryState": query_state},
                "visualContainerObjects": {
                    "title": [{"properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {
                            "Value": dax_literal(self._title(chart))}}},
                    }}]
                },
            },
        }

    # -- legacy Layout (.pbit) format ------------------------------------
    def build_legacy(self, chart: dict, box, order: int) -> dict:
        table, dimensions, measures = self._fields_for(chart)
        vtype = self._visual_type(chart, dimensions, measures)
        x, y, w, h = box

        projections = {}
        select = []

        def add_column(t, col):
            select.append({"Column": {"Expression": {"SourceRef": {"Source": "t"}},
                                      "Property": col},
                           "Name": f"{t}.{col}"})
            return {"queryRef": f"{t}.{col}"}

        def add_measure(t, mname):
            select.append({"Measure": {"Expression": {"SourceRef": {"Source": "t"}},
                                       "Property": mname},
                           "Name": f"{t}.{mname}"})
            return {"queryRef": f"{t}.{mname}"}

        if vtype == "slicer":
            source = dimensions or [(table, self.model.tables[0]["columns"][0]["name"])]
            projections["Values"] = [add_column(*source[0])]
        elif vtype in CARD_VISUALS:
            projections["Values"] = [add_measure(*m) for m in measures[:1]]
        elif vtype in TABLE_VISUALS:
            projections["Values"] = ([add_column(*d) for d in dimensions] +
                                     [add_measure(*m) for m in measures])
        else:
            if dimensions:
                projections["Category"] = [add_column(*dimensions[0])]
            if measures:
                projections["Y"] = [add_measure(*m) for m in measures]
            if len(dimensions) > 1:
                projections["Series"] = [add_column(*dimensions[1])]

        config = {
            "name": f"visual{new_guid().replace('-', '')[:20]}",
            "layouts": [{"id": 0, "position": {
                "x": round(x, 2), "y": round(y, 2), "z": order * 1000,
                "width": round(w, 2), "height": round(h, 2), "tabOrder": order}}],
            "singleVisual": {
                "visualType": vtype,
                "projections": projections,
                "prototypeQuery": {
                    "Version": 2,
                    "From": [{"Name": "t", "Entity": table, "Type": 0}],
                    "Select": select,
                },
                "vcObjects": {
                    "title": [{"properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {
                            "Value": dax_literal(self._title(chart))}}},
                    }}]
                },
            },
        }

        return {"x": round(x, 2), "y": round(y, 2),
                "width": round(w, 2), "height": round(h, 2),
                "z": order * 1000,
                "config": json.dumps(config, ensure_ascii=False)}


# ============================================================
# PROJECT WRITER
# ============================================================

class UniversalPBIPGenerator:
    """Writes the whole PBIP directory tree, the .pbit template, and the audit."""

    def __init__(self, extraction_data: dict, output_dir: str,
                 ai_brain: AIConverterBrain, max_rows=DEFAULT_MAX_ROWS,
                 data_tables=None, **_legacy):
        self.ai = ai_brain
        self.model = QlikModel(extraction_data, data_tables or {}, max_rows)
        self.builder = ModelBuilder(self.model, ai_brain)

        self.project_name = sanitize_identifier(self.model.title).replace(" ", "_")
        self.output_dir = Path(output_dir)
        self.report_dir = self.output_dir / f"{self.project_name}.Report"
        self.model_dir = self.output_dir / f"{self.project_name}.SemanticModel"
        self.definition_dir = self.report_dir / "definition"
        self.pages_dir = self.definition_dir / "pages"

    # -- entry point ------------------------------------------------------
    def generate(self):
        total_rows = sum(len(t["rows"]) for t in self.model.tables)
        chart_count = sum(len(self.model.sheet_charts(s)) for s in self.model.sheets)

        print(f"\n{'=' * 60}")
        print(f"  QLIK -> POWER BI MIGRATION - {self.project_name}")
        print(f"  Tables    : {len(self.model.tables)}")
        print(f"  Data rows : {total_rows:,} " +
              ("(real rows from the Qlik app)" if total_rows else
               "(app saved without data - schema only)"))
        print(f"  Sheets    : {len(self.model.sheets)}  ->  {chart_count} visuals")
        print(f"  DAX brain : {self.ai.provider.upper()} "
              f"({'Ollama available' if self.ai.is_available else 'rule-based'})")
        print(f"{'=' * 60}\n")

        self._create_directories()
        self._write_pbip_file()
        self._write_model_bim()
        self._write_definition_pbism()
        self._write_definition_pbir()
        self._write_report_json()
        self._write_version_json()
        self._generate_pages()
        self._write_pbit()
        self._write_audit_report()
        self._build_pbip_zip_archive()

        print(f"\n{'=' * 60}")
        print("  MIGRATION COMPLETE")
        print(f"{'=' * 60}")
        print(f"  Output folder : {self.output_dir}")
        print(f"  PBIP project  : {self.project_name}.pbip")
        print(f"  PBIT template : {self.project_name}.pbit\n")

    # -- structure --------------------------------------------------------
    def _create_directories(self):
        import shutil
        if self.pages_dir.exists():
            shutil.rmtree(self.pages_dir, ignore_errors=True)
        for d in (self.output_dir, self.report_dir, self.definition_dir,
                  self.pages_dir, self.model_dir):
            d.mkdir(parents=True, exist_ok=True)
        print("  [OK] PBIP directory tree")

    def _write_json(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  [OK] {path.relative_to(self.output_dir)}")

    def _write_pbip_file(self):
        self._write_json(self.output_dir / f"{self.project_name}.pbip", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/"
                       "pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{self.project_name}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        })

    def _write_definition_pbir(self):
        self._write_json(self.report_dir / "definition.pbir", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                       "definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {
                "byPath": {"path": f"../{self.project_name}.SemanticModel"}},
        })

    def _write_definition_pbism(self):
        self._write_json(self.model_dir / "definition.pbism",
                         {"version": "1.0", "settings": {}})

    def _write_model_bim(self):
        self._write_json(self.model_dir / "model.bim", self.builder.build())

    def _write_report_json(self):
        self._write_json(self.definition_dir / "report.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                       "definition/report/2.0.0/schema.json",
            "themeCollection": {"baseTheme": {"name": "CY24SU10"}},
            "settings": {
                "useStylableVisualContainerHeader": True,
                "exportDataMode": "AllowSummarized",
                "defaultDrillFilterOtherVisuals": True,
                "allowChangeFilterTypes": True,
                "useEnhancedTooltips": True,
            },
        })

    def _write_version_json(self):
        self._write_json(self.definition_dir / "version.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                       "definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        })

    # -- pages ------------------------------------------------------------
    def _sheets_for_output(self):
        sheets = self.model.sheets or [{"title": "Dashboard", "charts": [], "cells": []}]
        return sorted(sheets, key=lambda s: s.get("rank", 0))

    def _generate_pages(self):
        visual_builder = VisualBuilder(self.model, self.builder)
        page_ids = []

        for i, sheet in enumerate(self._sheets_for_output()):
            page_id = "ReportSection" if i == 0 else f"ReportSection{i}"
            page_ids.append(page_id)
            title = as_text(sheet.get("title")) or f"Page {i + 1}"

            page_dir = self.pages_dir / page_id
            self._write_json(page_dir / "page.json", {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/"
                           "report/definition/page/2.1.0/schema.json",
                "name": page_id,
                "displayName": title,
                "displayOption": "FitToPage",
                "width": CANVAS_WIDTH,
                "height": CANVAS_HEIGHT,
            })

            placed = self.model.sheet_charts(sheet)
            for chart, box in placed:
                visual = visual_builder.build_pbir(chart, box)
                self._write_json(
                    page_dir / "visuals" / visual["name"] / "visual.json", visual)
            print(f"  [OK] page '{title}': {len(placed)} visuals")

        self._write_json(self.pages_dir / "pages.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                       "definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": page_ids,
            "activePageName": page_ids[0],
        })

    # -- .pbit template ---------------------------------------------------
    def _build_legacy_layout(self):
        visual_builder = VisualBuilder(self.model, self.builder)
        sections = []
        for i, sheet in enumerate(self._sheets_for_output()):
            containers = []
            for order, (chart, box) in enumerate(self.model.sheet_charts(sheet), start=1):
                containers.append(visual_builder.build_legacy(chart, box, order))
            sections.append({
                "name": "ReportSection" if i == 0 else f"ReportSection{i}",
                "displayName": as_text(sheet.get("title")) or f"Page {i + 1}",
                "ordinal": i,
                "width": CANVAS_WIDTH,
                "height": CANVAS_HEIGHT,
                "visualContainers": containers,
                "config": json.dumps({}),
                "filters": "[]",
            })

        return {
            "id": 0,
            "resourcePackages": [],
            "sections": sections,
            "config": json.dumps({
                "version": "5.43",
                "activeSectionIndex": 0,
                "defaultDrillFilterOtherVisuals": True,
                "settings": {
                    "useStylableVisualContainerHeader": True,
                    "allowChangeFilterTypes": True,
                    "useEnhancedTooltips": True,
                },
            }, ensure_ascii=False),
            "filters": "[]",
            "layoutOptimization": 0,
        }

    def _write_pbit(self):
        pbit_path = self.output_dir / f"{self.project_name}.pbit"
        content_types = (
            b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>'
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="json" ContentType="" />'
            b'<Override PartName="/Version" ContentType="" />'
            b'<Override PartName="/Report/Layout" ContentType="" />'
            b'<Override PartName="/Settings" ContentType="application/json" />'
            b'<Override PartName="/Metadata" ContentType="application/json" />'
            b'<Override PartName="/DataModelSchema" ContentType="" />'
            b'</Types>')

        with zipfile.ZipFile(pbit_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("DataModelSchema",
                        json.dumps(self.builder.build(), ensure_ascii=False)
                        .encode("utf-16-le"))
            zf.writestr("Report/Layout",
                        json.dumps(self._build_legacy_layout(), ensure_ascii=False)
                        .encode("utf-16-le"))
            zf.writestr("Version", "1.28".encode("utf-16-le"))
            zf.writestr("Settings", json.dumps({
                "Version": 4,
                "ReportSettings": {},
                "QueriesSettings": {"TypeDetectionEnabled": True,
                                    "RelationshipImportEnabled": True},
            }, ensure_ascii=False).encode("utf-16-le"))
            zf.writestr("Metadata", json.dumps({
                "Version": 5,
                "AutoCreatedRelationships": [],
                "CreatedFrom": "Cloud",
            }, ensure_ascii=False).encode("utf-16-le"))

        print(f"  [OK] {pbit_path.name} (standalone Power BI template)")

    # -- audit ------------------------------------------------------------
    def _write_audit_report(self):
        lines = [
            f"# QLIK -> POWER BI MIGRATION AUDIT: {self.project_name}",
            "=" * 77,
            f"- Source app      : {self.model.title}",
            f"- Generated PBIP  : {self.project_name}.pbip",
            f"- Generated PBIT  : {self.project_name}.pbit",
            "",
            "## 1. Data model",
        ]
        for t in self.model.tables:
            embedded = len(t["rows"])
            note = (f"{embedded:,} rows embedded of {t['total_rows']:,} in the app"
                    if embedded else "schema only - the app was saved without data")
            lines.append(f"- `{t['name']}` : {len(t['columns'])} columns, {note}")

        lines += ["", "## 2. Measures translated from Qlik expressions"]
        for expr, (table, name) in self.builder.measure_lookup.items():
            dax = next((m["expression"] for m in self.builder.measures_by_table[table]
                        if m["name"] == name), "")
            lines.append(f"- `{expr}`  ->  **{name}** = `{dax}`")
            if "{" in expr:
                lines.append("  - REVIEW: the Qlik set analysis in this expression "
                             "selects a subset of rows. DAX expresses that with a "
                             "CALCULATE filter, which has to be chosen per measure, "
                             "so the translation aggregates over all rows.")
        if not self.builder.measure_lookup:
            lines.append("- (the app defined no chart expressions)")

        lines += ["", "## 3. Sheets and visuals"]
        for sheet in self._sheets_for_output():
            placed = self.model.sheet_charts(sheet)
            lines.append(f"- Sheet \"{as_text(sheet.get('title')) or 'Untitled'}\" "
                         f": {len(placed)} visuals")
            for chart, _box in placed:
                qtype = as_text(chart.get("visualization") or chart.get("type"))
                mapped = VISUAL_MAP.get(qtype.lower(), "clusteredColumnChart")
                lines.append(f"  - {qtype} -> {mapped} : "
                             f"\"{as_text(chart.get('title')) or 'untitled'}\"")

        lines += ["", "=" * 77]
        path = self.output_dir / "MIGRATION_AUDIT_REPORT.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  [OK] {path.name}")

    # -- bundle -----------------------------------------------------------
    def _build_pbip_zip_archive(self):
        zip_path = self.output_dir / f"{self.project_name}_PBIP.zip"
        if zip_path.exists():
            try:
                zip_path.unlink()
            except OSError:
                pass

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for ext in (".pbip", ".pbit", ".md"):
                for p in self.output_dir.glob(f"*{ext}"):
                    zf.write(p, arcname=p.name)
            for folder in (self.model_dir, self.report_dir):
                if not folder.exists():
                    continue
                for root, _dirs, files in os.walk(folder):
                    for f in files:
                        full = Path(root) / f
                        rel = full.relative_to(self.output_dir)
                        zf.write(full, arcname=str(rel).replace("\\", "/"))

        print(f"  [OK] {zip_path.name} (Fabric PBIP bundle)")


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def convert(qvf_path=None, extraction=None, output_dir=None, ai_brain=None,
            max_rows=DEFAULT_MAX_ROWS):
    """Run the whole migration. Importable so the web server can call it."""
    ai_brain = ai_brain or AIConverterBrain()

    data_tables = {}
    if qvf_path:
        from qvf_extractor import QVFExtractor
        from qvf_data_reader import QVFDataReader

        extraction = QVFExtractor(str(qvf_path)).extract()
        reader = QVFDataReader(str(qvf_path), max_rows=max_rows)
        try:
            data_tables = reader.read()
        except Exception as e:
            print(f"  [warn] row data could not be decoded: {e}")
            data_tables = {}

    if extraction is None:
        raise ValueError("convert() needs either qvf_path or extraction")

    title = as_text(extraction.get("app_properties", {}).get("title")) or "Qlik_Project"
    if not output_dir:
        output_dir = f"{sanitize_identifier(title).replace(' ', '_')}_PowerBI_Project"

    generator = UniversalPBIPGenerator(extraction, output_dir, ai_brain,
                                       max_rows=max_rows, data_tables=data_tables)
    generator.generate()
    return generator


def main():
    parser = argparse.ArgumentParser(
        description="Convert a Qlik Sense .qvf into a Power BI PBIP project")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--qvf", help="Path to any Qlik Sense (.qvf) file")
    group.add_argument("--input", "-i", help="Path to an extraction_result.json")

    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--provider", default="ollama",
                        choices=["ollama", "openai", "gemini"], help="AI provider")
    parser.add_argument("--model", default="llama3.2", help="Model name")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS,
                        help=f"Rows to embed per table (default {DEFAULT_MAX_ROWS})")
    args = parser.parse_args()

    ai_brain = AIConverterBrain(provider=args.provider, model=args.model)

    if args.qvf:
        convert(qvf_path=args.qvf, output_dir=args.output, ai_brain=ai_brain,
                max_rows=args.max_rows)
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            extraction = json.load(f)
        convert(extraction=extraction, output_dir=args.output, ai_brain=ai_brain,
                max_rows=args.max_rows)


if __name__ == "__main__":
    main()
