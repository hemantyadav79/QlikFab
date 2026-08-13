"""
Tableau Workbook Extractor
==========================
Reads a .twbx or .twb and produces the same extraction dict the Qlik path
produces, so everything downstream — model generation, DAX translation, visual
generation, PBIP emission — works on a Tableau workbook without knowing it.

A .twb is plain XML. A .twbx is a zip carrying that .twb plus its extracts and
images, so this is a far simpler read than the .qvf side, which has to recover
zlib streams out of a proprietary binary.

What is read, and from where:

    tables          <relation type='table'> inside each datasource connection
    joins           <relation type='join'> and its <clause> expressions
    columns         <metadata-record class='column'> — the authoritative list,
                    carrying the datatype the connection actually reported
    calculations    <column> elements holding a <calculation formula='…'>
    parameters      the special datasource named 'Parameters'
    charts          <worksheet> — its rows/cols shelves and mark class
    sheets          <dashboard> and its <zone> geometry

Nothing here invents data. A datatype always comes from what the workbook
states; it is never guessed from a field's name. Anything unparseable is
recorded in `extraction_problems` and surfaces in the audit report rather than
being filled in with a plausible-looking default.
"""

import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# Tableau's declared datatypes, mapped to the tag vocabulary TypeResolver
# already speaks. Reusing Qlik's tag names rather than inventing a parallel
# system is what lets the type resolver stay untouched.
_DATATYPE_TAGS = {
    "string":   (["$text", "$ascii"], False),
    "integer":  (["$numeric", "$integer"], True),
    "real":     (["$numeric"], True),
    "date":     (["$date"], False),
    "datetime": (["$timestamp"], False),
    "boolean":  (["$text"], False),
    "spatial":  (["$text"], False),
}

# Aggregation prefixes Tableau writes into a shelf reference. Anything in here
# marks the reference as a measure; `none` marks a dimension.
_AGGREGATIONS = {
    "sum", "avg", "min", "max", "cnt", "cntd", "median", "stdev", "stdevp",
    "var", "varp", "attr", "usr",
}

# Date truncations Tableau writes the same way. These are dimensions, not
# aggregates, even though they occupy the same slot in the reference.
_DATE_PARTS = {
    "yr", "qr", "mn", "dy", "wk", "hr", "mi", "sc",
    "tyr", "tqr", "tmn", "tdy", "twk", "thr", "tmi", "tsc",
    "mdy", "my", "md", "week", "weekday", "quarter", "month", "year", "day",
}


def _strip_brackets(value):
    """`[Orders]` -> `Orders`. Tableau brackets nearly every identifier."""
    text = (value or "").strip()
    while text.startswith("[") and text.endswith("]") and len(text) >= 2:
        text = text[1:-1].strip()
    return text


def _safe(value, default=""):
    return (value if value is not None else default)


