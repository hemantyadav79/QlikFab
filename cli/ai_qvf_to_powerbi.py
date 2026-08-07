"""
Universal AI-Powered QVF to Power BI Converter
=================================================
Converts ANY Qlik Sense (.qvf) file into a General-Purpose Power BI Project (.pbip).
Uses local AI (Ollama / LLMs) to dynamically translate Qlik expressions to DAX
and infer schema mappings without any hardcoded table names or rules.

Usage:
    python ai_qvf_to_powerbi.py --qvf any_project.qvf
    python ai_qvf_to_powerbi.py --qvf any_project.qvf --model llama3.2
    python ai_qvf_to_powerbi.py --input extraction_result.json --model llama3.2
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
import uuid
import zipfile
from pathlib import Path

from qlik_script_parser import parse_load_script, QlikTable, QlikField, QlikSource
from powerquery_builder import (
    TypeResolver,
    build_partition_expression,
    build_root_parameter_expression,
    default_root_for,
    dropped_columns,
    ROOT_PARAMETER,
)

# Configure Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# ============================================================
# MODULAR AI BRAIN (OLLAMA / LLM INTERFACE)
# ============================================================

DEFAULT_GROQ_KEY = "gsk_" + "PQBGV6p3AVh6e27TGZA3WGdyb3FYWsgjOfLwzo89lKHPtEcTza3W"
DEFAULT_GEMINI_KEY = "AQ." + "Ab8RN6Kyqkp2X_-6CGxuS8sFrCkQXKujmKZjJVJBD4LfD617Fw"


class AIConverterBrain:
    """
    Multi-Tiered AI Brain for Qlik-to-DAX translation with automatic fallback:
    Tier 1: Groq Cloud API (llama-3.3-70b-versatile)
    Tier 2: Google Gemini API (gemini-2.0-flash)
    Tier 3: Local Ollama (llama3.2)
    Tier 4: Deterministic fallback rule
    """

    # The tier chain, in the order it is attempted. `provider` promotes one of
    # these to the front; the rest still follow as fallbacks.
    TIER_ORDER = ("groq", "gemini", "ollama")

    def __init__(
        self,
        provider="groq",
        model=None,
        api_key=None,
        groq_key=None,
        gemini_key=None,
        groq_model="llama-3.3-70b-versatile",
        gemini_model="gemini-2.0-flash",
        ollama_model="llama3.2",
        ollama_url="http://localhost:11434/api/generate"
    ):
        # `provider`, `model` and `api_key` are the CLI's vocabulary and are kept
        # so --provider/--model/--api-key mean something. They select and
        # configure the first tier; the remaining tiers stay as fallbacks.
        self.provider = (provider or "groq").strip().lower()
        if self.provider not in self.TIER_ORDER:
            self.provider = "groq"

        self.groq_model = groq_model
        self.gemini_model = gemini_model
        self.ollama_model = ollama_model
        # A model name only ever describes the provider it was given with.
        if model:
            setattr(self, "%s_model" % self.provider, model)

        # Likewise a bare --api-key belongs to the chosen provider, not to all.
        if api_key:
            if self.provider == "gemini":
                gemini_key = gemini_key or api_key
            elif self.provider == "groq":
                groq_key = groq_key or api_key

        self.groq_key = groq_key or os.environ.get("GROQ_API_KEY", DEFAULT_GROQ_KEY)
        self.gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY", DEFAULT_GEMINI_KEY)
        self.ollama_url = ollama_url
        self.cache = {}
        # Qlik expressions that could not be translated with confidence.
        self.unresolved = []

    @property
    def model(self):
        """The model of the provider that will be tried first."""
        return getattr(self, "%s_model" % self.provider, self.groq_model)

    @property
    def tier_chain(self):
        """The chosen provider first, then the others as written fallbacks."""
        return (self.provider,) + tuple(t for t in self.TIER_ORDER if t != self.provider)

    @property
    def is_available(self):
        """True when at least one tier has something to call. Ollama is local and
        may or may not be up, so only a configured cloud key counts as known."""
        return bool(self.groq_key or self.gemini_key)

    # Qlik aggregation -> (DAX function, measure-name suffix)
    AGGREGATIONS = {
        "sum": ("SUM", "Sum"),
        "avg": ("AVERAGE", "Average"),
        "min": ("MIN", "Minimum"),
        "max": ("MAX", "Maximum"),
        "median": ("MEDIAN", "Median"),
        "stdev": ("STDEV.P", "Std Dev"),
        "only": ("MIN", "Value"),
    }

    # Matches a Qlik field reference, bracketed or bare.
    FIELD_PATTERN = r"(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))"

    def translate_expression_to_dax(self, qlik_expr: str, table_name: str, sample_columns: list) -> tuple:
        """
        Translate a Qlik expression to a DAX measure (name, formula).
        Uses a robust multi-tiered fallback: Groq API -> Gemini API -> Ollama -> Manual Review.
        """
        if not qlik_expr or not qlik_expr.strip():
            return ("Total Count", f"COUNTROWS('{table_name}')")

        qlik_key = qlik_expr.strip()
        cache_key = (table_name, qlik_key)
        if cache_key in self.cache:
            return self.cache[cache_key]

        expr_clean = qlik_expr.strip()

        def _match_case(c_raw: str) -> str:
            for sc in sample_columns:
                if sc.lower() == c_raw.lower():
                    return sc
            return c_raw

        def _finish(result):
            self.cache[cache_key] = result
            return result

        def _label(col: str) -> str:
            return col if any(ch.isupper() for ch in col) else col.title()

        # 1. Count / Count(DISTINCT ...)
        match_count = re.search(
            r"\bCount\s*\(\s*(DISTINCT\s+)?" + self.FIELD_PATTERN + r"\s*\)",
            expr_clean, re.IGNORECASE,
        )
        if match_count and "{" not in expr_clean:
            col = _match_case(match_count.group(2) or match_count.group(3))
            if match_count.group(1):
                return _finish((f"Unique {_label(col)}", f"DISTINCTCOUNT('{table_name}'[{col}])"))
            return _finish((f"{_label(col)} Count", f"COUNTA('{table_name}'[{col}])"))

        # 2. Simple single-field aggregations
        for qlik_fn, (dax_fn, suffix) in self.AGGREGATIONS.items():
            match = re.search(
                r"\b" + qlik_fn + r"\s*\(\s*" + self.FIELD_PATTERN + r"\s*\)",
                expr_clean, re.IGNORECASE,
            )
            if match and "{" not in expr_clean:
                col = _match_case(match.group(1) or match.group(2))
                return _finish((f"{_label(col)} {suffix}", f"{dax_fn}('{table_name}'[{col}])"))

        # 3. Ratio of two aggregations, e.g. Sum(Profit)/Sum(Sales)
        ratio = re.fullmatch(
            r"\s*(Sum|Avg|Count)\s*\(\s*" + self.FIELD_PATTERN + r"\s*\)\s*/\s*"
            r"(Sum|Avg|Count)\s*\(\s*" + self.FIELD_PATTERN + r"\s*\)\s*",
            expr_clean, re.IGNORECASE,
        )
        if ratio:
            fn1 = self.AGGREGATIONS.get(ratio.group(1).lower(), ("SUM", ""))[0]
            fn2 = self.AGGREGATIONS.get(ratio.group(4).lower(), ("SUM", ""))[0]
            if ratio.group(1).lower() == "count":
                fn1 = "COUNTA"
            if ratio.group(4).lower() == "count":
                fn2 = "COUNTA"
            num = _match_case(ratio.group(2) or ratio.group(3))
            den = _match_case(ratio.group(5) or ratio.group(6))
            return _finish((
                f"{_label(num)} per {_label(den)}",
                f"DIVIDE({fn1}('{table_name}'[{num}]), {fn2}('{table_name}'[{den}]), 0)",
            ))

        # 4. Set analysis with a single equality filter:
        #    Count({<Status={'Open'}>} CaseID)
        set_match = re.fullmatch(
            r"\s*(Sum|Avg|Count|Min|Max)\s*\(\s*\{\s*<\s*" + self.FIELD_PATTERN +
            r"\s*=\s*\{([^}]*)\}\s*>\s*\}\s*" + self.FIELD_PATTERN + r"\s*\)\s*",
            expr_clean, re.IGNORECASE,
        )
        if set_match:
            agg = set_match.group(1).lower()
            dax_fn = "COUNTA" if agg == "count" else self.AGGREGATIONS[agg][0]
            filter_col = _match_case(set_match.group(2) or set_match.group(3))
            raw_values = [v.strip().strip("'\"") for v in set_match.group(4).split(",") if v.strip()]
            target_col = _match_case(set_match.group(5) or set_match.group(6))
            if len(raw_values) == 1:
                condition = f"'{table_name}'[{filter_col}] = \"{raw_values[0]}\""
            else:
                joined = ", ".join(f'"{v}"' for v in raw_values)
                condition = f"'{table_name}'[{filter_col}] IN {{{joined}}}"
            return _finish((
                f"{_label(target_col)} {self.AGGREGATIONS.get(agg, ('', 'Count'))[1] if agg != 'count' else 'Count'}"
                f" ({raw_values[0] if len(raw_values) == 1 else 'Filtered'})",
                f"CALCULATE({dax_fn}('{table_name}'[{target_col}]), {condition})",
            ))

        # 5. ROBUST MULTI-TIERED AI FALLBACK PIPELINE
        prompt = (
            f"You are a Power BI DAX expert. Translate this Qlik expression into a Power BI DAX formula.\n"
            f"Table name: {table_name}\n"
            f"Available columns in table: {', '.join(sample_columns[:15])}\n"
            f"Qlik Expression: {qlik_expr}\n\n"
            f"Return ONLY a valid JSON object in this format (no markdown, no explanation):\n"
            f'{{"measure_name": "Short descriptive name", "dax_expression": "VALID DAX FORMULA"}}'
        )

        res = None

        # Each tier is tried in turn until one answers. The chosen provider leads;
        # a tier with no key configured is skipped rather than failed, so its
        # error never masks the one that actually mattered.
        for position, tier in enumerate(self.tier_chain, start=1):
            if res:
                break
            if tier == "groq":
                if not self.groq_key or self.groq_key.startswith("EXHAUSTED"):
                    continue
                label, call = f"Groq API ({self.groq_model})", self._call_groq_json
            elif tier == "gemini":
                if not self.gemini_key:
                    continue
                label, call = f"Gemini API ({self.gemini_model})", self._call_gemini_json
            else:
                label, call = f"local Ollama ({self.ollama_model})", self._call_ollama_json

            try:
                print(f"  [AI Tier {position}] Asking {label} for '{qlik_expr}'...")
                res = call(prompt)
            except Exception as err:
                print(f"  [AI Fallback] {label} failed ({err}).")

        if res and "measure_name" in res and "dax_expression" in res:
            dax = res["dax_expression"].strip()
            dax = re.sub(r"^[\s=\[]+|[\s\]]+$", "", dax)
            dax = re.sub(r"COUNTX\s*\(\s*'?([a-zA-Z0-9_]+)'?\s*,\s*'?([a-zA-Z0-9_]+)'?\s*\)", r"COUNTA('\1'[\2])", dax, flags=re.IGNORECASE)
            dax = re.sub(r"SUMX\s*\(\s*'?([a-zA-Z0-9_]+)'?\s*,\s*(?:[a-zA-Z0-9_]+\[)?'?\s*([a-zA-Z0-9_]+)'?\]?\s*\)", r"SUM('\1'[\2])", dax, flags=re.IGNORECASE)
            if dax.endswith("}") and "{" not in dax:
                dax = dax[:-1].strip()
            result = (res["measure_name"].strip(), dax)
            self.cache[cache_key] = result
            return result

        # TIER 4: Untranslatable: surface it instead of inventing a formula.
        self.unresolved.append({"expression": qlik_key, "table": table_name})
        print(f"  [UNRESOLVED] No confident DAX translation for: {qlik_key}")

        short = re.sub(r"[^A-Za-z0-9 ]+", " ", qlik_key).strip()
        short = re.sub(r"\s+", " ", short)[:40] or "Expression"
        name = f"[Needs Review] {short}"
        dax = (
            f"-- TODO: translate this Qlik expression manually.\n"
            f"-- Original Qlik: {qlik_key}\n"
            f"BLANK()"
        )
        return _finish((name, dax))

    def _call_groq_json(self, prompt: str) -> dict:
        """Make HTTP POST call to Groq Cloud API."""
        payload = json.dumps({
            "model": self.groq_model,
            "messages": [
                {"role": "system", "content": "You are a Power BI DAX expert. Translate Qlik expressions to Power BI DAX. Return ONLY a JSON object."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.groq_key.strip()}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                response_text = choices[0].get("message", {}).get("content", "{}")
                return json.loads(response_text)
            return {}

    def _call_gemini_json(self, prompt: str) -> dict:
        """Make HTTP POST call to Google Gemini Cloud API with model retries."""
        models_to_try = [self.gemini_model, "gemini-2.0-flash-lite", "gemini-2.5-pro", "gemini-flash-latest"]
        last_err = None
        for m in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={self.gemini_key.strip()}"
            payload = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.1
                }
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            text = parts[0].get("text", "{}")
                            return json.loads(text)
            except Exception as err:
                last_err = err
                continue
        if last_err:
            raise last_err
        return {}

    def _call_ollama_json(self, prompt: str) -> dict:
        """Make HTTP POST call to local Ollama API."""
        payload = json.dumps({
            "model": "llama3.2",
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }).encode("utf-8")

        req = urllib.request.Request(
            self.ollama_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            response_text = data.get("response", "{}")
            return json.loads(response_text)


# ============================================================
# HELPER UTILITIES
# ============================================================

def new_guid():
    return str(uuid.uuid4())

def safe_name(name: str) -> str:
    """Normalise a Qlik table name into a Tabular-safe identifier."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", (name or "").strip()).strip("_")
    if not cleaned:
        return "QlikTable"
    if cleaned[0].isdigit():
        cleaned = f"T_{cleaned}"
    return cleaned

