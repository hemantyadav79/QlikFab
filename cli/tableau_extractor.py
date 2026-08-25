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

# Tableau's own shelf controls. They occupy a field slot but name no column:
# 'Measure Names'/'Measure Values' are how Tableau pivots several measures onto
# one axis, and Power BI expresses that by projecting the measures themselves.
# Carried through as fields they would bind a visual to a column the model has
# no way to contain.
_PSEUDO_FIELDS = {
    ":measure names", "measure names", "measure values",
    ":measure values", "multiple values",
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
        # Datasources published as their own item on the server. Their rows are
        # not in this workbook; the caller fetches them separately.
        self.published_datasources = []

        # local-name -> caption, so a shelf reference like [Calculation_123]
        # can be reported under the name a user would recognise.
        self._caption_by_name = {}
        # The subset of the above that are calculated fields. A calculated field
        # reaches the model under its caption rather than its internal name, so
        # a shelf reference to one has to be rewritten before it can bind.
        self._calculated_captions = {}
        # worksheet name -> parsed chart, so a dashboard zone can find it.
        self._charts_by_worksheet = {}
        # (table, remote column) -> the local name that column ended up with.
        self._local_by_remote = {}
        # named-connection id -> where that connection actually points. A
        # workbook on a live connection packages no rows, so this is the only
        # record of where its data is; without it such a migration can only
        # ever produce an empty model.
        self._connections = {}

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

    def _collect_connections(self, datasource):
        """Records where each named connection points.

        A live-connection workbook carries no data at all -- the rows stay in
        the database -- so these attributes are the whole of what is known
        about them: class ('snowflake', 'redshift', ...), server, database,
        schema, warehouse and the user Tableau signs in as. The password is
        not among them and never is: Tableau strips embedded credentials on
        export, which is why reading such a workbook's data needs a credential
        of its own rather than one recovered from the file.
        """
        for named in datasource.findall(".//named-connection"):
            inner = named.find("connection")
            if inner is None:
                continue
            identifier = named.get("name") or ""
            if not identifier:
                continue
            self._connections[identifier] = {
                "id": identifier,
                "caption": named.get("caption") or "",
                "class": (inner.get("class") or "").strip().lower(),
                "server": (inner.get("server") or "").strip(),
                "port": (inner.get("port") or "").strip(),
                "database": (inner.get("dbname") or "").strip(),
                "schema": (inner.get("schema") or "").strip(),
                "warehouse": (inner.get("warehouse") or "").strip(),
                "role": (inner.get("role") or "").strip(),
                "username": (inner.get("username") or "").strip(),
                "authentication": (inner.get("authentication") or "").strip(),
                "service": (inner.get("service") or "").strip(),
            }

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

        # A datasource published separately on the server. Its rows live in that
        # item, not in this workbook -- a .twbx built on one packages no extract
        # at all, which otherwise looks identical to a live database connection
        # and is reported as one. Recorded so the caller can fetch the
        # datasource and read its extract instead.
        repository = datasource.find("repository-location")
        if repository is not None or (
                connection is not None and (connection.get("class") or "") == "sqlproxy"):
            self.published_datasources.append({
                "caption": caption,
                "name": name,
                "id": (repository.get("id", "") if repository is not None else ""),
                "revision": (repository.get("revision", "") if repository is not None else ""),
                "site": (repository.get("site", "") if repository is not None else ""),
            })

        relations = {}
        if connection is not None:
            self._collect_connections(datasource)
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

            self._calculated_captions[local_name] = column_caption
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
                # Where the rows really are. Carried through so a workbook with
                # no extract can still be read from its source.
                "connection": self._connections.get(meta.get("connection") or "", {}),
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

    @staticmethod
    def _shelf_references(text):
        """Every field reference on a shelf or encoding, as its own token.

        A shelf holds `/`-separated references, each either `[datasource].[field]`
        or a bare `[field]`, and the field is always the last bracketed group of
        its own reference. Splitting on the separator and taking that last group
        is what keeps the datasource token from being mistaken for a field,
        without having to recognise datasource naming conventions.

        The previous rule -- keep only tokens containing a colon -- worked for an
        aggregated field, which Tableau writes as a column instance
        (`[sum:Sales:qk]`), but silently discarded every unaggregated dimension,
        which it writes as the bare column (`[Region]`) with no colon anywhere.
        A bar chart of Sales by Region therefore arrived carrying its measure and
        no category at all, and a worksheet whose shelves held only dimensions
        arrived empty and was dropped as a visual with nothing to project.
        """
        references = []
        for segment in re.split(r"[/+]", text or ""):
            groups = re.findall(r"\[([^\[\]]+)\]", segment)
            if groups:
                references.append(groups[-1])
        return references

    def _worksheet_dependencies(self, view):
        """What the worksheet says it uses, from <datasource-dependencies>.

        This is the authoritative list: Tableau records every column a
        worksheet touches here, with its role, whichever shelf or Marks card
        slot it happens to sit in. Shelves alone miss anything placed only on
        Marks, which for a pie, treemap, map or text table is all of it.
        """
        dependencies = {}
        for block in view.findall("datasource-dependencies"):
            columns = {}
            for column in block.findall("column"):
                columns[_strip_brackets(column.get("name", ""))] = {
                    "role": (column.get("role") or "").lower(),
                    "datatype": (column.get("datatype") or "").lower(),
                    "caption": column.get("caption") or "",
                }
            for instance in block.findall("column-instance"):
                name = _strip_brackets(instance.get("name", ""))
                column = _strip_brackets(instance.get("column", ""))
                info = columns.get(column, {})
                if not name:
                    continue
                dependencies[name] = {
                    "column": column,
                    "role": info.get("role", ""),
                    "derivation": (instance.get("derivation") or "").lower(),
                    "caption": info.get("caption") or column,
                }
            # A column with no instance is still a dependency the sheet uses.
            for column_name, info in columns.items():
                dependencies.setdefault(column_name, {
                    "column": column_name,
                    "role": info.get("role", ""),
                    "derivation": "",
                    "caption": info.get("caption") or column_name,
                })
        return dependencies

    def _field_names(self, field):
        """(binding name, display name) for the field half of a shelf token.

        These are not always the same string, and using one where the other
        belongs is how a visual ends up bound to a column that does not exist.
        A physical column reaches the model under the name the connection
        reported, so a projection must bind to *that*, while the caption is only
        what the workbook chose to display. A calculated field is the other way
        round: nothing physical stands behind it, and the model builds it under
        its caption, so `Calculation_1697` is the wrong thing to bind to.
        """
        if field in self._calculated_captions:
            caption = self._calculated_captions[field]
            return caption, caption
        return field, self._caption_by_name.get(field, field)

    def _parse_shelf_reference(self, reference):
        """Reads one shelf entry into its field, aggregation and role.

        A reference looks like `[datasource].[sum:Sales:qk]`, or just
        `[sum:Sales:qk]`, or -- for an unaggregated dimension -- plain
        `[datasource].[Region]`. The instance form carries the aggregation, the
        field, and a type suffix; the bare form carries only the field.
        """
        references = self._shelf_references(reference)
        if not references:
            return None
        token = references[-1]

        segments = token.split(":")
        if len(segments) >= 3:
            prefix, field = segments[0].lower(), ":".join(segments[1:-1])
        elif len(segments) == 2:
            prefix, field = segments[0].lower(), segments[1]
        else:
            prefix, field = "", token

        field = _strip_brackets(field)
        if not field or field.lower() in _PSEUDO_FIELDS:
            # 'Measure Names'/'Measure Values' are Tableau's own pivot controls,
            # not columns. Binding a visual to them would project a field the
            # model does not contain.
            return None

        binding, display = self._field_names(field)

        if prefix in _AGGREGATIONS:
            return {"name": binding, "label": display,
                    "aggregation": prefix, "is_measure": True}
        if prefix in _DATE_PARTS:
            return {"name": binding, "label": display,
                    "aggregation": prefix, "is_measure": False}
        return {"name": binding, "label": display,
                "aggregation": prefix or "none", "is_measure": False}

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

        def add_measure(name, aggregation, label=None, encoding=""):
            if any(item["name"] == name for item in measures):
                return
            entry = {
                "name": name,
                # The engine's measure builder reads `expression`; the
                # aggregation Tableau stated is what it means. The binding name
                # goes in the expression, not the caption, because the DAX
                # translator resolves the bracketed token against the model's
                # real columns.
                "expression": "%s([%s])" % ((aggregation or "sum").upper(), name),
                "label": label or name,
                "aggregation": aggregation or "sum",
            }
            if encoding:
                entry["encoding"] = encoding
            measures.append(entry)

        def add_dimension(name, label=None, encoding=""):
            if any(item["name"] == name for item in dimensions):
                return
            entry = {"name": name, "field": name, "label": label or name}
            if encoding:
                entry["encoding"] = encoding
            dimensions.append(entry)

        # What the worksheet declares it uses, read up front so a shelf
        # reference that states no aggregation can be resolved against the role
        # the worksheet itself gave the column, rather than being assumed a
        # dimension because it merely looks like one.
        view = table.find("view")
        dependencies = self._worksheet_dependencies(view) if view is not None else {}

        def place(token, encoding=""):
            parsed = self._parse_shelf_reference("[%s]" % token)
            if not parsed:
                return
            declared = dependencies.get(token) or {}
            role = declared.get("role", "")
            derivation = declared.get("derivation", "")

            # A bare reference carries no aggregation, so its role is not stated
            # in the token itself. The worksheet's own declaration is the
            # authority; without it the reference stays a dimension, which is
            # what an unaggregated shelf entry almost always is.
            if parsed["aggregation"] == "none" and role == "measure":
                add_measure(parsed["name"],
                            derivation if derivation in _AGGREGATIONS else "sum",
                            parsed["label"], encoding)
                return

            if parsed["is_measure"]:
                add_measure(parsed["name"], parsed["aggregation"],
                            parsed["label"], encoding)
            else:
                add_dimension(parsed["name"], parsed["label"], encoding)

        for shelf in ("rows", "cols"):
            for token in self._shelf_references(table.findtext(shelf, "") or ""):
                place(token)

        # Encodings (colour, size, label, detail, text) carry further fields.
        # For a pie, treemap, map or text table these are the *only* fields --
        # such a worksheet has empty rows and cols shelves.
        for encoding in table.findall(".//encodings/*"):
            column = encoding.get("column")
            if not column:
                continue
            for token in self._shelf_references(column):
                place(token, encoding.tag)

        # Fallback: what the worksheet declares it depends on. Reached when the
        # shelves and Marks card between them named nothing this parser
        # recognised -- a worksheet built entirely from column-instances the
        # shelf syntax does not spell out, which would otherwise reach the
        # engine with no fields at all and be dropped as an empty visual.
        if not dimensions and not measures and dependencies:
            for instance, info in dependencies.items():
                column = info["column"]
                if not column or column.lower() in _PSEUDO_FIELDS:
                    continue
                binding, display = self._field_names(column)
                derivation = info["derivation"]
                if info["role"] == "measure" or derivation in _AGGREGATIONS:
                    add_measure(binding,
                                derivation if derivation in _AGGREGATIONS else "sum",
                                display)
                elif info["role"] == "dimension":
                    add_dimension(binding, display)
                else:
                    # Role unstated: read it off the instance's own prefix
                    # rather than assuming one.
                    place(instance)

        if not dimensions and not measures:
            # Recorded rather than passed on silently: the engine drops a visual
            # with nothing to project, and without this the page would just come
            # out empty with no explanation.
            self.problems.append(
                "Worksheet %r names no fields this parser could read, so it "
                "produces no visual." % name)

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

        if self.published_datasources:
            names = ", ".join(d["caption"] for d in self.published_datasources)
            print("      %d published datasource(s) referenced: %s"
                  % (len(self.published_datasources), names))
            self.problems.append(
                "This workbook reads from %d datasource(s) published separately on "
                "the server (%s). Their rows live in those items, not in the "
                "workbook, which is why it packages no extract."
                % (len(self.published_datasources), names))

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