class TableauExtractor:
    """Reads one Tableau workbook into the engine's extraction dict."""

    def __init__(self, workbook_path):
        self.path = Path(workbook_path)
        self.xml_bytes = b""
        self.twb_name = ""
        self.root = None

        self.tables = []            # [{name, fields:[{name,...}], origin, origin_kind}]
        self.fields = []            # flat list, the shape TypeResolver reads
        self.relationships = []     # explicit joins
        self.calculations = []      # calculated fields, for the DAX translator
        self.sheets = []            # dashboards -> charts
        self.variables = []         # Tableau parameters
        self.app_properties = {}
        self.problems = []          # everything that could not be read

        # local-name -> caption, so a shelf reference like [Calculation_123]
        # can be reported under the name a user would recognise.
        self._caption_by_name = {}
        # worksheet name -> parsed chart, so a dashboard zone can find it.
        self._charts_by_worksheet = {}
        # (table, remote column) -> the local name that column ended up with.
        self._local_by_remote = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _read_workbook_xml(self):
        """Pulls the .twb XML out of whichever container this is."""
        if not self.path.exists():
            raise FileNotFoundError("No workbook at %s" % self.path)

        suffix = self.path.suffix.lower()

        if suffix == ".twb":
            self.twb_name = self.path.name
            self.xml_bytes = self.path.read_bytes()
            return

        if suffix != ".twbx":
            # Not fatal on its own: a workbook downloaded without an extension
            # is still readable, so the container is sniffed instead of refused.
            self.problems.append(
                "Unexpected extension %r; treating the file as a packaged workbook."
                % suffix)

        if not zipfile.is_zipfile(self.path):
            # A .twbx that is not a zip is a bare .twb under the wrong name,
            # which happens when a workbook is saved without an extract.
            self.twb_name = self.path.name
            self.xml_bytes = self.path.read_bytes()
            return

        with zipfile.ZipFile(self.path) as archive:
            candidates = [n for n in archive.namelist() if n.lower().endswith(".twb")]
            if not candidates:
                raise ValueError(
                    "This .twbx carries no .twb workbook file, so there is no "
                    "structure to read. It holds: %s"
                    % ", ".join(archive.namelist()[:10]))
            # The workbook sits at the archive root; anything nested is a copy
            # bundled with the extract, so the shallowest wins.
            candidates.sort(key=lambda n: (n.count("/"), len(n)))
            self.twb_name = candidates[0]
            self.xml_bytes = archive.read(self.twb_name)

    def _parse_xml(self):
        try:
            self.root = ET.fromstring(self.xml_bytes)
        except ET.ParseError as err:
            raise ValueError("The workbook XML could not be parsed: %s" % err)
        if self.root.tag != "workbook":
            self.problems.append(
                "Root element is <%s>, not <workbook>; reading it anyway."
                % self.root.tag)

    # ------------------------------------------------------------------
    # Data model
    # ------------------------------------------------------------------

    def _collect_relations(self, node, datasource_name, found):
        """Walks the <relation> tree, collecting physical tables and joins.

        Tableau nests joins: a join relation holds its clause plus the two
        relations it joins, either of which may be another join. Recursion is
        the only way to reach the leaf tables of a three-table join.
        """
        for relation in node.findall("relation"):
            kind = (relation.get("type") or "").lower()

            if kind == "table":
                name = _strip_brackets(relation.get("name") or relation.get("table") or "")
                if name and name not in found:
                    found[name] = {
                        "name": name,
                        "table": _strip_brackets(relation.get("table") or ""),
                        "connection": relation.get("connection") or "",
                        "kind": "table",
                    }
            elif kind == "text":
                name = _strip_brackets(relation.get("name") or "")
                if name and name not in found:
                    found[name] = {"name": name, "table": "", "connection": "", "kind": "text"}
            else:
                # A join or union states its operands as nested relations, and
                # either operand may itself be a join. Recursing is the only way
                # to reach the leaf tables of a three-table join.
                if kind in ("join", "union"):
                    self._collect_joins(relation, datasource_name)
                elif kind and kind != "collection":
                    # Custom SQL and stored procedures name no table to read
                    # columns from; the metadata records still describe them.
                    name = _strip_brackets(relation.get("name") or "")
                    if name and name not in found:
                        found[name] = {"name": name, "table": "", "connection": "", "kind": kind}

            # Descend regardless of this node's type: a 'collection' wrapper
            # holds the real relations as children.
            self._collect_relations(relation, datasource_name, found)

    def _collect_joins(self, relation, datasource_name):
        """Records one join's operands, as the workbook states them."""
        join_kind = (relation.get("join") or "inner").lower()

        for clause in relation.findall("clause"):
            for expression in clause.findall(".//expression"):
                operands = [e.get("op", "") for e in expression.findall("expression")]
                operator = expression.get("op", "=")
                if len(operands) != 2:
                    continue
                left, right = operands
                left_table, left_field = self._split_reference(left)
                right_table, right_field = self._split_reference(right)
                if not (left_field and right_field):
                    self.problems.append(
                        "A join in datasource %r could not be read: %s %s %s"
                        % (datasource_name, left, operator, right))
                    continue
                self.relationships.append({
                    "left_table": left_table,
                    "left_field": left_field,
                    "right_table": right_table,
                    "right_field": right_field,
                    "join_kind": join_kind,
                    "operator": operator,
                    "raw": "%s %s %s" % (left, operator, right),
                })

    @staticmethod
    def _split_reference(reference):
        """`[Orders].[Order ID]` -> ('Orders', 'Order ID')."""
        parts = re.findall(r"\[([^\]]+)\]", reference or "")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        if len(parts) == 1:
            return "", parts[0]
        return "", ""

    def _parse_datasource(self, datasource):
        """Reads one <datasource> into tables, fields and calculations."""
        name = datasource.get("name", "")
        caption = datasource.get("caption", "") or name

        # The Parameters pseudo-datasource holds workbook parameters, not data.
        if name == "Parameters":
            self._parse_parameters(datasource)
            return

        connection = datasource.find("connection")
        relations = {}
        if connection is not None:
            self._collect_relations(connection, caption, relations)

        # Columns, grouped by the table the workbook says they belong to. The
        # metadata records are authoritative: they carry the datatype the
        # connection itself reported, which the <column> elements do not always.
        by_table = {}
        for record in datasource.findall(".//metadata-record"):
            if (record.get("class") or "") != "column":
                continue
            local_name = _strip_brackets(record.findtext("local-name", "") or "")
            if not local_name:
                continue
            parent = _strip_brackets(record.findtext("parent-name", "") or "")
            datatype = (record.findtext("local-type", "") or "").strip().lower()
            remote = (record.findtext("remote-name", "") or "").strip()

            table_name = parent or (list(relations) or [caption])[0]
            by_table.setdefault(table_name, [])

            # Tableau writes a metadata record per column *per connection*, so a
            # column reached through more than one connection appears more than
            # once. Emitting it twice would put a duplicate field in the M record
            # type -- `type table [#"Sales" = ..., #"Sales" = ...]` -- which the
            # mashup engine rejects outright, failing the whole model.
            if any(existing["name"].lower() == local_name.lower()
                   for existing in by_table[table_name]):
                continue

            by_table[table_name].append({
                "name": local_name,
                "datatype": datatype,
                "remote_name": remote,
            })
            self._register_field(local_name, datatype, table_name)

            # A join clause names the column as the source system does, while
            # the metadata record may have renamed it to keep it unique across
            # tables -- Superstore's Returns.[Order ID] becomes
            # [Order ID (Returns)]. Without this index the join would be
            # discarded for naming a column that "does not exist".
            if remote:
                self._local_by_remote[(table_name.lower(), remote.lower())] = local_name

        # Calculated fields and captions. A <column> with a <calculation> child
        # is a calculated field; without one it is a rename or a role override
        # of a physical column.
        for column in datasource.findall("column"):
            local_name = _strip_brackets(column.get("name", ""))
            column_caption = column.get("caption", "") or local_name
            if local_name:
                self._caption_by_name[local_name] = column_caption

            calculation = column.find("calculation")
            if calculation is None:
                continue
            formula = calculation.get("formula")
            if not formula:
                # A calculation with no formula is a reference to something the
                # workbook did not inline — reported, never guessed at.
                self.problems.append(
                    "Calculated field %r in %r states no formula, so it cannot be "
                    "translated." % (column_caption, caption))
                continue

            self.calculations.append({
                "name": column_caption,
                "internal_name": local_name,
                "datasource": caption,
                "formula": formula,
                "datatype": (column.get("datatype") or "").lower(),
                "role": (column.get("role") or "").lower(),
                "class": calculation.get("class", ""),
            })

        # Tables, in the order the workbook declares them.
        for table_name, meta in relations.items():
            self.tables.append({
                "name": table_name,
                "fields": by_table.get(table_name, []),
                "origin": meta.get("table") or table_name,
                "origin_kind": "extract" if meta.get("kind") == "table" else meta.get("kind", "unknown"),
                "datasource": caption,
            })

        # Columns whose parent table was never declared as a relation — a
        # federated or custom-SQL datasource does this. They still belong in the
        # model, under the datasource's own name.
        for table_name, columns in by_table.items():
            if any(t["name"] == table_name for t in self.tables):
                continue
            self.tables.append({
                "name": table_name,
                "fields": columns,
                "origin": table_name,
                "origin_kind": "unknown",
                "datasource": caption,
            })

    def _resolve_relationship_columns(self):
        """Rewrites join columns to the names the model will actually carry.

        Runs after every datasource is read, because a join is recorded from the
        connection block while the names it needs come from the metadata records
        further down the same datasource.
        """
        for join in self.relationships:
            for side in ("left", "right"):
                table = str(join.get("%s_table" % side, ""))
                field = str(join.get("%s_field" % side, ""))
                if not table or not field:
                    continue
                local = self._local_by_remote.get((table.lower(), field.lower()))
                if local and local != field:
                    join["%s_field" % side] = local
                    join.setdefault("renamed", []).append(
                        "%s.%s -> %s" % (table, field, local))

    def _register_field(self, name, datatype, table_name):
        """Adds one column to the flat field list TypeResolver reads."""
        tags, is_numeric = _DATATYPE_TAGS.get(datatype, (["$text"], False))
        if datatype and datatype not in _DATATYPE_TAGS:
            self.problems.append(
                "Column %r has datatype %r, which has no known mapping; treated "
                "as text." % (name, datatype))
        self.fields.append({
            "name": name,
            "source_table": [table_name] if table_name else [],
            "cardinality": 0,          # not stated in the workbook
            "total_count": 0,
            "is_numeric": is_numeric,
            "tags": tags,
            "byte_size": 0,
        })

    def _parse_parameters(self, datasource):
        for column in datasource.findall("column"):
            self.variables.append({
                "name": column.get("caption", "") or _strip_brackets(column.get("name", "")),
                "definition": column.get("value", ""),
                "datatype": (column.get("datatype") or "").lower(),
            })

    # ------------------------------------------------------------------
    # Worksheets and dashboards
    # ------------------------------------------------------------------

    def _parse_shelf_reference(self, reference):
        """Reads one shelf entry into (field_name, aggregation, is_measure).

        A reference looks like `[datasource].[sum:Sales:qk]`. The middle segment
        carries the aggregation, the field, and a type suffix.
        """
        parts = re.findall(r"\[([^\]]+)\]", reference or "")
        if not parts:
            return None
        token = parts[-1]

        segments = token.split(":")
        if len(segments) >= 3:
            prefix, field = segments[0].lower(), ":".join(segments[1:-1])
        elif len(segments) == 2:
            prefix, field = segments[0].lower(), segments[1]
        else:
            prefix, field = "", token

        field = _strip_brackets(field)
        # An internal name like Calculation_123 means nothing to a user; the
        # caption is what the worksheet actually displays.
        display = self._caption_by_name.get(field, field)

        if prefix in _AGGREGATIONS:
            return {"name": display, "aggregation": prefix, "is_measure": True}
        if prefix in _DATE_PARTS:
            return {"name": display, "aggregation": prefix, "is_measure": False}
        return {"name": display, "aggregation": prefix or "none", "is_measure": False}

    def _parse_worksheet(self, worksheet):
        name = worksheet.get("name", "")
        table = worksheet.find("table")
        if table is None:
            self.problems.append("Worksheet %r has no <table>, so it holds no view." % name)
            return None

        # Mark class decides the visual type. Tableau states it per pane; a
        # worksheet with several panes is a dual-axis chart, and the first pane
        # is the one the visual mapper is given.
        mark = ""
        for pane in table.findall(".//pane"):
            mark_element = pane.find("mark")
            if mark_element is not None and mark_element.get("class"):
                mark = mark_element.get("class")
                break

        dimensions, measures = [], []
        for shelf in ("rows", "cols"):
            text = table.findtext(shelf, "") or ""
            for reference in re.findall(r"\[[^\]]+\]\.\[[^\]]+\]", text):
                parsed = self._parse_shelf_reference(reference)
                if not parsed:
                    continue
                if parsed["is_measure"]:
                    measures.append({
                        "name": parsed["name"],
                        # The engine's measure builder reads `expression`; the
                        # aggregation Tableau stated is what it means.
                        "expression": "%s([%s])" % (parsed["aggregation"].upper(), parsed["name"]),
                        "label": parsed["name"],
                        "aggregation": parsed["aggregation"],
                    })
                else:
                    dimensions.append({
                        "name": parsed["name"],
                        "field": parsed["name"],
                        "label": parsed["name"],
                    })

        # Encodings (colour, size, label, detail) carry further fields. They are
        # read as dimensions so the visual can bind them, which is what Tableau
        # does with them on every mark type except text.
        for encoding in table.findall(".//encodings/*"):
            column = encoding.get("column")
            if not column:
                continue
            parsed = self._parse_shelf_reference(column)
            if not parsed:
                continue
            target = measures if parsed["is_measure"] else dimensions
            if not any(item["name"] == parsed["name"] for item in target):
                if parsed["is_measure"]:
                    target.append({
                        "name": parsed["name"],
                        "expression": "%s([%s])" % (parsed["aggregation"].upper(), parsed["name"]),
                        "label": parsed["name"],
                        "aggregation": parsed["aggregation"],
                    })
                else:
                    target.append({
                        "name": parsed["name"],
                        "field": parsed["name"],
                        "label": parsed["name"],
                        "encoding": encoding.tag,
                    })

        chart = {
            "id": name,
            "type": mark or "Automatic",
            # Read by UniversalVisualGenerator to pick the Power BI visual.
            "visualization": (mark or "Automatic").lower(),
            "title": name,
            "subtitle": "",
            "footnote": "",
            "showTitles": True,
            "dimensions": dimensions,
            "measures": measures,
            "settings": {"markClass": mark},
        }
        self._charts_by_worksheet[name] = chart
        return chart

    def _parse_dashboards(self):
        """Dashboards become sheets; their zones decide which charts land where."""
        placed = set()

        for dashboard in self.root.findall(".//dashboards/dashboard"):
            name = dashboard.get("name", "")
            charts = []
            for zone in dashboard.findall(".//zone"):
                worksheet_name = zone.get("name")
                if not worksheet_name or worksheet_name not in self._charts_by_worksheet:
                    continue
                chart = dict(self._charts_by_worksheet[worksheet_name])
                # Zone geometry is kept so the report page can reproduce the
                # dashboard's layout rather than re-flowing it into a grid.
                chart["layout"] = {
                    "x": _safe(zone.get("x"), ""),
                    "y": _safe(zone.get("y"), ""),
                    "w": _safe(zone.get("w"), ""),
                    "h": _safe(zone.get("h"), ""),
                }
                charts.append(chart)
                placed.add(worksheet_name)

            self.sheets.append({
                "name": name,
                "title": name,
                "charts": charts,
                "source": "dashboard",
            })

        # A worksheet on no dashboard is still content the user built, so it
        # becomes its own page rather than being silently dropped.
        for worksheet_name, chart in self._charts_by_worksheet.items():
            if worksheet_name in placed:
                continue
            self.sheets.append({
                "name": worksheet_name,
                "title": worksheet_name,
                "charts": [chart],
                "source": "worksheet",
            })

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def extract(self):
        print("\n%s" % ("=" * 60))
        print("  TABLEAU EXTRACTOR — %s" % self.path.name)
        print("%s\n" % ("=" * 60))

        print("[1/5] Reading workbook...")
        self._read_workbook_xml()
        print("      Workbook XML: %s (%d bytes)" % (self.twb_name, len(self.xml_bytes)))

        print("[2/5] Parsing XML...")
        self._parse_xml()

        print("[3/5] Reading datasources...")
        datasources = self.root.findall(".//datasources/datasource")
        for datasource in datasources:
            try:
                self._parse_datasource(datasource)
            except Exception as err:                    # noqa: BLE001
                self.problems.append(
                    "Datasource %r could not be read: %s"
                    % (datasource.get("caption") or datasource.get("name"), err))
        self._resolve_relationship_columns()
        print("      %d table(s), %d field(s), %d calculation(s), %d join(s)"
              % (len(self.tables), len(self.fields), len(self.calculations),
                 len(self.relationships)))

        print("[4/5] Reading worksheets...")
        for worksheet in self.root.findall(".//worksheets/worksheet"):
            try:
                self._parse_worksheet(worksheet)
            except Exception as err:                    # noqa: BLE001
                self.problems.append(
                    "Worksheet %r could not be read: %s" % (worksheet.get("name"), err))
        print("      %d worksheet(s)" % len(self._charts_by_worksheet))

        print("[5/5] Reading dashboards...")
        self._parse_dashboards()
        print("      %d sheet(s) built\n" % len(self.sheets))

        title = Path(self.twb_name).stem or self.path.stem
        self.app_properties = {
            "title": title,
            "description": "",
            "published": "",
        }

        if self.problems:
            print("  %d item(s) could not be read; they are recorded in the audit report."
                  % len(self.problems))

        return {
            "source_platform": "tableau",
            "file": {
                "name": self.path.name,
                "path": str(self.path.absolute()),
                "size_bytes": self.path.stat().st_size,
                "extracted_at": datetime.now().isoformat(),
                "workbook_xml": self.twb_name,
            },
            "header": {},
            "app_properties": self.app_properties,
            # Tableau has no load script. The tables below carry their own
            # fields, which is the branch _discover_tables() takes when this is
            # empty.
            "load_script": "",
            "data_model": {
                "tables": self.tables,
                "fields": self.fields,
                "meta": {},
                "relationships": self.relationships,
            },
            "sheets": self.sheets,
            "variables": self.variables,
            "calculations": self.calculations,
            "security_metadata": {},
            "extraction_problems": self.problems,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract a Tableau workbook's structure")
    parser.add_argument("workbook", help="Path to a .twbx or .twb")
    parser.add_argument("--output", "-o", help="Write the extraction JSON here")
    args = parser.parse_args()

    results = TableauExtractor(args.workbook).extract()

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, ensure_ascii=False)
        print("  [OK] Wrote %s" % args.output)
    else:
        print(json.dumps(results, indent=2, ensure_ascii=False)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