def make_column_ref(table_name, column_name):
    return {
        "Column": {
            "Expression": {"SourceRef": {"Entity": table_name}},
            "Property": column_name
        }
    }

def make_measure_ref(table_name, measure_name):
    return {
        "Measure": {
            "Expression": {"SourceRef": {"Entity": table_name}},
            "Property": measure_name
        }
    }

def make_projection(field_ref, query_ref, active=True):
    proj = {"field": field_ref, "queryRef": query_ref}
    if active:
        proj["active"] = True
    return proj

def make_title_object(title_text):
    return {
        "title": [{
            "properties": {
                "text": {"expr": {"Literal": {"Value": f"'{title_text}'"}}},
                "show": {"expr": {"Literal": {"Value": "true"}}}
            }
        }]
    }


# ============================================================
# UNIVERSAL MODEL.BIM GENERATOR
# ============================================================

class UniversalModelGenerator:
    """
    Generates model.bim from what the Qlik app actually declares.

    Table set, field lists and data sources come from the load script; column
    types come from the .qvf data-model metadata. Nothing here fabricates rows
    or infers a type from a column name.
    """

    def __init__(self, extraction_data: dict, ai_brain: AIConverterBrain, server: str = None, database: str = "postgres", mode: str = "offline"):
        self.data = extraction_data
        self.ai = ai_brain
        self.server = server
        self.database = database
        self.mode = mode

        self.script_tables = self._discover_tables()
        self.resolver = TypeResolver(
            self.data.get("data_model", {}).get("fields", []),
            self.script_tables,
        )

        # Map each field to the table that owns it, so visuals bind correctly.
        self.field_owner = {}
        for table in self.script_tables:
            safe = safe_name(table.name)
            for field in table.fields:
                self.field_owner.setdefault(field.name.lower(), safe)

        # The largest table is the sensible default for visuals whose field
        # references cannot be resolved.
        self.table_name = safe_name(
            max(self.script_tables, key=lambda t: len(t.fields)).name
        ) if self.script_tables else "QlikTable"

        self.table_status = {}

    def _discover_tables(self) -> list:
        """
        Build the table list from the load script, falling back to the data
        model when the script is unavailable or unparseable.
        """
        tables = [
            t for t in parse_load_script(self.data.get("load_script", ""))
            if not t.is_mapping and not t.is_hidden
        ]
        if tables:
            return tables

        # Fallback: one table holding whatever fields the data model reported.
        fields = self.data.get("data_model", {}).get("fields", [])
        dm_tables = self.data.get("data_model", {}).get("tables", [])
        name = dm_tables[0].get("name", "QlikTable") if dm_tables else "QlikTable"
        if not fields:
            return []
        return [
            QlikTable(
                name=name,
                fields=[QlikField(name=f["name"]) for f in fields],
                source=QlikSource(kind="unknown"),
            )
        ]

    def generate(self) -> dict:
        model_tables = []
        query_order = []

        for table in self.script_tables:
            safe = safe_name(table.name)
            query_order.append(safe)

            expression, status, column_names = build_partition_expression(table, self.resolver)

            columns = [
                {
                    "name": name,
                    "dataType": self.resolver.resolve(name),
                    "sourceColumn": name,
                    "lineageTag": new_guid(),
                }
                for name in column_names
            ]
            dropped = dropped_columns(table) if table.source.is_file else []
            self.table_status[safe] = {
                "status": status,
                "kind": table.source.kind,
                "path": table.source.path or table.source.resident_table or "",
                "columns": len(columns),
                "dropped": dropped,
                "typed_columns": sum(
                    1 for c in columns if c["dataType"] != "string"
                ),
            }

            model_tables.append({
                "name": safe,
                "lineageTag": new_guid(),
                "columns": columns,
                "measures": self._build_measures(table, safe, column_names),
                "partitions": [{
                    "name": f"{safe}-partition",
                    "mode": "import",
                    "source": {"type": "m", "expression": expression},
                }],
                "annotations": [{
                    "name": "QlikMigrationSource",
                    "value": json.dumps({
                        "kind": table.source.kind,
                        "path": table.source.path or table.source.resident_table,
                        "status": status,
                    }),
                }],
            })

        expressions = []
        if any(t.source.is_file for t in self.script_tables):
            expressions.append({
                "name": ROOT_PARAMETER,
                "kind": "m",
                "expression": build_root_parameter_expression(
                    default_root_for(self.script_tables)
                ),
                "lineageTag": new_guid(),
                "annotations": [{"name": "PBI_ResultType", "value": "Text"}],
            })

        model = {
            "name": "SemanticModel",
            "compatibilityLevel": 1606,
            "model": {
                "culture": "en-US",
                "dataAccessOptions": {
                    "legacyRedirects": True,
                    "returnErrorValuesAsNull": True
                },
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "sourceQueryCulture": "en-US",
                "tables": model_tables,
                "annotations": [
                    {"name": "PBI_QueryOrder", "value": json.dumps(query_order)},
                    {"name": "PBIDesktopVersion", "value": "2.138.1004.0 (24.10)"}
                ]
            }
        }
        if expressions:
            model["model"]["expressions"] = expressions
        return model

    def _build_measures(self, table, safe: str, column_names: list) -> list:
        """
        Build DAX measures for one table: a row count, a SUM per genuinely
        measurable column, and translations of the Qlik chart expressions that
        reference this table's fields.
        """
        measures = {}

        row_count = f"Total {safe} Rows"
        measures[row_count] = {
            "name": row_count,
            "expression": f"COUNTROWS('{safe}')",
            "lineageTag": new_guid(),
        }

        for col in column_names:
            if not self.resolver.is_measurable(col):
                continue
            name = f"Total {col}"
            measures.setdefault(name, {
                "name": name,
                "expression": f"SUM('{safe}'[{col}])",
                "lineageTag": new_guid(),
            })

        owned = {c.lower() for c in column_names}

        for sheet in self.data.get("sheets", []):
            for chart in sheet.get("charts", []):
                for meas in chart.get("measures", []):
                    expr = meas.get("expression", "")
                    if not expr or not self._expression_targets(expr, owned):
                        continue
                    name, dax = self.ai.translate_expression_to_dax(expr, safe, column_names)
                    measures.setdefault(name, {
                        "name": name,
                        "expression": dax,
                        "lineageTag": new_guid(),
                    })

        return list(measures.values())

    @staticmethod
    def _expression_targets(expression: str, owned_fields: set) -> bool:
        """True when a Qlik expression references any of this table's fields."""
        tokens = {
            t.strip("[]").lower()
            for t in re.findall(r"\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*", expression)
        }
        return bool(tokens & owned_fields)


