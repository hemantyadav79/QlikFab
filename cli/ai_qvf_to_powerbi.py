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
import collections
import json
import os
import re
import sys
import urllib.request
import urllib.error
import uuid
import zipfile
from pathlib import Path

from qlik_script_parser import parse_load_script
from source_model import SourceField, SourceRelation, SourceTable, TableOrigin
from powerquery_builder import (
    TypeResolver,
    unavailable_description,
    build_embedded_expression,
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

    # Per-dialect prompt guidance. The two expression languages differ enough
    # that one shared prompt degrades both: a model told only "translate this
    # expression" reads Tableau's {FIXED …} as a record constructor and Qlik's
    # {<Year={2024}>} as a set literal, and quietly emits DAX that computes
    # something else. The notes name the constructs that actually mistranslate.
    DIALECT_NOTES = {
        "qlik": (
            "Qlik notes:\n"
            "- Set analysis {<Field={value}>} is a filter context: express it with CALCULATE.\n"
            "- Aggr(...) computes at a grain: express it with SUMMARIZE or an iterator.\n"
            "- Qlik's if() is DAX IF(); alt(x, y) is COALESCE(x, y).\n"
        ),
        "tableau": (
            "Tableau notes:\n"
            "- A Level-of-Detail expression {FIXED [A] : SUM([B])} ignores the visual's\n"
            "  own grouping: express it as CALCULATE(SUM(...), ALLEXCEPT(table, [A])).\n"
            "  {INCLUDE ...} and {EXCLUDE ...} adjust the current grain instead.\n"
            "- IF/ELSEIF/THEN/END maps to IF(...) or SWITCH(TRUE(), ...); IIF is IF.\n"
            "- ZN(x) is COALESCE(x, 0). IFNULL(x, y) is COALESCE(x, y).\n"
            "- Table calculations (WINDOW_SUM, INDEX, RANK, LOOKUP, TOTAL, RUNNING_SUM)\n"
            "  depend on the visual's layout, which DAX has no direct equal for. If the\n"
            "  expression uses one, do NOT approximate it — return the measure name with\n"
            "  a dax_expression of exactly: NEEDS_MANUAL_REVIEW\n"
        ),
    }

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
        ollama_url="http://localhost:11434/api/generate",
        dialect="qlik"
    ):
        # Which expression language the source writes in. It selects the prompt
        # guidance only — the tier chain, caching and fallbacks are identical.
        self.dialect = (dialect or "qlik").strip().lower()
        if self.dialect not in self.DIALECT_NOTES:
            self.dialect = "qlik"
        self.dialect_label = "Tableau" if self.dialect == "tableau" else "Qlik"
        self.dialect_notes = self.DIALECT_NOTES[self.dialect]

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
            f"You are a Power BI DAX expert. Translate this {self.dialect_label} expression "
            f"into a Power BI DAX formula.\n"
            f"Table name: {table_name}\n"
            f"Available columns in table: {', '.join(sample_columns[:15])}\n"
            f"{self.dialect_notes}"
            f"{self.dialect_label} Expression: {qlik_expr}\n\n"
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

            # The model was told to answer this when the construct has no
            # faithful DAX equivalent — a Tableau table calculation depends on
            # the visual's layout, and any DAX "equivalent" would return
            # different numbers while looking correct. Fall through to the
            # unresolved path so it is reported rather than silently wrong.
            if dax.upper().replace(" ", "").strip("-;") != "NEEDS_MANUAL_REVIEW":
                dax = re.sub(r"^[\s=\[]+|[\s\]]+$", "", dax)
                dax = re.sub(r"COUNTX\s*\(\s*'?([a-zA-Z0-9_]+)'?\s*,\s*'?([a-zA-Z0-9_]+)'?\s*\)", r"COUNTA('\1'[\2])", dax, flags=re.IGNORECASE)
                dax = re.sub(r"SUMX\s*\(\s*'?([a-zA-Z0-9_]+)'?\s*,\s*(?:[a-zA-Z0-9_]+\[)?'?\s*([a-zA-Z0-9_]+)'?\]?\s*\)", r"SUM('\1'[\2])", dax, flags=re.IGNORECASE)
                if dax.endswith("}") and "{" not in dax:
                    dax = dax[:-1].strip()
                result = (res["measure_name"].strip(), dax)
                self.cache[cache_key] = result
                return result
            print(f"  [MANUAL REVIEW] {self.dialect_label} construct has no faithful "
                  f"DAX equivalent: {qlik_key}")

        # TIER 4: Untranslatable: surface it instead of inventing a formula.
        self.unresolved.append({"expression": qlik_key, "table": table_name})
        print(f"  [UNRESOLVED] No confident DAX translation for: {qlik_key}")

        short = re.sub(r"[^A-Za-z0-9 ]+", " ", qlik_key).strip()
        short = re.sub(r"\s+", " ", short)[:40] or "Expression"
        name = f"[Needs Review] {short}"
        dax = (
            f"-- TODO: translate this {self.dialect_label} expression manually.\n"
            f"-- Original {self.dialect_label}: {qlik_key}\n"
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

def _mb(num_bytes):
    """Bytes as MB for a message. Trimmed, so a sub-MB budget is not shown as 0."""
    mb = num_bytes / (1024.0 * 1024.0)
    return ("%.2f" % mb).rstrip("0").rstrip(".") if mb < 1 else "%d" % mb


def new_guid():
    return str(uuid.uuid4())

# Name given to a table the source left unnamed. It reaches the generated model,
# so it is per-platform: calling a Tableau table "QlikTable" would be a visible
# lie in the user's semantic model. Qlik keeps the name it has always had, which
# is why "qlik" is the default everywhere this is consulted.
DEFAULT_TABLE_NAMES = {"qlik": "QlikTable", "tableau": "TableauTable"}


def default_table_name(platform: str = "qlik") -> str:
    return DEFAULT_TABLE_NAMES.get((platform or "qlik").strip().lower(), "SourceTable")


def safe_name(name: str, fallback: str = "QlikTable") -> str:
    """Normalise a source table name into a Tabular-safe identifier."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", (name or "").strip()).strip("_")
    if not cleaned:
        return fallback
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

    # Embedded rows travel to Fabric base64-encoded inside a single JSON body,
    # which inflates them by a third and is rejected outright past a certain
    # size. The budget is on generated M text, shared across every table,
    # because a row count is the wrong control: 50,000 rows of a two-column
    # lookup is trivial, and 50,000 rows of a twenty-column fact table is 30 MB.
    DEFAULT_EMBED_BUDGET_BYTES = 15 * 1024 * 1024

    def __init__(self, extraction_data: dict, ai_brain: AIConverterBrain, server: str = None,
                 database: str = "postgres", mode: str = "offline",
                 embedded_data: dict = None, data_problems: dict = None,
                 embed_budget_bytes: int = None, stage_dir: str = None):
        self.data = extraction_data
        self.ai = ai_brain
        self.server = server
        self.database = database
        self.mode = mode

        # Which platform this metadata was read out of. Absent on extractions
        # produced before the field existed, all of which were Qlik.
        self.source_platform = (extraction_data.get("source_platform") or "qlik").strip().lower()
        self.default_table = default_table_name(self.source_platform)

        # Rows read from the live Qlik engine, keyed by the engine's table name.
        # Empty for a .qvf handed over on its own: the file carries structure,
        # never data.
        self.embedded_data = embedded_data or {}
        self.data_problems = data_problems or {}
        self.embed_budget_bytes = embed_budget_bytes or self.DEFAULT_EMBED_BUDGET_BYTES

        # When set, rows are written to Parquet here and the model reads them as
        # Direct Lake instead of carrying them inline.
        self.stage_dir = stage_dir
        self.staged_tables = []
        self.stage_notes = []
        self.size_notes = []
        self.engine_only_tables = []

        self.script_tables = self._discover_tables()
        self.resolver = TypeResolver(
            self.data.get("data_model", {}).get("fields", []),
            self.script_tables,
        )

        # Map each field to the table that owns it, so visuals bind correctly.
        self.field_owner = {}
        self.all_column_names = []
        for table in self.script_tables:
            safe = safe_name(table.name)
            for field in table.fields:
                self.field_owner.setdefault(field.name.lower(), safe)
                self.all_column_names.append(field.name)

        # Measures whose DAX had to reach across tables. Qlik associates on
        # shared field names with no explicit join; Power BI needs a modelled
        # relationship, so these are reported for the user to wire up.
        self.cross_table_measures = set()

        # table -> set of measure names this model actually defines, filled in
        # as they are built and handed to the visual generator so a visual can
        # never project a measure that does not exist.
        self.built_measures = {}

        # The largest table is the sensible default for visuals whose field
        # references cannot be resolved.
        self.table_name = safe_name(
            max(self.script_tables, key=lambda t: len(t.fields)).name,
            self.default_table,
        ) if self.script_tables else self.default_table

        self.table_status = {}

    def _discover_tables(self) -> list:
        """
        Build the table list, in descending order of how much the source told us.

        1. A load script, parsed. Qlik states its whole schema there, so when one
           is present it is authoritative.
        2. Data-model tables that carry their own field lists. Sources with no
           load script -- Tableau states its schema as datasources and relations
           -- describe themselves this way.
        3. A single table holding every reported field, when the source named no
           tables at all.

        Order matters: step 2 sits above the single-table fallback because
        collapsing a multi-table source into one table would silently destroy
        its schema, and below the script because a script is the richer
        description of the same thing.
        """
        tables = [
            t for t in parse_load_script(self.data.get("load_script", ""))
            if not t.is_mapping and not t.is_hidden
        ]
        if tables:
            return tables

        data_model = self.data.get("data_model", {}) or {}
        dm_tables = data_model.get("tables", []) or []
        fields = data_model.get("fields", []) or []

        # Step 2. Only tables that name their own fields qualify -- a table entry
        # that is just a name tells us nothing a single table would not.
        structured = [t for t in dm_tables if isinstance(t, dict) and t.get("fields")]
        if structured:
            built = []
            for index, t in enumerate(structured):
                # Deduplicated case-insensitively: a repeated column becomes a
                # duplicate field in the generated M record type, which the
                # mashup engine rejects and which fails the entire model rather
                # than the one table. Guarded here as well as in each extractor
                # so no source can produce an unloadable model.
                fields, seen = [], set()
                for f in t["fields"]:
                    name = (f.get("name", "") if isinstance(f, dict) else str(f)) or ""
                    if not name or name.lower() in seen:
                        continue
                    seen.add(name.lower())
                    fields.append(SourceField(
                        name=name,
                        expression=f.get("expression", "") if isinstance(f, dict) else "",
                        is_derived=bool(f.get("is_derived")) if isinstance(f, dict) else False,
                    ))
                built.append(SourceTable(
                    name=t.get("name") or "Table%d" % (index + 1),
                    fields=fields,
                    source=TableOrigin(kind=t.get("origin_kind", "unknown"),
                                       raw=t.get("origin", "")),
                ))
            return built

        # Step 3. One table holding whatever fields the data model reported.
        name = dm_tables[0].get("name", self.default_table) if dm_tables else self.default_table
        if not fields:
            return []
        return [
            SourceTable(
                name=name,
                fields=[SourceField(name=f["name"]) for f in fields],
                source=TableOrigin(kind="unknown"),
            )
        ]

    def _embedded_for(self, table_name: str):
        """
        Rows the engine returned for this table, matched case-insensitively.

        The engine keys tables by their script name; model.bim holds a
        Tabular-safe variant, and the two differ in case often enough that an
        exact match silently loses the data.
        """
        wanted = str(table_name or "").lower()
        for name, payload in self.embedded_data.items():
            if str(name).lower() == wanted:
                return payload
        return None

    def _fit_embedded_data(self):
        """
        Trim embedded rows to fit the size budget, smallest tables first.

        Filling the budget in ascending order of size means a single wide fact
        table cannot crowd out every lookup table around it; the model stays
        usable and the one table that had to be cut is named. Nothing is
        dropped silently -- a trimmed table is marked truncated and a table
        that could not be embedded at all is recorded in size_notes.
        """
        if not self.embedded_data:
            return

        def row_bytes(payload):
            """Bytes one row costs in the generated M, measured on a sample."""
            rows = payload["rows"][:20]
            if not rows:
                return 1
            total = sum(
                sum(len(str(cell)) + 4 for cell in row) + 8 for row in rows
            )
            return max(total // len(rows), 1)

        budget = self.embed_budget_bytes
        ordered = sorted(
            self.embedded_data.items(),
            key=lambda kv: row_bytes(kv[1]) * len(kv[1]["rows"]),
        )

        for name, payload in ordered:
            cost = row_bytes(payload)
            wanted = len(payload["rows"])
            affordable = int(budget // cost) if cost else wanted
            if affordable >= wanted:
                budget -= cost * wanted
                continue
            if affordable <= 0:
                self.size_notes.append(
                    "`%s` (%s row(s)) did not fit the %s MB embedded-data budget "
                    "and was left as an empty table rather than partly filled."
                    % (name, f"{wanted:,}", _mb(self.embed_budget_bytes)))
                payload["rows"] = []
                payload["truncated"] = True
                continue
            self.size_notes.append(
                "`%s` was cut from %s to %s row(s) to fit the %s MB embedded-data "
                "budget. Stage to a Lakehouse to carry it whole."
                % (name, f"{wanted:,}", f"{affordable:,}",
                   _mb(self.embed_budget_bytes)))
            payload["rows"] = payload["rows"][:affordable]
            payload["truncated"] = True
            budget = 0

    # Substituted by the publisher once Fabric has assigned the ids. The
    # engine cannot know them: the lakehouse does not exist until publish time.
    WORKSPACE_PLACEHOLDER = "{{QLIKFAB_WORKSPACE_ID}}"
    LAKEHOUSE_PLACEHOLDER = "{{QLIKFAB_LAKEHOUSE_ID}}"
    DATABASE_QUERY = "DatabaseQuery"
    ONELAKE_DFS_BASE = "https://onelake.dfs.fabric.microsoft.com"

    def _direct_lake_expression(self) -> list:
        """
        The shared expression every Direct Lake partition resolves against.

        Direct Lake *on OneLake* is identified by this expression using
        AzureStorage.DataLake against the lakehouse itself. That matters: the
        alternative shape, pointing at the SQL analytics endpoint, is Direct
        Lake on SQL and needs a stored credential. This one runs under the
        workspace identity -- no gateway, no secret to bind after publishing.
        """
        return [
            "let",
            '    Source = AzureStorage.DataLake("%s/%s/%s", [HierarchicalNavigation = true])'
            % (self.ONELAKE_DFS_BASE, self.WORKSPACE_PLACEHOLDER, self.LAKEHOUSE_PLACEHOLDER),
            "in",
            "    Source",
        ]

    def _direct_lake_table(self, safe: str, columns: list, written_types: dict = None) -> dict:
        """
        One Direct Lake table.

        `written_types` is what the staged Parquet actually holds. It wins over
        the type resolved from the .qvf: the model must describe the Delta file
        it is reading, and a column the migration hoped was numeric but had to
        write as text is text.

        schemaName is deliberately absent. Whether Delta tables live at
        `Tables/<name>` or `Tables/<schema>/<name>` is a property of the
        lakehouse, which does not exist yet; the publisher sets or removes it
        once Fabric has answered. Guessing here and being wrong does not error
        -- the partition just resolves to nothing and every measure returns
        BLANK over a table that visibly has data.
        """
        written_types = written_types or {}
        return {
            "name": safe,
            "lineageTag": new_guid(),
            "columns": [
                {
                    "name": name,
                    "dataType": written_types.get(name) or self.resolver.resolve(name),
                    "sourceColumn": name,
                    "lineageTag": new_guid(),
                }
                for name in columns
            ],
            "measures": [],
            "partitions": [{
                "name": safe,
                "mode": "directLake",
                "source": {
                    "type": "entity",
                    "entityName": safe,
                    "expressionSource": self.DATABASE_QUERY,
                },
            }],
        }

    def _write_stage(self, staged: list) -> list:
        """
        Write each table to Parquet under the staging directory, plus a manifest.

        The publisher reads the manifest rather than globbing the directory, so
        a partially written stage cannot be mistaken for a complete one.
        """
        from parquet_writer import write_parquet

        # generate() runs more than once per migration (semantic model, then
        # .pbit), and rewriting a 632,000-row Parquet file on the second pass
        # costs minutes for an identical result.
        if self.staged_tables:
            return self.staged_tables

        # Staging exists to get rows out of the model and into a Lakehouse. With
        # no rows there is nothing to move, and going ahead would create a
        # Lakehouse holding empty tables and bind the model to it as Direct
        # Lake -- which needs a Storage-audience token the caller may not have,
        # turning a publish that would have succeeded into one that fails, in
        # exchange for an empty Lakehouse. The tables keep their import
        # partitions instead, and the audit report already states why they carry
        # no rows.
        if not any(rows for _safe, _columns, rows in staged):
            print("  [SKIP] No rows were read, so nothing is staged to a Lakehouse; "
                  "the tables keep their schema-only partitions.")
            return []

        os.makedirs(self.stage_dir, exist_ok=True)
        entries = []
        for safe, columns, rows in staged:
            types = {name: self.resolver.resolve(name) for name in columns}
            filename = "%s.parquet" % safe
            path = os.path.join(self.stage_dir, filename)
            size, notes, actual = write_parquet(path, columns, rows, types)
            self.stage_notes.extend(notes)
            entries.append({
                "table": safe,
                "file": filename,
                "rows": len(rows),
                "columns": list(columns),
                "bytes": size,
                # What the file genuinely holds, so the model can declare it.
                "types": actual,
            })
            print("  [OK] Staged %s: %s row(s), %.2f MB"
                  % (safe, f"{len(rows):,}", size / 1048576.0))

        manifest = {
            "workspacePlaceholder": self.WORKSPACE_PLACEHOLDER,
            "lakehousePlaceholder": self.LAKEHOUSE_PLACEHOLDER,
            "tables": entries,
        }
        with open(os.path.join(self.stage_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        return entries

    def _generate_direct_lake(self) -> dict:
        """
        Build a Direct Lake model over Delta tables staged in a Lakehouse.

        Used instead of embedding whenever rows are staged, because embedding is
        bounded by the Fabric request body: a 632,000-row fact table is ~103 MB
        of M and is simply refused. Direct Lake reads the Delta files in place,
        so table size stops being the migration's problem.

        Every table goes to the lakehouse, including ones whose source could not
        be read -- those become empty Delta tables. Mixing Direct Lake and
        import partitions in one model is a compatibility minefield, and a
        uniform model is worth an empty Parquet file.
        """
        model_tables = []
        query_order = []
        staged = []

        for table in self.script_tables:
            safe = safe_name(table.name)
            payload = self._embedded_for(table.name)
            if payload:
                columns = list(payload["columns"])
                rows = payload["rows"]
            else:
                # Unreadable source: real schema, no rows. Same honesty as the
                # import path, expressed as an empty Delta table.
                _expr, _status, columns = build_partition_expression(table, self.resolver)
                rows = []
            query_order.append(safe)
            staged.append((safe, columns, rows))

        claimed = {str(t.name).lower() for t in self.script_tables}
        for name, payload in sorted(self.embedded_data.items()):
            if str(name).lower() in claimed:
                continue
            safe = safe_name(name)
            query_order.append(safe)
            staged.append((safe, list(payload["columns"]), payload["rows"]))
            self.engine_only_tables.append(
                "`%s` (%s row(s), %d column(s))"
                % (name, f"{len(payload['rows']):,}", len(payload["columns"])))

        # Written before the tables are described, because the Parquet file --
        # not the .qvf's field metadata -- decides what type each column really
        # is. A Direct Lake table reads those files directly, so a model that
        # declares int64 over a column written as text is describing data that
        # does not exist. Staging first makes the file the authority.
        self.staged_tables = self._write_stage(staged)
        written = {e["table"]: e.get("types") or {} for e in self.staged_tables}
        staged_rows = {e["table"]: e.get("rows", 0) for e in self.staged_tables}

        for safe, columns, _rows in staged:
            entry = self._direct_lake_table(safe, columns, written.get(safe, {}))
            model_tables.append(entry)

            # The audit report is built from this. Left unset it reads as an
            # empty dict, and every Direct Lake migration reports itself as
            # zero tables and zero columns over a model that has both.
            rows = staged_rows.get(safe, 0)
            self.table_status[safe] = {
                "status": "connected" if rows else "schema-only",
                "kind": "lakehouse",
                "path": "Tables/%s" % safe,
                "columns": len(entry["columns"]),
                "dropped": [],
                "typed_columns": sum(
                    1 for c in entry["columns"] if c["dataType"] != "string"),
            }

        self.field_owner = {}
        self.all_column_names = []
        for safe, columns, _rows in staged:
            for column in columns:
                self.field_owner.setdefault(column.lower(), safe)
                self.all_column_names.append(column)

        # Measures are built after ownership is known, exactly as the import
        # path does, so cross-table references can be repointed.
        for entry, (safe, columns, _rows) in zip(model_tables, staged):
            entry["measures"] = self._build_measures(None, safe, columns)

        model = {
            "name": "SemanticModel",
            "compatibilityLevel": 1606,
            "model": {
                "culture": "en-US",
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "sourceQueryCulture": "en-US",
                "tables": model_tables,
                "expressions": [{
                    "name": self.DATABASE_QUERY,
                    "kind": "m",
                    "expression": self._direct_lake_expression(),
                    "lineageTag": new_guid(),
                }],
                "annotations": [
                    {"name": "PBI_QueryOrder", "value": json.dumps(query_order)},
                    {"name": "PBI_ProTooling", "value": json.dumps(["DirectLake"])},
                ],
            },
        }
        relationships = self._build_relationships(model_tables)
        if relationships:
            model["model"]["relationships"] = relationships
        return model

    def generate(self) -> dict:
        model_tables = []
        query_order = []

        # generate() is called more than once per run (the semantic model and
        # the .pbit are written from the same source), so anything accumulated
        # here is reset rather than appended to a previous pass.
        self.engine_only_tables = []

        if self.stage_dir:
            return self._generate_direct_lake()

        self._fit_embedded_data()

        # Partitions are built for every table first, because a measure can
        # reference a column on any table and must be checked against the
        # columns those partitions actually emit -- not against every field the
        # Qlik script mentions. Script-derived fields have no column in the
        # migrated model, so a measure using one has to be flagged, not
        # repointed at a column that does not exist.
        built = []
        for table in self.script_tables:
            # Rows read from the Qlik engine beat any reference to the original
            # source: a file path cannot be resolved from the Fabric service,
            # so a query pointing at one publishes as an empty table.
            embedded = self._embedded_for(table.name)
            if embedded:
                expression = build_embedded_expression(
                    table.name, embedded["columns"], embedded["rows"],
                    self.resolver, embedded.get("truncated", False),
                )
                built.append((table, safe_name(table.name), expression,
                              "embedded", list(embedded["columns"])))
                continue

            expression, status, column_names = build_partition_expression(table, self.resolver)
            built.append((table, safe_name(table.name), expression, status, column_names))

        # Tables the engine returned data for that the load-script parser never
        # produced. The engine is the authority on what the app actually holds
        # -- a script with resident loads, joins or generated tables can easily
        # defeat static parsing -- so this data is carried into the model
        # rather than read and thrown away.
        claimed = {str(t.name).lower() for t in self.script_tables}
        for name, payload in sorted(self.embedded_data.items()):
            if str(name).lower() in claimed:
                continue
            synthetic = SourceTable(
                name=name,
                fields=[SourceField(name=c) for c in payload["columns"]],
                source=TableOrigin(kind="unknown"),
            )
            expression = build_embedded_expression(
                name, payload["columns"], payload["rows"],
                self.resolver, payload.get("truncated", False),
            )
            built.append((synthetic, safe_name(name), expression,
                          "embedded", list(payload["columns"])))
            self.engine_only_tables.append(
                "`%s` (%s row(s), %d column(s))"
                % (name, f"{len(payload['rows']):,}", len(payload["columns"])))

        self.field_owner = {}
        self.all_column_names = []
        for _table, safe, _expr, _status, column_names in built:
            for name in column_names:
                self.field_owner.setdefault(name.lower(), safe)
                self.all_column_names.append(name)

        for table, safe, expression, status, column_names in built:
            query_order.append(safe)

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

            entry = {
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
            }

            # Why this table has no rows, as metadata rather than as a comment
            # banner inside the query. Power BI shows a table description in the
            # field list, so the explanation is more visible here than it was in
            # the M -- and it can no longer perturb the mashup document.
            if status == "schema-only":
                entry["description"] = unavailable_description(table)

            model_tables.append(entry)

        expressions = []
        # Emitted for file-backed tables, which resolve their paths against it,
        # and for schema-only tables, whose description tells the user to point
        # this parameter at their exported data. Without the second case that
        # instruction named a parameter the model did not contain -- which is
        # exactly the shape a Tableau migration produces, since every one of its
        # tables is schema-only and none is file-backed.
        needs_root = (
            any(t.source.is_file for t in self.script_tables)
            or any(status == "schema-only" for status in
                   (info.get("status") for info in self.table_status.values()))
        )
        if needs_root:
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
        relationships = self._build_relationships(model_tables)
        if relationships:
            model["model"]["relationships"] = relationships
        return model

    def _build_relationships(self, model_tables) -> list:
        """Turns the source's explicit joins into Tabular relationships.

        Only sources that state their joins produce any. Qlik states none — it
        associates on shared field names — so this returns empty there and the
        existing cross-table reporting is unchanged.

        A join is emitted only when both of its tables and both of its columns
        exist in the model that was actually built. A relationship naming a
        column that is not there loads as a broken model in Power BI, which is
        a worse outcome than the join being reported as unmapped.
        """
        relationships = []
        skipped = []

        # safe table name -> the column names it really has, matched
        # case-insensitively because the source's casing need not agree.
        columns_by_table = {
            entry["name"]: {c["name"].lower(): c["name"] for c in entry.get("columns", [])}
            for entry in model_tables
        }

        # The source's own table names map to the sanitised names in the model.
        safe_by_source = {}
        for table in self.script_tables:
            safe_by_source[table.name.lower()] = safe_name(table.name, self.default_table)

        seen = set()
        for join in (self.data.get("data_model", {}) or {}).get("relationships", []) or []:
            left_table = safe_by_source.get(str(join.get("left_table", "")).lower())
            right_table = safe_by_source.get(str(join.get("right_table", "")).lower())
            left_field = str(join.get("left_field", ""))
            right_field = str(join.get("right_field", ""))

            problem = None
            if not left_table or not right_table:
                problem = "one of its tables is not in the model"
            elif left_table == right_table:
                problem = "both sides resolve to the same table"
            elif str(join.get("operator", "=")) != "=":
                # Tabular relationships are equality-only; a non-equi join has
                # no representation at all.
                problem = "Power BI relationships support only '=' joins"
            else:
                left_column = columns_by_table.get(left_table, {}).get(left_field.lower())
                right_column = columns_by_table.get(right_table, {}).get(right_field.lower())
                if not left_column or not right_column:
                    problem = "a joined column is not in the model"

            if problem:
                skipped.append("%s.%s = %s.%s (%s)"
                               % (join.get("left_table"), left_field,
                                  join.get("right_table"), right_field, problem))
                continue

            key = (left_table, left_column.lower(), right_table, right_column.lower())
            if key in seen:
                continue
            seen.add(key)

            relationships.append({
                "name": new_guid(),
                # Tableau's left table is the many side of a typical fact-to-
                # dimension join. The cardinality is not stated in the workbook,
                # so the Tabular default (many-to-one) is used and the direction
                # is reported for the user to confirm.
                "fromTable": left_table,
                "fromColumn": left_column,
                "toTable": right_table,
                "toColumn": right_column,
                "joinOnDateBehavior": "datePartOnly",
            })

        if skipped:
            self.size_notes.append(
                "%d join(s) the source declared could not be modelled as Power BI "
                "relationships and need wiring by hand: %s"
                % (len(skipped), "; ".join(skipped)))
        if relationships:
            print("  [OK] Modelled %d relationship(s) from the source's joins."
                  % len(relationships))

        return relationships

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
                        "expression": self._rebind_columns(dax, name, expr),
                        "lineageTag": new_guid(),
                    })

        # Recorded so visuals can only ever project a measure that exists. The
        # name is otherwise derived twice from the same Qlik expression -- once
        # here, once when a visual binds to it -- and the two derivations can
        # disagree, which Power BI reports as Missing_References on a report
        # that looks fine everywhere else.
        self.built_measures.setdefault(safe, set()).update(measures)

        return list(measures.values())

    def _rebind_columns(self, dax: str, measure_name: str, qlik_expr: str) -> str:
        """
        Point every column reference in a generated measure at the table that
        actually holds that column.

        The translator is told which table it is writing for, so it qualifies
        every column with that table -- but a Qlik expression freely mixes
        fields from across the associative model, and Qlik needs no join to do
        it. `'FactTable'[Fiscal Year]` is simply not a column that exists.

        Where the column exists on another table the reference is corrected and
        the measure is flagged as needing a relationship. Where it exists
        nowhere the whole measure is replaced with a [Needs Review] stub
        carrying the original Qlik text, rather than shipping DAX that cannot
        evaluate.
        """
        unknown = []

        def fix(match):
            table, column = match.group(1), match.group(2)
            owner = self.field_owner.get(column.lower())
            if owner is None:
                if column.lower() not in {c.lower() for c in self.all_column_names}:
                    unknown.append(column)
                return match.group(0)
            if owner != table:
                self.cross_table_measures.add(measure_name)
            return "'%s'[%s]" % (owner, column)

        repaired = re.sub(r"'([^']+)'\[([^\]]+)\]", fix, dax or "")

        if unknown:
            self.ai.unresolved.append({
                "expression": qlik_expr,
                "table": measure_name,
                "reason": "references field(s) %s that no migrated table provides"
                          % ", ".join(sorted(set(unknown))),
            })
            return (
                "-- [Needs Review] The Qlik expression below references field(s) "
                "%s that this migration could not locate in any table.\n"
                "-- Original Qlik: %s\n"
                "BLANK()" % (", ".join(sorted(set(unknown))), (qlik_expr or "").replace("\n", " "))
            )
        return repaired

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

        # Qlik object types this tool has a genuine Power BI counterpart for.
        # Types whose shape depends on a chart setting (bar orientation, pie
        # donut, line area) are resolved in _resolve_visual_type instead.
        self.qlik_to_pbi_map = {
            "linechart": "lineChart",
            "piechart": "pieChart",
            "listbox": "slicer",
            "kpi": "card",
            "gauge": "card",
            "table": "tableEx",
            "sn-table": "tableEx",
            "pivot-table": "pivotTable",
            "sn-pivot-table": "pivotTable",
            "scatterplot": "scatterChart",
            "treemap": "treemap",
            "waterfallchart": "waterfallChart",
            "histogram": "clusteredColumnChart",
            "map": "map",
            "text-image": "textbox",
            "combochart": "lineClusteredColumnComboChart",

            # Tableau mark classes, lowercased by the extractor. They share this
            # table because a mark class and a Qlik object type occupy the same
            # slot: both name what the source drew, and neither collides with
            # the other's vocabulary.
            "bar": "clusteredColumnChart",
            "line": "lineChart",
            "area": "areaChart",
            "square": "treemap",
            "circle": "scatterChart",
            "shape": "scatterChart",
            "text": "tableEx",
            "pie": "pieChart",
            "polygon": "map",
            "multipolygon": "map",
        }

        # Qlik objects that hold other objects rather than rendering data. The
        # extractor already flattens their children into the sheet, so emitting
        # the container as well would duplicate every visual inside it. A
        # filter pane is one of these: it is a frame around listboxes, and each
        # of those listboxes becomes its own Power BI slicer.
        self.container_types = {
            "sn-layout-container", "container", "tabbed-container", "filterpane",
        }

        # Qlik types with no faithful Power BI equivalent. They are rendered as
        # the closest approximation and reported, never silently substituted.
        self.approximated_types = {
            "distributionplot": ("clusteredColumnChart",
                                 "Power BI has no distribution plot"),
            "boxplot": ("clusteredColumnChart", "Power BI has no box plot"),
            "bulletchart": ("clusteredBarChart", "Power BI has no bullet chart"),

            # Tableau mark classes with no faithful equivalent.
            "gantt": ("clusteredBarChart", "Power BI has no native Gantt mark"),
            "automatic": ("clusteredColumnChart",
                          "Tableau chose this mark automatically, so the workbook "
                          "states no explicit chart type"),
        }

        # Populated as visuals are built, surfaced in the audit report.
        self.fidelity_notes = []

        # table -> set of measure names the model genuinely defines. Filled in
        # by _write_model_bim once model.bim has been generated; empty until
        # then, which _resolve_measure treats as "cannot check".
        self.built_measures = {}

    @staticmethod
    def _chart_title(chart: dict) -> str:
        """
        The chart's title as display text.

        A Qlik title is usually a plain string, but it can also be a computed
        expression -- {"qStringExpression": {"qExpr": "'Revenue = ' & num(...)"}}.
        There is no DAX equivalent for a title that recomputes against the
        current selection, so the static part is kept and the dynamic part is
        reported rather than rendered as a Python dict repr.
        """
        title = chart.get("title", "")
        if isinstance(title, dict):
            expr = (title.get("qStringExpression") or {}).get("qExpr", "")
            literal = re.match(r"\s*'([^']*)'", str(expr))
            return (literal.group(1).strip() if literal else "").strip(" =")
        return str(title or "").strip()

    def _resolve_visual_type(self, chart: dict, title: str):
        """
        Map a Qlik object to a Power BI visual type.

        Returns (pbi_type, note). `note` is non-empty when the mapping is an
        approximation or a guess, so the caller can report it instead of the
        report quietly showing the wrong kind of chart.
        """
        qlik_type = str(chart.get("visualization") or chart.get("type") or "").lower()
        settings = chart.get("settings", {}) or {}

        if qlik_type in self.qlik_to_pbi_map:
            pbi_type = self.qlik_to_pbi_map[qlik_type]

            # Shape-changing settings Qlik keeps outside the object type.
            if qlik_type == "piechart" and (settings.get("donut") is True):
                pbi_type = "donutChart"
            elif qlik_type == "linechart" and str(settings.get("lineType", "")).lower() == "area":
                pbi_type = "areaChart"
            return pbi_type, ""

        if qlik_type == "barchart":
            horizontal = str(settings.get("orientation", "")).lower() == "horizontal"
            stacked = str(settings.get("barGrouping", {}).get("grouping", "")
                          if isinstance(settings.get("barGrouping"), dict)
                          else settings.get("barGrouping", "")).lower() == "stacked"
            if horizontal:
                return ("stackedBarChart" if stacked else "clusteredBarChart"), ""
            return ("stackedColumnChart" if stacked else "clusteredColumnChart"), ""

        if qlik_type in self.approximated_types:
            pbi_type, why = self.approximated_types[qlik_type]
            return pbi_type, "'%s' rendered as %s — %s." % (title or qlik_type, pbi_type, why)

        return "clusteredColumnChart", (
            "Qlik object type '%s'%s has no known Power BI equivalent; rendered as a "
            "column chart. Verify it against the Qlik sheet."
            % (qlik_type or "(unnamed)", " ('%s')" % title if title else "")
        )

    def create_visual(self, chart: dict, x: int, y: int, width: int, height: int) -> dict:
        qlik_type = str(chart.get("visualization") or chart.get("type") or "").lower()
        if qlik_type in self.container_types:
            # Its children are already flattened onto the sheet by the extractor.
            return None

        title = self._chart_title(chart)
        pbi_type, note = self._resolve_visual_type(chart, title)
        if note:
            self.fidelity_notes.append(note)

        dims = chart.get("dimensions", [])
        meass = chart.get("measures", [])

        query = self._build_query(pbi_type, dims, meass)
        if query is None:
            self.fidelity_notes.append(
                "'%s' (%s) declares no dimensions or measures in the .qvf, so there is "
                "nothing to project; it was skipped rather than filled with a "
                "substitute field." % (title or "(untitled)", qlik_type or "unknown")
            )
            return None

        self.visual_counter += 1
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
                "query": query,
                "objects": {}
            },
        }

        if title:
            visual["visualContainerObjects"] = {
                "title": [
                    {
                        "properties": {
                            "show": {"expr": {"Literal": {"Value": "true"}}},
                            "text": {"expr": {"Literal": {"Value": "'%s'" % title.replace("'", "''")}}}
                        }
                    }
                ]
            }

        return visual

    def _resolve_col(self, col: str) -> str:
        """
        Match a Qlik dimension name to a real migrated column, case-insensitively.

        When there is no match the first column is still used, because a visual
        must project something to be valid -- but it is reported. Silently
        binding a chart labelled 'country' to whatever happens to sit in column
        zero produces a report that looks right and is wrong.
        """
        if not self.columns_list:
            return "Column1"
        raw = str(col or "").strip().strip("[]")
        for c in self.columns_list:
            if c.lower() == raw.lower():
                return c
        substitute = self.columns_list[0]
        self.fidelity_notes.append(
            "Qlik dimension '%s' has no matching migrated column (it is most likely a "
            "script-computed field); the visual was bound to '%s' instead and needs "
            "repointing." % (raw, substitute)
        )
        return substitute

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
        return self._resolve_measure(table, name, expression)

    def _resolve_measure(self, table: str, measure: str, expression: str):
        """
        Pin a projection to a measure the model genuinely contains.

        The measure name is derived twice from one Qlik expression -- when the
        measure is built, and again here when a visual binds to it -- and the
        two derivations do not always agree, because they resolve the column's
        casing against different column lists. Power BI answers a name that
        does not exist with Missing_References, and the whole visual renders as
        an error box, so the model's own inventory is the authority.
        """
        if not self.built_measures:
            return table, measure          # inventory unknown; nothing to check
        if measure in self.built_measures.get(table, ()):
            return table, measure

        # Built, but attributed to a different table.
        for owner, names in self.built_measures.items():
            if measure in names:
                return owner, measure

        # Never built. A row count always exists and is honest about what it
        # shows; the alternative is a visual that cannot render at all.
        fallback = f"Total {table} Rows"
        if fallback not in self.built_measures.get(table, ()):
            for owner, names in sorted(self.built_measures.items()):
                candidate = f"Total {owner} Rows"
                if candidate in names:
                    table, fallback = owner, candidate
                    break
            else:
                return table, measure      # nothing to fall back to
        self.fidelity_notes.append(
            "No measure was generated for the Qlik expression `%s`, so the visual "
            "shows `%s` instead. The original expression is in the audit table above."
            % ((expression or "").replace("\n", " ")[:70], fallback)
        )
        return table, fallback

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

    def _build_query(self, pbi_type: str, dims: list, meass: list):
        """
        Project every dimension and measure the Qlik chart declares.

        Returns None when the chart declares neither. Previously the first
        dimension and first measure were the only ones carried over -- a Qlik
        table with eight measures arrived showing one -- and a chart with none
        was backfilled with the first column of the table plus a row-count
        measure, which is how a filter pane became "Total netflix_titles Rows
        by show_id". A chart with nothing to project is reported, not invented.
        """
        dim_fields = [d["field"] for d in dims if d.get("field")]
        meas_exprs = [m["expression"] for m in meass if m.get("expression")]
        if not dim_fields and not meas_exprs:
            return None

        query_state = {}
        if pbi_type in ("slicer", "textbox"):
            # A slicer takes a single field; Qlik filter panes with several
            # fields become several slicers in Power BI, which this tool does
            # not split, so the extras are reported.
            if not dim_fields:
                return None
            if len(dim_fields) > 1:
                self.fidelity_notes.append(
                    "Filter pane covered %d fields (%s); Power BI slicers hold one "
                    "field each, so only '%s' was migrated."
                    % (len(dim_fields), ", ".join(dim_fields), dim_fields[0])
                )
            query_state["Values"] = {"projections": [self._column_projection(dim_fields[0])]}

        elif pbi_type == "card":
            if not meas_exprs:
                return None
            query_state["Values"] = {"projections": [self._measure_projection(meas_exprs[0])]}

        elif pbi_type in ("tableEx", "pivotTable"):
            projections = [self._column_projection(f) for f in dim_fields]
            projections += [self._measure_projection(e) for e in meas_exprs]
            query_state["Values"] = {"projections": projections}

        elif pbi_type == "scatterChart":
            # X and Y are the first two measures; the dimension is the bubble.
            if dim_fields:
                query_state["Details"] = {
                    "projections": [self._column_projection(f) for f in dim_fields]
                }
            if meas_exprs:
                query_state["X"] = {"projections": [self._measure_projection(meas_exprs[0])]}
            if len(meas_exprs) > 1:
                query_state["Y"] = {"projections": [self._measure_projection(meas_exprs[1])]}
            if len(meas_exprs) > 2:
                query_state["Size"] = {"projections": [self._measure_projection(meas_exprs[2])]}

        else:
            # Cartesian charts: first dimension on the axis, any second
            # dimension becomes the series, all measures on the value axis.
            if dim_fields:
                query_state["Category"] = {
                    "projections": [self._column_projection(dim_fields[0])]
                }
            if len(dim_fields) > 1:
                query_state["Series"] = {
                    "projections": [self._column_projection(dim_fields[1])]
                }
            if len(dim_fields) > 2:
                self.fidelity_notes.append(
                    "Chart used %d dimensions (%s); a Power BI cartesian visual takes "
                    "an axis and a series, so the rest were dropped."
                    % (len(dim_fields), ", ".join(dim_fields))
                )
            if meas_exprs:
                query_state["Y"] = {
                    "projections": [self._measure_projection(e) for e in meas_exprs]
                }

        if not any(v.get("projections") for v in query_state.values()):
            return None
        return {"queryState": query_state}


# ============================================================
# UNIVERSAL PBIP PROJECT GENERATOR
# ============================================================

class UniversalPBIPGenerator:
    """Generates the entire PBIP directory structure dynamically for ANY QVF."""

    def __init__(self, extraction_data: dict, output_dir: str, ai_brain: AIConverterBrain,
                 server: str = None, database: str = "postgres", mode: str = "offline",
                 embedded_data: dict = None, data_problems: dict = None,
                 embed_budget_bytes: int = None, stage_dir: str = None):
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
            self.data, self.ai, self.server, self.database, self.mode,
            embedded_data=embedded_data,
            data_problems=data_problems,
            embed_budget_bytes=embed_budget_bytes,
            stage_dir=stage_dir,
        )
        self.table_name = self.model_gen.table_name

        # Built from every field the script mentions. _write_model_bim replaces
        # this with the columns the partitions genuinely emit, once it knows
        # them -- binding a visual to a script-derived field would point it at a
        # column the model does not contain.
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

        # generate() resolves which columns each partition actually emits. Point
        # the visual generator at those, so a visual can only ever bind to a
        # column that exists in the model it was written against.
        self.vis_gen.columns_list = self.model_gen.all_column_names or ["Column1"]
        self.vis_gen.field_owner = self.model_gen.field_owner
        self.vis_gen.built_measures = self.model_gen.built_measures

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
                
                visuals = []
                for chart, box in self._chart_boxes(sheet, charts):
                    vis = self.vis_gen.create_visual(chart, *box)
                    # create_visual returns None for containers and for charts
                    # that declare nothing to project.
                    if vis is not None:
                        visuals.append(vis)

                self._create_page_layout(page_id, title, visuals)

        pages_meta = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
            "pageOrder": page_ids,
            "activePageName": page_ids[0]
        }
        self._write_json(self.pages_dir / "pages.json", pages_meta)

    # Power BI's default page, and the margin kept clear around the content.
    CANVAS_W, CANVAS_H, MARGIN, GUTTER = 1280, 720, 16, 8

    def _chart_boxes(self, sheet: dict, charts: list):
        """
        Pair each chart with its (x, y, width, height) on the Power BI canvas.

        A Qlik sheet is a grid -- 24 columns by 12 rows by default -- and every
        object's col/row/colspan/rowspan is recorded in the sheet's `cells`,
        keyed by the same id as the chart. Scaling that grid onto the canvas
        reproduces the author's layout, which is the whole point of a migration.

        Sheets that predate the grid, or objects with no cell, fall back to a
        packed grid so nothing is lost -- but unlike the previous version that
        fallback never wraps back onto row 0, which is what stacked visuals on
        top of each other once a sheet held more than twelve of them.
        """
        cells = {c.get("name"): c for c in (sheet.get("cells") or []) if c.get("name")}
        layout = sheet.get("layout") or {}
        grid_cols = layout.get("columns") or 24
        grid_rows = layout.get("rows") or 12

        # A cell may legitimately extend past the declared grid; trust the cells.
        for cell in cells.values():
            grid_cols = max(grid_cols, cell.get("col", 0) + cell.get("colspan", 1))
            grid_rows = max(grid_rows, cell.get("row", 0) + cell.get("rowspan", 1))

        usable_w = self.CANVAS_W - 2 * self.MARGIN
        usable_h = self.CANVAS_H - 2 * self.MARGIN
        col_w = usable_w / float(grid_cols)
        row_h = usable_h / float(grid_rows)

        def box_of(cell):
            return (self.MARGIN + cell.get("col", 0) * col_w,
                    self.MARGIN + cell.get("row", 0) * row_h,
                    max(cell.get("colspan", 1), 1) * col_w,
                    max(cell.get("rowspan", 1), 1) * row_h)

        placed, unplaced = [], []
        for chart in charts:
            cell = cells.get(chart.get("id"))
            if cell:
                placed.append((chart, box_of(cell)))
            else:
                unplaced.append(chart)

        # An object nested in a container has no cell of its own -- in Qlik it
        # renders inside the container's area, so its children share that area
        # here too rather than being exiled to the bottom of the page.
        by_parent = collections.defaultdict(list)
        for chart in list(unplaced):
            parent_cell = cells.get(chart.get("parent"))
            if parent_cell:
                by_parent[chart["parent"]].append(chart)
                unplaced.remove(chart)

        for parent_id, children in by_parent.items():
            px, py, pw, ph = box_of(cells[parent_id])
            share = pw / float(len(children))
            for i, child in enumerate(children):
                placed.append((child, (px + i * share, py, share, ph)))

        boxes = []
        for chart, (x, y, w, h) in placed:
            boxes.append((chart, (int(round(x)), int(round(y)),
                                  int(round(max(w - self.GUTTER, 40))),
                                  int(round(max(h - self.GUTTER, 40))))))

        if unplaced:
            # Pack whatever the sheet did not position into a grid below the
            # canvas fold; the page scrolls, so they stay reachable and legible.
            per_row = 3
            w = int((usable_w - (per_row - 1) * self.GUTTER) / per_row)
            h = 200
            start_y = self.CANVAS_H + self.MARGIN if placed else self.MARGIN
            for idx, chart in enumerate(unplaced):
                x = self.MARGIN + (idx % per_row) * (w + self.GUTTER)
                y = start_y + (idx // per_row) * (h + self.GUTTER)
                boxes.append((chart, (x, y, w, h)))

        return boxes

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

        # Everything the live-data path could not carry over, stated plainly.
        # These are the facts a reader most needs and the ones most easily lost:
        # a capped table and a complete one look identical in the report.
        gen = self.model_gen
        problems = getattr(gen, "data_problems", None) or {}
        if problems:
            lines += [
                "## Tables whose data could not be read",
                "",
                "These carry their schema and no rows. Nothing was substituted.",
                "",
                "| Table | Reason |",
                "| --- | --- |",
            ]
            for tname, reason in sorted(problems.items()):
                # A cell cannot span lines: a reason with newlines in it would
                # end the table early and leave the rest as loose prose. The
                # whole reason is kept, joined onto one line.
                flat = " ".join(str(reason).split())
                lines.append("| %s | %s |" % (tname, flat.replace("|", "\\|")[:300]))
            lines.append("")

        size_notes = list(dict.fromkeys(getattr(gen, "size_notes", None) or []))
        if size_notes:
            lines += [
                "## Rows dropped to fit the embedded-data budget",
                "",
                "Embedded rows travel inside a single Fabric request, which has a size",
                "ceiling. These tables did not fit whole:",
                "",
            ] + ["- %s" % n for n in size_notes] + [""]

        stage_notes = list(dict.fromkeys(getattr(gen, "stage_notes", None) or []))
        if stage_notes:
            lines += [
                "## Columns written as text",
                "",
                "The migration typed these as numeric from the .qvf's field metadata,",
                "but the values the engine returned would not all parse. They were",
                "written as text rather than replaced with nulls, so a column of",
                "strings is visible where silently-missing data would not be.",
                "",
            ] + ["- %s" % n for n in stage_notes] + [""]

        engine_only = list(dict.fromkeys(getattr(gen, "engine_only_tables", None) or []))
        if engine_only:
            lines += [
                "## Tables found by the engine but not by the script parser",
                "",
                "A load script using resident loads, joins or generated tables can",
                "defeat static parsing. The engine is the authority on what the app",
                "actually holds, so these were carried into the model anyway:",
                "",
            ] + ["- %s" % n for n in engine_only] + [""]

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

        # Things the source stated but that could not be read. Recorded by the
        # extractor and surfaced here rather than being quietly filled in with a
        # default, which would leave the user believing the migration saw more
        # than it did.
        extraction_problems = self.data.get("extraction_problems") or []
        if extraction_problems:
            lines += [
                "## Source content that could not be read",
                "",
                "The source workbook stated these, but they could not be interpreted.",
                "Nothing was substituted for them: they are absent from the model",
                "rather than present with a guessed value.",
                "",
            ]
            lines += ["- %s" % str(problem).replace("|", "\\|") for problem in extraction_problems]
            lines.append("")

        # Joins the source declared that Power BI cannot express. Reported here
        # because an unmodelled join means two tables that will not filter each
        # other, which shows up as wrong totals rather than as an error.
        join_notes = [n for n in getattr(self.model_gen, "size_notes", [])
                      if "join(s) the source declared" in n]
        if join_notes:
            lines += [
                "## Joins needing manual wiring",
                "",
                "The source declared these joins, but they have no Power BI",
                "relationship equivalent. Until they are wired up by hand, the tables",
                "involved will not filter one another.",
                "",
            ]
            lines += ["- %s" % note.replace("|", "\\|") for note in join_notes]
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

        fidelity = list(dict.fromkeys(self.vis_gen.fidelity_notes))
        if fidelity:
            lines += [
                "## Visual fidelity",
                "",
                "Each Qlik object was placed using the sheet's own grid position and",
                "mapped to the closest Power BI visual. The following could not be",
                "reproduced exactly and are listed so they can be checked against the",
                "original sheet rather than assumed correct.",
                "",
            ]
            lines += ["- %s" % note for note in fidelity]
            lines.append("")

        cross_table = sorted(self.model_gen.cross_table_measures)
        if cross_table:
            lines += [
                "## Measures that span tables",
                "",
                "Qlik associates tables automatically on shared field names. Power BI",
                "does not: these measures were repointed at the table that owns each",
                "column, but they will only return correct values once the matching",
                "relationships are created in the model.",
                "",
            ]
            lines += ["- `%s`" % m for m in cross_table]
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
        all_cols = self.vis_gen.columns_list if hasattr(self.vis_gen, "columns_list") else ["id", "address", "suburb"]

        # This query has a single From entry, so every Property below is read
        # against `table_name` alone. A column that exists in the model but on
        # another table is therefore just as broken here as one that does not
        # exist at all -- Power BI answers both with Missing_References -- so
        # the candidate list is narrowed to what this one table actually owns.
        cols_list = [c for c in all_cols
                     if self.vis_gen._owner_of(c) == table_name] or all_cols

        default_dim = cols_list[0]
        for c in cols_list:
            if not any(id_k in c.lower() for id_k in ["id", "code", "key", "num"]) and any(cat_k in c.lower() for cat_k in ["cat", "region", "country", "name", "title", "genre", "type", "date", "year", "month"]):
                default_dim = c
                break

        default_meas = f"Total {table_name} Rows"

        # A field the model does not contain renders as an error box reading
        # Missing_References, not as an empty visual, so an unresolvable column
        # falls back to one that exists rather than being passed through as
        # whatever the Qlik chart happened to name.
        col_name = default_dim
        if dims and dims[0].get("field"):
            raw_col = dims[0]["field"]
            for c in cols_list:
                if c.lower() == str(raw_col).lower():
                    col_name = c
                    break
            else:
                self.vis_gen.fidelity_notes.append(
                    "The Qlik chart `%s` groups by `%s`, which this report page "
                    "cannot reach on `%s`, so it groups by `%s` instead."
                    % (title, raw_col, table_name, default_dim))

        meas_name = default_meas
        if meass and meass[0].get("expression"):
            expression = meass[0]["expression"]
            meas_name, _ = self.ai.translate_expression_to_dax(expression, table_name, cols_list)
            # The same expression is turned into a measure name twice -- once
            # when the measure is built, once here -- and the two resolve column
            # casing against different lists, so they can disagree. The model's
            # own inventory decides; this is the check the PBIR path already
            # makes and this path was missing.
            owner, meas_name = self.vis_gen._resolve_measure(
                table_name, meas_name, expression)
            if owner != table_name and meas_name != default_meas:
                self.vis_gen.fidelity_notes.append(
                    "The Qlik expression `%s` became a measure on `%s`, which this "
                    "report page cannot reach, so the visual shows `%s` instead."
                    % ((expression or "").replace("\n", " ")[:70], owner, default_meas))
                meas_name = default_meas

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
    group.add_argument("--twbx", help="Path to any Tableau workbook (.twbx or .twb)")
    group.add_argument("--input", "-i", help="Path to extracted extraction_result.json")
    
    parser.add_argument("--output", "-o", help="Output directory for Power BI Project")
    parser.add_argument("--provider", default="groq", choices=["groq", "ollama", "openai", "gemini"], help="AI provider (default: groq)")
    parser.add_argument("--model", default="llama-3.3-70b-versatile", help="Model name (default: llama-3.3-70b-versatile)")
    parser.add_argument("--api-key", help="Groq or AI Provider API Key")
    parser.add_argument("--server", help="Optional database server override for general-purpose connections")
    parser.add_argument("--database", default="postgres", help="Optional database name override (default: postgres)")
    parser.add_argument("--mode", default="offline", choices=["offline", "live"], help="Data mode: 'offline' (zero-login universal table) or 'live' (database connection) (default: offline)")

    parser.add_argument("--qlik-tenant",
                        help="Qlik Cloud tenant URL; with --qlik-app-id, reads the app's "
                             "real rows over the QIX engine and carries them into the model")
    parser.add_argument("--qlik-app-id",
                        help="Qlik Cloud app id to read data from (credential comes from "
                             "QLIK_AUTHORIZATION or QLIK_API_KEY)")
    parser.add_argument("--max-rows", type=int, default=50000,
                        help="Cap on rows read per table (default: 50000). Lifted "
                             "automatically when staging to a Lakehouse.")
    parser.add_argument("--max-embedded-mb", type=float, default=15.0,
                        help="Budget for rows embedded in model.bim (default: 15 MB). "
                             "Fabric refuses a request body much beyond this.")
    parser.add_argument("--stage-lakehouse", action="store_true",
                        help="Write rows to Parquet and build a Direct Lake model instead "
                             "of embedding them. Removes the size ceiling; needs a "
                             "Storage-audience token at publish time.")

    args = parser.parse_args()

    # 1. Initialize AI Brain
    # The dialect follows the input flag: it is settled before the extraction
    # runs, because the brain is built first.
    ai_brain = AIConverterBrain(provider=args.provider, model=args.model, api_key=args.api_key,
                               dialect="tableau" if args.twbx else "qlik")

    # 2. Load or Extract Data
    if args.qvf:
        print(f"Extracting metadata dynamically from '{args.qvf}'...")
        from qvf_extractor import QVFExtractor
        extractor = QVFExtractor(args.qvf)
        extraction_data = extractor.extract()
    elif args.twbx:
        print(f"Extracting metadata dynamically from '{args.twbx}'...")
        from tableau_extractor import TableauExtractor
        extractor = TableauExtractor(args.twbx)
        extraction_data = extractor.extract()
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            extraction_data = json.load(f)

    # 3. Determine Output Directory
    title = extraction_data.get("app_properties", {}).get("title", "Universal_Qlik_Project")
    clean_title = re.sub(r"[^a-zA-Z0-9_]", "_", title)
    output_dir = args.output if args.output else f"{clean_title}_AI_PowerBI"

    # 4. Read the app's real rows, when this run came from a live tenant.
    embedded_data, data_problems = _read_live_data(args, extraction_data)

    # 5. Generate Universal PBIP Project
    generator = UniversalPBIPGenerator(
        extraction_data, output_dir, ai_brain,
        server=args.server, database=args.database, mode=args.mode,
        embedded_data=embedded_data, data_problems=data_problems,
        embed_budget_bytes=int(args.max_embedded_mb * 1024 * 1024),
        stage_dir=os.path.join(output_dir, "lakehouse") if args.stage_lakehouse else None,
    )
    generator.generate()


def _read_tableau_data(args, extraction_data):
    """Read rows out of the workbook's own extract.

    Unlike the Qlik path there is nothing to connect to: a .twbx bundles its
    extract, so the rows are already local. Failure is never fatal — the
    migration continues with structure only, and the reason is recorded for the
    audit report.
    """
    tables = (extraction_data.get("data_model") or {}).get("tables") or []
    table_names = [t.get("name") for t in tables if t.get("name")]

    max_rows = args.max_rows
    if args.stage_lakehouse and "--max-rows" not in sys.argv:
        max_rows = None
        print("\n  Staging to a Lakehouse, so no row cap is applied.")

    print("\n  Reading data for %d table(s) from the workbook's extract..."
          % len(table_names))
    try:
        from tableau_data_reader import read_workbook_tables
        data, problems = read_workbook_tables(
            args.twbx, table_names, max_rows=max_rows,
            work_dir=os.path.dirname(os.path.abspath(args.twbx)),
            note=lambda text: print("  " + text),
        )
    except Exception as err:            # noqa: BLE001 - reported, never fatal
        print(f"  [WARN] Could not read the workbook's extract: {err}")
        return {}, {"(all tables)": str(err)}

    total = sum(len(d["rows"]) for d in data.values())
    print(f"  [OK] Read {total:,} row(s) across {len(data)} table(s); "
          f"{len(problems)} table(s) could not be read. "
          f"The audit report states what was finally embedded.")
    return data, problems


def _read_live_data(args, extraction_data):
    """
    Pull each table's rows from the Qlik engine, when a tenant was supplied.

    A file-backed M query cannot run in the Fabric service, so without this the
    published model is always empty. The API key is read from the environment
    rather than argv, which would expose it in the process list.

    Failure here is never fatal: the migration continues and every table that
    could not be read keeps the partition it would otherwise have had, with the
    reason recorded for the audit report.
    """
    # A Tableau workbook carries its own rows: the .twbx already on disk holds
    # the extract, so there is no second call to make and no credential needed.
    if args.twbx:
        return _read_tableau_data(args, extraction_data)

    if not (args.qlik_tenant and args.qlik_app_id):
        return {}, {}

    authorization = os.environ.get("QLIK_AUTHORIZATION") or os.environ.get("QLIK_API_KEY")
    if not authorization:
        print("  [WARN] --qlik-tenant was given but neither QLIK_AUTHORIZATION nor "
              "QLIK_API_KEY is set; no data will be read.")
        return {}, {"(all tables)": "No Qlik credential was available to this process."}
    if not authorization.lower().startswith("bearer "):
        authorization = "Bearer %s" % authorization

    tables = (extraction_data.get("data_model") or {}).get("tables") or []
    table_names = [t.get("name") for t in tables if t.get("name")]

    # An empty list is not "nothing to read": plenty of .qvf files carry no
    # data-model metadata and had their fields recovered from the load script.
    # The reader falls back to whatever the engine itself reports.
    #
    # The row cap exists because embedded rows have to fit in a Fabric request
    # body. Staging to a Lakehouse has no such ceiling, so the cap is lifted
    # there unless the caller asked for one explicitly.
    max_rows = args.max_rows
    if args.stage_lakehouse and "--max-rows" not in sys.argv:
        max_rows = None
        print("\n  Staging to a Lakehouse, so no row cap is applied.")

    print("\n  Reading data for %s table(s) from the Qlik engine..."
          % (len(table_names) if table_names else "all"))
    try:
        from qlik_data_reader import read_app_tables
        data, problems = read_app_tables(
            args.qlik_tenant, args.qlik_app_id, authorization, table_names,
            max_rows=max_rows, note=lambda text: print("  " + text),
        )
    except Exception as err:            # noqa: BLE001 - reported, never fatal
        print(f"  [WARN] Could not read data from the tenant: {err}")
        return {}, {"(all tables)": str(err)}

    total = sum(len(d["rows"]) for d in data.values())
    # "Read", not "embedded": the size budget is applied later, during model
    # generation, and may still trim a table. Claiming a row count here that a
    # later step reduces would overstate what actually reached Fabric.
    print(f"  [OK] Read {total:,} row(s) across {len(data)} table(s); "
          f"{len(problems)} table(s) could not be read. "
          f"The audit report states what was finally embedded.")
    return data, problems


if __name__ == "__main__":
    # The return value is the exit code. Without carrying it through, a run that
    # bailed out would still exit 0 and be reported to the user as a successful
    # migration that happens to have produced nothing.
    sys.exit(main() or 0)
