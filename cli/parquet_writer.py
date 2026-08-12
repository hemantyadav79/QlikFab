"""
Writes tables read from the Qlik engine to Parquet for staging into a Lakehouse.

Parquet rather than CSV because a Delta table carries real types: a CSV round
trip would make every column text, and the migrated model already goes to some
trouble to resolve types from the .qvf's own field metadata rather than guessing
from column names. Throwing that away at the last step would undo it.

Values arrive from QIX as text (with None for null) because that is what the
engine returns. Conversion here is deliberately conservative: a column becomes
numeric only when the migration already decided it is numeric *and* every value
present parses. One unparseable value leaves the whole column as text rather
than silently turning it into nulls -- a column of nulls looks like missing data
and is much harder to notice than a column of strings.
"""
import os
import sys


class ParquetUnavailable(RuntimeError):
    """pyarrow is not installed, so Parquet staging cannot be used."""


def _to_int(text):
    return int(str(text).strip())


def _to_float(text):
    return float(str(text).strip())


# The dataType values the model generator emits, and how to parse each. The
# Arrow type is named rather than built here so importing this module does not
# require pyarrow -- the caller may only want to know whether staging is
# possible. `_arrow_type` resolves it once pyarrow is in hand.
_PARSERS = {
    "int64": (_to_int, "int64"),
    "double": (_to_float, "float64"),
}


def _arrow_type(pa, name):
    return getattr(pa, name)()


def _convert(values, parser):
    """
    Parse every non-null value, or report the first one that refuses.

    Returns (converted, failure). `failure` is None on success; otherwise it is
    the offending value and the column is left as text by the caller.
    """
    out = []
    for value in values:
        if value is None or value == "":
            out.append(None)
            continue
        try:
            out.append(parser(value))
        except (TypeError, ValueError):
            return None, value
    return out, None


def build_arrow_table(columns, rows, column_types=None):
    """
    An Arrow table for these rows.

    `column_types` maps column name -> the model's dataType ('int64', 'double',
    'string', ...). Anything absent or unrecognised stays text.

    Returns (table, notes) where notes records any column that was kept as text
    because a value would not parse -- reported rather than silently coerced.
    """
    try:
        import pyarrow as pa
    except ImportError as err:              # noqa: BLE001
        raise ParquetUnavailable(
            "Staging to a Lakehouse needs the 'pyarrow' package, and this "
            "interpreter does not have it:\n\n    %s\n\n"
            "Install it there specifically:\n\n"
            '    "%s" -m pip install pyarrow'
            % (sys.executable, sys.executable)
        ) from err

    column_types = column_types or {}
    arrays, notes = [], []

    for index, name in enumerate(columns):
        values = [row[index] if index < len(row) else None for row in rows]
        parser, arrow_name = _PARSERS.get(
            str(column_types.get(name, "")).lower(), (None, None))

        if parser is not None:
            converted, failure = _convert(values, parser)
            if failure is None:
                # The type is pinned rather than inferred. An empty table, or a
                # column whose every value is null, infers Arrow `null` -- which
                # Delta has no equivalent for, and which written_types would then
                # report as 'string', telling the model to declare a type the
                # file does not hold. Both failures are silent.
                arrays.append(pa.array(converted, type=_arrow_type(pa, arrow_name)))
                continue
            notes.append(
                "Column %r was typed %s by the migration but holds %r, which is "
                "not a number; it was written as text rather than replaced with "
                "nulls." % (name, column_types.get(name), str(failure)[:40])
            )

        arrays.append(pa.array([None if v is None else str(v) for v in values],
                               type=pa.string()))

    return pa.Table.from_arrays(arrays, names=list(columns)), notes


# Arrow type -> the dataType a Tabular model must declare for that column.
_TMSL_TYPES = {"int64": "int64", "double": "double", "string": "string"}


def written_types(table):
    """
    The dataType each column was actually written as.

    A Direct Lake table reads the Delta files directly, so a column the model
    declares as int64 that was in fact written as text is not a cosmetic
    disagreement -- the model is describing data that is not there. The file is
    the authority, so the caller declares what this reports rather than what it
    hoped for.
    """
    result = {}
    for field in table.schema:
        name = str(field.type)
        result[field.name] = _TMSL_TYPES.get(name, "string")
    return result


def write_parquet(path, columns, rows, column_types=None):
    """
    Write one table to `path`. Returns (bytes_written, notes, actual_types).

    Snappy compression: it is what Fabric's Delta reader expects by default and
    cuts a wide fact table to a fraction of its Parquet size, which matters
    because every byte crosses the network twice (here, then into Delta).
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as err:              # noqa: BLE001
        raise ParquetUnavailable(
            "Staging to a Lakehouse needs the 'pyarrow' package, and this "
            "interpreter does not have it:\n\n    %s\n\n"
            "Install it there specifically:\n\n"
            '    "%s" -m pip install pyarrow'
            % (sys.executable, sys.executable)
        ) from err

    table, notes = build_arrow_table(columns, rows, column_types)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pq.write_table(table, path, compression="snappy")
    return os.path.getsize(path), notes, written_types(table)