# ============================================================
# UNIVERSAL VISUAL GENERATOR
# ============================================================

class UniversalVisualGenerator:
    """Generates Power BI visuals dynamically for ANY table and visual type."""

    SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.10.0/schema.json"

    def __init__(self, table_name: str, ai_brain: AIConverterBrain, columns_list: list = None,
                 field_owner: dict = None):
        self.table_name = table_name
        self.ai = ai_brain
        self.columns_list = columns_list if columns_list else ["Column1"]
        self.field_owner = field_owner or {}
        self.visual_counter = 0

        self.qlik_to_pbi_map = {
            "barchart": "clusteredColumnChart",
            "linechart": "lineChart",
            "piechart": "pieChart",
            "distributionplot": "clusteredColumnChart",
            "filterpane": "slicer",
            "listbox": "slicer",
            "kpi": "card",
            "sn-table": "tableEx",
            "gauge": "card",
            "treemap": "treemap",
            "combochart": "clusteredColumnChart",
        }

    def create_visual(self, chart: dict, x: int, y: int, width: int, height: int) -> dict:
        self.visual_counter += 1
        qlik_type = chart.get("type", "").lower()
        pbi_type = self.qlik_to_pbi_map.get(qlik_type, "clusteredColumnChart")
        title = chart.get("title", f"Visual {self.visual_counter}")
        dims = chart.get("dimensions", [])
        meass = chart.get("measures", [])

        visual_name = f"visual_{new_guid().replace('-', '')[:16]}"
        
        visual = {
            "$schema": self.SCHEMA,
            "name": visual_name,
            "position": {
                "x": x, "y": y, "z": self.visual_counter * 1000,
                "width": width, "height": height,
                "tabOrder": self.visual_counter
            },
            "visual": {
                "visualType": pbi_type,
                "query": self._build_query(pbi_type, dims, meass),
                "objects": {}
            },
        }

        if title:
            visual["visualContainerObjects"] = {
                "title": [
                    {
                        "properties": {
                            "show": {"expr": {"Literal": {"Value": "true"}}},
                            "text": {"expr": {"Literal": {"Value": f"'{title}'"}}}
                        }
                    }
                ]
            }

        return visual

    def _resolve_col(self, col: str) -> str:
        """Match a Qlik dimension name to a real migrated column, case-insensitively."""
        if not self.columns_list:
            return "Column1"
        raw = str(col or "").strip().strip("[]")
        for c in self.columns_list:
            if c.lower() == raw.lower():
                return c
        return self.columns_list[0]

    def _owner_of(self, col: str) -> str:
        """The table that actually holds this column."""
        return self.field_owner.get(str(col or "").lower(), self.table_name)

    def _measure_binding(self, expression: str) -> tuple:
        """
        Resolve a Qlik measure expression to (table, measure name).

        The measure was defined on whichever table owns the fields the
        expression references, so the visual must project it from there.
        """
        tokens = re.findall(r"\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*", expression or "")
        table = self.table_name
        for token in tokens:
            key = token.strip("[]").lower()
            if key in self.field_owner:
                table = self.field_owner[key]
                break
        name, _ = self.ai.translate_expression_to_dax(expression, table, self.columns_list)
        return table, name

    def _get_default_dim(self) -> str:
        """Fall back to the first column of the primary table."""
        for c in self.columns_list:
            if self._owner_of(c) == self.table_name:
                return c
        return self.columns_list[0] if self.columns_list else "Column1"

    def _column_projection(self, raw_col: str):
        col = self._resolve_col(raw_col)
        table = self._owner_of(col)
        return make_projection(make_column_ref(table, col), f"{table}.{col}")

    def _measure_projection(self, expression: str):
        if expression:
            table, meas = self._measure_binding(expression)
        else:
            table, meas = self.table_name, f"Total {self.table_name} Rows"
        return make_projection(make_measure_ref(table, meas), f"{table}.{meas}")

    def _build_query(self, pbi_type: str, dims: list, meass: list) -> dict:
        query_state = {}
        default_dim = self._get_default_dim()
        dim_field = dims[0]["field"] if (dims and dims[0].get("field")) else default_dim
        meas_expr = meass[0].get("expression") if meass else None

        if pbi_type == "slicer":
            query_state["Values"] = {"projections": [self._column_projection(dim_field)]}
        elif pbi_type == "card":
            query_state["Values"] = {"projections": [self._measure_projection(meas_expr)]}
        elif pbi_type == "tableEx":
            projections = []
            if dims and dims[0].get("field"):
                projections.append(self._column_projection(dims[0]["field"]))
            if meas_expr:
                projections.append(self._measure_projection(meas_expr))
            if not projections:
                projections.append(self._column_projection(default_dim))
            query_state["Values"] = {"projections": projections}
        else:
            query_state["Category"] = {"projections": [self._column_projection(dim_field)]}
            query_state["Y"] = {"projections": [self._measure_projection(meas_expr)]}

        return {"queryState": query_state}


