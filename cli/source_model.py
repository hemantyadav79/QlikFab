"""
Source-neutral schema model
===========================
The table/field/origin structures the engine works in, independent of which BI
platform the metadata was read out of.

These began life in `qlik_script_parser` as QlikTable / QlikField / QlikSource,
described a Qlik load script, and were named for it. A Tableau workbook
describes the same three things -- tables, the fields they carry, and where the
rows come from -- so the structures are shared rather than duplicated per
platform, and the generators downstream never learn which source produced them.

`SourceRelation` has no Qlik counterpart and is new here. Qlik associates tables
implicitly on shared field names, so a Qlik app has no joins to carry; Tableau
states its joins explicitly, and dropping them would produce a model that is
wrong rather than merely incomplete.

Nothing in this module invents data. Every field is populated from something the
source actually stated, or left at its default.
"""

from dataclasses import dataclass, field as dc_field


@dataclass
class TableOrigin:
    """Where a table's rows come from."""

    kind: str = "unknown"          # qvd | delimited | excel | inline | resident | sql | extract | live | unknown
    raw: str = ""                  # the source clause exactly as written
    path: str = ""                 # resolved path with any variables substituted
    connection: str = ""           # named connection, if any
    relative_path: str = ""        # path beneath the named connection
    options: dict = dc_field(default_factory=dict)
    resident_table: str = ""
    inline_text: str = ""

    @property
    def is_file(self) -> bool:
        return self.kind in ("qvd", "delimited", "excel", "xml", "json", "parquet")


@dataclass
class SourceField:
    """A single field carried by a table."""

    name: str                      # the name the field has after loading (alias wins)
    expression: str = ""           # what the source defined it as, verbatim
    is_derived: bool = False       # True when expression is not a bare column reference
    tags: list = dc_field(default_factory=list)


@dataclass
class SourceTable:
    """One table in the source's data model."""

    name: str
    fields: list = dc_field(default_factory=list)
    source: TableOrigin = dc_field(default_factory=TableOrigin)
    is_mapping: bool = False
    is_hidden: bool = False        # helper tables the source hides from users
    statement: str = ""            # the definition verbatim, for the audit trail

    @property
    def field_names(self) -> list:
        return [f.name for f in self.fields]


@dataclass
class SourceRelation:
    """An explicit join between two tables.

    Only sources that state their joins populate this. A source that associates
    tables implicitly emits none, and the engine reports the implicit links for
    the user to wire up by hand instead -- inventing a join that the source
    never stated would silently change the numbers.
    """

    left_table: str = ""
    left_field: str = ""
    right_table: str = ""
    right_field: str = ""
    join_kind: str = "inner"       # inner | left | right | full
    operator: str = "="
    raw: str = ""                  # the relation as the source expressed it


# Historical names. `qlik_script_parser` and the engine were written against
# these, and both are re-exported so the Qlik path needs no edit to keep working.
QlikSource = TableOrigin
QlikField = SourceField
QlikTable = SourceTable