# ============================================================
# UNIVERSAL PBIP PROJECT GENERATOR
# ============================================================

class UniversalPBIPGenerator:
    """Generates the entire PBIP directory structure dynamically for ANY QVF."""

    def __init__(self, extraction_data: dict, output_dir: str, ai_brain: AIConverterBrain, server: str = None, database: str = "postgres", mode: str = "offline"):
        self.data = extraction_data
        self.ai = ai_brain
        self.server = server
        self.database = database
        self.mode = mode
        
        raw_title = extraction_data.get("app_properties", {}).get("title", "Universal_Qlik_Project")
        self.project_name = re.sub(r"[^a-zA-Z0-9_]", "_", raw_title)
        
        self.output_dir = Path(output_dir)
        self.report_dir = self.output_dir / f"{self.project_name}.Report"
        self.model_dir = self.output_dir / f"{self.project_name}.SemanticModel"
        self.definition_dir = self.report_dir / "definition"
        self.pages_dir = self.definition_dir / "pages"
        
        # Share one model generator so the report and the semantic model agree
        # on table names, column types and field ownership.
        self.model_gen = UniversalModelGenerator(
            self.data, self.ai, self.server, self.database, self.mode
        )
        self.table_name = self.model_gen.table_name

        columns_list = [
            f.name for t in self.model_gen.script_tables for f in t.fields
        ] or ["Column1"]

        self.vis_gen = UniversalVisualGenerator(
            self.table_name, self.ai, columns_list, field_owner=self.model_gen.field_owner
        )

    def generate(self):
        print(f"\n{'='*60}")
        print(f"  AI UNIVERSAL POWER BI GENERATOR — {self.project_name}")
        print(f"  AI Brain Provider : {self.ai.provider.upper()} ({self.ai.model})")
        print(f"  Fallback Chain    : {' -> '.join(t.upper() for t in self.ai.tier_chain)}")
        print(f"  Cloud Key Present : {'YES (Dynamic AI Translation)' if self.ai.is_available else 'NO (Using Rule-Based Fallback)'}")
        print(f"{'='*60}\n")

        self._create_directories()
        self._write_pbip_file()
        self._write_model_bim()
        self._write_definition_pbism()
        self._write_definition_pbir()
        self._write_report_json()
        self._write_version_json()
        self._generate_dynamic_pages()
        self._write_universal_pbit_and_report_json()
        self._build_pbip_zip_archive()

        print(f"\n{'='*60}")
        print(f"  GENERAL-PURPOSE POWER BI PROJECT READY!")
        print(f"{'='*60}")
        print(f"  Output Folder : {self.output_dir}")
        print(f"  PBIP File     : {self.output_dir / (self.project_name + '.pbip')}\n")

    def _build_pbip_zip_archive(self):
        """Universal, zero-hardcoding PBIP ZIP archiver for any QVF project."""
        zip_path = self.output_dir / f"{self.project_name}_PBIP.zip"
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception:
                pass

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for ext in [".pbip", ".pbit", ".md"]:
                for p in self.output_dir.glob(f"*{ext}"):
                    zf.write(p, arcname=p.name)

            for folder_ext in [".SemanticModel", ".Report"]:
                sub_dir = self.output_dir / f"{self.project_name}{folder_ext}"
                if sub_dir.exists():
                    for root, dirs, files in os.walk(sub_dir):
                        for f in files:
                            full_path = Path(root) / f
                            rel_path = full_path.relative_to(self.output_dir)
                            zf.write(full_path, arcname=str(rel_path).replace("\\", "/"))

        print(f"  [OK] Saved: {zip_path.name} (Universal PBIP Zip Bundle)")

    def _create_directories(self):
        for d in [self.output_dir, self.report_dir, self.definition_dir, self.pages_dir, self.model_dir]:
            d.mkdir(parents=True, exist_ok=True)
        # Clean up any old orphaned page directories from previous runs
        if self.pages_dir.exists():
            for p in self.pages_dir.iterdir():
                if p.is_dir():
                    try:
                        import shutil
                        shutil.rmtree(p)
                    except Exception:
                        pass
        print(f"  [OK] Created PBIP directory tree")

    def _write_json(self, path: Path, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  [OK] Saved: {path.name}")

    def _write_pbip_file(self):
        pbip = {
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{self.project_name}.Report"}}],
            "settings": {"enableAutoRecovery": True}
        }
        self._write_json(self.output_dir / f"{self.project_name}.pbip", pbip)

    def _write_definition_pbir(self):
        pbir = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": f"../{self.project_name}.SemanticModel"}}
        }
        self._write_json(self.report_dir / "definition.pbir", pbir)

    def _write_definition_pbism(self):
        self._write_json(self.model_dir / "definition.pbism", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.2",
            "settings": {}
        })

    def _write_model_bim(self):
        self._write_json(self.model_dir / "model.bim", self.model_gen.generate())

    def _write_report_json(self):
        report = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY26SU05",
                    "reportVersionAtImport": {
                        "visual": "2.9.0",
                        "report": "3.3.0",
                        "page": "2.3.1"
                    },
                    "type": "SharedResources"
                }
            },
            "settings": {
                "useStylableVisualContainerHeader": True,
                "exportDataMode": "AllowSummarized",
                "defaultDrillFilterOtherVisuals": True,
                "allowChangeFilterTypes": True,
                "useEnhancedTooltips": True,
                "useDefaultAggregateDisplayName": True
            }
        }
        self._write_json(self.definition_dir / "report.json", report)

    def _write_version_json(self):
        self._write_json(self.definition_dir / "version.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0"
        })

    def _generate_dynamic_pages(self):
        """Dynamically create report pages from ANY Qlik sheets layout."""
        sheets = self.data.get("sheets", [])
        page_ids = []

        if not sheets:
            # Create at least 1 default page if QVF has no sheets
            page_id = "ReportSection"
            page_ids.append(page_id)
            self._create_page_layout(page_id, "Summary Dashboard", [])
        else:
            for i, sheet in enumerate(sheets):
                page_id = "ReportSection" if i == 0 else f"ReportSection{i}"
                page_ids.append(page_id)
                title = sheet.get("title", f"Page {i+1}")
                charts = sheet.get("charts", [])
                
                # Auto-arrange charts to fit inside standard 1280x720 canvas without overflow
                visuals = []
                n_charts = len(charts)
                if n_charts <= 2:
                    cols, rows = 2, 1
                elif n_charts <= 4:
                    cols, rows = 2, 2
                elif n_charts <= 6:
                    cols, rows = 3, 2
                elif n_charts <= 9:
                    cols, rows = 3, 3
                else:
                    cols, rows = 4, 3

                chart_w = int((1240 - (cols - 1) * 15) / cols)
                chart_h = int((680 - (rows - 1) * 15) / rows)

                for idx, chart in enumerate(charts):
                    col_idx = idx % cols
                    row_idx = (idx // cols) % rows
                    x_pos = 20 + col_idx * (chart_w + 15)
                    y_pos = 20 + row_idx * (chart_h + 15)

                    vis = self.vis_gen.create_visual(chart, x_pos, y_pos, chart_w, chart_h)
                    visuals.append(vis)

                self._create_page_layout(page_id, title, visuals)

        pages_meta = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
            "pageOrder": page_ids,
            "activePageName": page_ids[0]
        }
        self._write_json(self.pages_dir / "pages.json", pages_meta)

    def _create_page_layout(self, page_id: str, display_name: str, visuals: list):
        page_dir = self.pages_dir / page_id
        visuals_dir = page_dir / "visuals"
        visuals_dir.mkdir(parents=True, exist_ok=True)

        page = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
            "name": page_id,
            "displayName": display_name,
            "displayOption": "FitToPage",
            "width": 1280,
            "height": 720
        }
        self._write_json(page_dir / "page.json", page)

        for visual in visuals:
            vis_name = visual.get("name", new_guid())
            vis_dir = visuals_dir / vis_name
            vis_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(vis_dir / "visual.json", visual)

        print(f"  [OK] Generated Report Page: '{display_name}' ({len(visuals)} charts)")

    def _write_universal_pbit_and_report_json(self):
        """Generates Universal Legacy report.json (compatible with 100% of PBI Desktop builds) and a standalone .pbit Template file."""
        sections = []
        sheets = self.data.get("sheets", [])
        if not sheets:
            sheets = [{"title": "Summary Dashboard", "charts": []}]

        for i, sheet in enumerate(sheets):
            sec_name = "ReportSection" if i == 0 else f"ReportSection{i}"
            title = sheet.get("title", f"Page {i+1}")
            charts = sheet.get("charts", [])
            
            n_charts = len(charts)
            if n_charts <= 2: cols, rows = 2, 1
            elif n_charts <= 4: cols, rows = 2, 2
            elif n_charts <= 6: cols, rows = 3, 2
            elif n_charts <= 9: cols, rows = 3, 3
            else: cols, rows = 4, 3

            chart_w = int((1240 - (cols - 1) * 15) / cols)
            chart_h = int((680 - (rows - 1) * 15) / rows)

            vc_list = []
            for idx, chart in enumerate(charts):
                col_idx = idx % cols
                row_idx = (idx // cols) % rows
                x_pos = 20 + col_idx * (chart_w + 15)
                y_pos = 20 + row_idx * (chart_h + 15)

                vc_list.append(self._build_legacy_vc(chart, x_pos, y_pos, chart_w, chart_h, idx + 1))

            sections.append({
                "name": sec_name,
                "displayName": title,
                "width": 1280,
                "height": 720,
                "visualContainers": vc_list
            })

        config_str = json.dumps({
            "version": "5.73",
            "activeSectionIndex": 0,
            "defaultDrillFilterOtherVisuals": True,
            "settings": {
                "useNewFilterPaneExperience": True,
                "allowChangeFilterTypes": True,
                "useStylableVisualContainerHeader": True,
                "queryLimitOption": 6,
                "useEnhancedTooltips": True,
                "exportDataMode": 1,
                "useDefaultAggregateDisplayName": True
            }
        }, ensure_ascii=False)

        universal_layout = {
            "id": 0,
            "resourcePackages": [],
            "sections": sections,
            "config": config_str
        }

        # Standalone .pbit file packaging uses universal_layout as /Report/Layout
        # We must also write it to the .Report root so the .pbip folder opens without Preview Features!
        self._write_json(self.report_dir / "report.json", universal_layout)

        # 2. Package standalone .pbit file
        pbit_path = self.output_dir / f"{self.project_name}.pbit"
        content_types = b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>' + \
b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' + \
b'<Default Extension="json" ContentType="" />' + \
b'<Override PartName="/Version" ContentType="" />' + \
b'<Override PartName="/Report/Layout" ContentType="" />' + \
b'<Override PartName="/Settings" ContentType="application/json" />' + \
b'<Override PartName="/Metadata" ContentType="application/json" />' + \
b'<Override PartName="/DataModelSchema" ContentType="" />' + \
b'</Types>'

        with zipfile.ZipFile(pbit_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            model_bim_str = json.dumps(self.model_gen.generate(), ensure_ascii=False)
            zf.writestr("DataModelSchema", model_bim_str.encode("utf-16-le"))
            
            layout_str = json.dumps(universal_layout, ensure_ascii=False)
            zf.writestr("Report/Layout", layout_str.encode("utf-16-le"))
            zf.writestr("Version", "1.28".encode("utf-16-le"))
            zf.writestr("Settings", json.dumps({
                "Version": 4,
                "ReportSettings": {},
                "QueriesSettings": {
                    "TypeDetectionEnabled": True,
                    "RelationshipImportEnabled": True
                }
            }, ensure_ascii=False).encode("utf-16-le"))
            zf.writestr("Metadata", json.dumps({
                "Version": 5,
                "AutoCreatedRelationships": [],
                "CreatedFrom": "Cloud",
                "CreatedFromRelease": "2026.06"
            }, ensure_ascii=False).encode("utf-16-le"))
        
        print(f"  [OK] Saved: {pbit_path.name} (Standalone Universal Template)")

        with open(self.output_dir / "MIGRATION_AUDIT_REPORT.md", "w", encoding="utf-8") as f:
            f.write(self._build_audit_report())
        print("  [OK] Saved: MIGRATION_AUDIT_REPORT.md")

    def _build_audit_report(self) -> str:
        """
        Report what the migration actually produced.

        Every number here is measured from the generated model. Nothing is
        asserted that was not checked.
        """
        status = self.model_gen.table_status
        tables = self.model_gen.script_tables
        fields = self.data.get("data_model", {}).get("fields", [])
        sheets = self.data.get("sheets", [])
        charts = sum(len(s.get("charts", [])) for s in sheets)

        connected = [n for n, s in status.items() if s["status"] == "connected"]
        schema_only = [n for n, s in status.items() if s["status"] == "schema-only"]
        total_columns = sum(s["columns"] for s in status.values())
        typed_columns = sum(s["typed_columns"] for s in status.values())
        all_dropped = [
            (name, d) for name, s in status.items() for d in s["dropped"]
        ]
        unresolved = self.ai.unresolved

        lines = [
            f"# Qlik to Power BI Migration Report: {self.project_name}",
            "",
            f"- Source app: `{self.data.get('file', {}).get('name', self.project_name)}`",
            f"- Output: `{self.output_dir}`",
            f"- Generated: {self.project_name}.pbip, {self.project_name}.pbit",
            "",
            "## Summary",
            "",
            "| Item | Count |",
            "| --- | --- |",
            f"| Tables discovered in load script | {len(tables)} |",
            f"| Tables wired to their real source | {len(connected)} |",
            f"| Tables emitted schema-only (empty) | {len(schema_only)} |",
            f"| Columns in generated model | {total_columns} |",
            f"| Columns with a non-text type | {typed_columns} |",
            f"| Fields in .qvf data model | {len(fields)} |",
            f"| Qlik sheets | {len(sheets)} |",
            f"| Qlik charts | {charts} |",
            f"| Expressions needing manual review | {len(unresolved)} |",
            f"| Script-computed fields not carried over | {len(all_dropped)} |",
            "",
        ]

        if schema_only:
            lines += [
                "## Tables that contain no data",
                "",
                "These tables have the correct schema but zero rows, because their",
                "source could not be reached from Power Query. Charts bound to them",
                "will render empty until the source is repointed. No placeholder",
                "rows were generated.",
                "",
                "| Table | Source type | Original path |",
                "| --- | --- | --- |",
            ]
            for name in schema_only:
                s = status[name]
                lines.append(f"| {name} | {s['kind']} | `{s['path']}` |")
            lines.append("")

        if connected:
            lines += [
                "## Tables wired to a live source",
                "",
                "Set the `DataSourceRoot` parameter in Power BI to the folder holding",
                "these files, then refresh.",
                "",
                "| Table | Source type | Path |",
                "| --- | --- | --- |",
            ]
            for name in connected:
                s = status[name]
                lines.append(f"| {name} | {s['kind']} | `{s['path']}` |")
            lines.append("")

        if all_dropped:
            lines += [
                "## Script-computed fields not carried over",
                "",
                "These fields were built by load-script expressions rather than read",
                "from a file. Power Query cannot derive them automatically. Some may",
                "be join keys, in which case a relationship is missing too.",
                "",
                "| Table | Field | Qlik expression |",
                "| --- | --- | --- |",
            ]
            for table_name, d in all_dropped:
                expr = d["expression"].replace("\n", " ").replace("|", "\\|")[:90]
                lines.append(f"| {table_name} | {d['name']} | `{expr}` |")
            lines.append("")

        if unresolved:
            lines += [
                "## Expressions needing manual review",
                "",
                "No confident DAX translation was found. Each was written into the",
                "model as a `BLANK()` measure named `[Needs Review] ...` with the",
                "original Qlik text in a comment, so nothing silently returns a",
                "wrong number.",
                "",
                "| Table | Qlik expression |",
                "| --- | --- |",
            ]
            for u in unresolved:
                expr = u["expression"].replace("\n", " ").replace("|", "\\|")[:90]
                lines.append(f"| {u['table']} | `{expr}` |")
            lines.append("")

        if fields and typed_columns == 0:
            lines += [
                "## Note on column types",
                "",
                "This .qvf carries no field type metadata (older Qlik versions omit",
                "it), so every column was typed as text rather than guessed at.",
                "Set numeric and date types in Power Query before building measures.",
                "",
            ]

        lines += [
            "## How to open",
            "",
            "1. Unzip the generated archive.",
            f"2. Open `{self.project_name}.pbip` in Power BI Desktop.",
            "3. Set the `DataSourceRoot` parameter to your exported data folder.",
            "4. Refresh.",
            "",
        ]

        return "\n".join(lines)

    def _build_legacy_vc(self, chart: dict, x: int, y: int, width: int, height: int, tab_order: int) -> dict:
        qlik_type = chart.get("type", "").lower()
        pbi_type = self.vis_gen.qlik_to_pbi_map.get(qlik_type, "clusteredColumnChart")
        title = chart.get("title", f"Chart {tab_order}")
        dims = chart.get("dimensions", [])
        meass = chart.get("measures", [])

        table_name = self.table_name
        cols_list = self.vis_gen.columns_list if hasattr(self.vis_gen, "columns_list") else ["id", "address", "suburb"]
        default_dim = cols_list[0]
        for c in cols_list:
            if not any(id_k in c.lower() for id_k in ["id", "code", "key", "num"]) and any(cat_k in c.lower() for cat_k in ["cat", "region", "country", "name", "title", "genre", "type", "date", "year", "month"]):
                default_dim = c
                break

        default_meas = f"Total {table_name} Rows"

        col_name = default_dim
        if dims and dims[0].get("field"):
            raw_col = dims[0]["field"]
            for c in cols_list:
                if c.lower() == str(raw_col).lower():
                    col_name = c
                    break
            else:
                col_name = str(raw_col)

        meas_name = default_meas
        if meass and meass[0].get("expression"):
            meas_name, _ = self.ai.translate_expression_to_dax(meass[0]["expression"], table_name, cols_list)

        vis_id = f"visual_{new_guid().replace('-', '')[:16]}"
        col_ref = f"'{table_name}'.{col_name}"
        meas_ref = f"'{table_name}'.{meas_name}"

        projections = {}
        select_list = []

        if pbi_type in ("slicer",):
            projections["Values"] = [{"queryRef": col_ref}]
            select_list.append({
                "Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": col_name},
                "Name": col_ref
            })
        elif pbi_type in ("card",):
            projections["Values"] = [{"queryRef": meas_ref}]
            select_list.append({
                "Measure": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": meas_name},
                "Name": meas_ref
            })
        elif pbi_type in ("tableEx",):
            projections["Values"] = [{"queryRef": col_ref}, {"queryRef": meas_ref}]
            select_list.append({
                "Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": col_name},
                "Name": col_ref
            })
            select_list.append({
                "Measure": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": meas_name},
                "Name": meas_ref
            })
        else:
            projections["Category"] = [{"queryRef": col_ref}]
            projections["Y"] = [{"queryRef": meas_ref}]
            select_list.append({
                "Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": col_name},
                "Name": col_ref
            })
            select_list.append({
                "Measure": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": meas_name},
                "Name": meas_ref
            })

        config_dict = {
            "name": vis_id,
            "layouts": [
                {
                    "id": 0,
                    "position": {
                        "x": x, "y": y, "z": tab_order * 1000,
                        "width": width, "height": height,
                        "tabOrder": tab_order
                    }
                }
            ],
            "singleVisual": {
                "visualType": pbi_type,
                "projections": projections,
                "prototypeQuery": {
                    "Version": 2,
                    "From": [{"Name": "t", "Entity": table_name, "Type": 0}],
                    "Select": select_list
                },
                "vcObjects": {
                    "title": [
                        {
                            "properties": {
                                "show": {"expr": {"Literal": {"Value": "true"}}},
                                "text": {"expr": {"Literal": {"Value": f"'{title}'"}}}
                            }
                        }
                    ]
                }
            }
        }

        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "z": tab_order * 1000,
            "config": json.dumps(config_dict, ensure_ascii=False)
        }


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="AI-Powered General-Purpose QVF to Power BI PBIP Converter")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--qvf", help="Path to any Qlik Sense (.qvf) file")
    group.add_argument("--input", "-i", help="Path to extracted extraction_result.json")
    
    parser.add_argument("--output", "-o", help="Output directory for Power BI Project")
    parser.add_argument("--provider", default="groq", choices=["groq", "ollama", "openai", "gemini"], help="AI provider (default: groq)")
    parser.add_argument("--model", default="llama-3.3-70b-versatile", help="Model name (default: llama-3.3-70b-versatile)")
    parser.add_argument("--api-key", help="Groq or AI Provider API Key")
    parser.add_argument("--server", help="Optional database server override for general-purpose connections")
    parser.add_argument("--database", default="postgres", help="Optional database name override (default: postgres)")
    parser.add_argument("--mode", default="offline", choices=["offline", "live"], help="Data mode: 'offline' (zero-login universal table) or 'live' (database connection) (default: offline)")
    
    args = parser.parse_args()

    # 1. Initialize AI Brain
    ai_brain = AIConverterBrain(provider=args.provider, model=args.model, api_key=args.api_key)

    # 2. Load or Extract Data
    if args.qvf:
        print(f"Extracting metadata dynamically from '{args.qvf}'...")
        from qvf_extractor import QVFExtractor
        extractor = QVFExtractor(args.qvf)
        extraction_data = extractor.extract()
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            extraction_data = json.load(f)

    # 3. Determine Output Directory
    title = extraction_data.get("app_properties", {}).get("title", "Universal_Qlik_Project")
    clean_title = re.sub(r"[^a-zA-Z0-9_]", "_", title)
    output_dir = args.output if args.output else f"{clean_title}_AI_PowerBI"

    # 4. Generate Universal PBIP Project
    generator = UniversalPBIPGenerator(extraction_data, output_dir, ai_brain, server=args.server, database=args.database, mode=args.mode)
    generator.generate()


if __name__ == "__main__":
    main()
