"""Source column types for SQL-source ingest: map, serialise, and say what fell back.

Batch 1's bulk load quoted every value, so every SQL-sourced bronze column landed
``VARCHAR`` and a generated ``SUM(amount)`` failed to bind - the ask abstained on a
question the data could answer. The source database already says what each column is;
this module reads that from ``cursor.description`` and maps it to a DuckDB type.

Pure Python, no DuckDB handle: the connector imports it, and the connector may hold
none (``tests/invariants/test_extract_only.py``). ``dms_executor.bronze`` does the
landing and runs the DuckDB half of the check.

The rule is "exact or VARCHAR". A column whose values cannot all be carried exactly
into the mapped type - a Postgres ``NaN`` in a ``numeric``, a scale the declared type
does not hold, an unsigned 64-bit id beyond ``HUGEINT``, a zero date a driver returned
as text - lands ``VARCHAR`` for that column only, and a note naming the column and the
reason rides on the pull. Never a silently rounded or nulled value.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

#: DuckDB DECIMAL tops out at 38 digits.
MAX_DECIMAL_PRECISION = 38

_INT_RANGES: dict[str, tuple[int, int]] = {
    "SMALLINT": (-(2**15), 2**15 - 1),
    "INTEGER": (-(2**31), 2**31 - 1),
    "BIGINT": (-(2**63), 2**63 - 1),
    "HUGEINT": (-(2**127), 2**127 - 1),
}


@dataclass(frozen=True)
class ColumnType:
    """What the source said a column is, and what bronze will hold it as."""

    name: str
    #: The source's own name for the type (``numeric(12,4)``, ``int8``, ``oid 2950``).
    source_type: str
    #: ``VARCHAR``, ``BIGINT``, ``DECIMAL(12,4)``, ... ``DECIMAL`` alone means "precision
    #: and scale not declared; derive them from the values" (unconstrained ``numeric``).
    duck_type: str
    #: Set when the type is unknown to the mapping, so VARCHAR is a fallback, not a choice.
    note: str | None = None


def _varchar(name: str, source_type: str, *, known_text: bool) -> ColumnType:
    if known_text:
        note = None
    elif source_type == "unreported":
        note = "the driver reported no source type"
    else:
        note = f"no DuckDB mapping for source type {source_type}"
    return ColumnType(name=name, source_type=source_type, duck_type="VARCHAR", note=note)


def _decimal(name: str, source_type: str, precision: Any, scale: Any) -> ColumnType:
    p = int(precision) if isinstance(precision, int) and precision > 0 else None
    s = int(scale) if isinstance(scale, int) and scale >= 0 else None
    if p is None or s is None or p > MAX_DECIMAL_PRECISION or s > p:
        return ColumnType(name, source_type, "DECIMAL")
    return ColumnType(name, source_type, f"DECIMAL({p},{s})")


# --- PostgreSQL (psycopg): type_code is the type OID ---------------------------------

_PG_SIMPLE: dict[int, tuple[str, str]] = {
    16: ("bool", "BOOLEAN"),
    20: ("int8", "BIGINT"),
    21: ("int2", "SMALLINT"),
    23: ("int4", "INTEGER"),
    700: ("float4", "REAL"),
    701: ("float8", "DOUBLE"),
    1082: ("date", "DATE"),
    1083: ("time", "TIME"),
    1114: ("timestamp", "TIMESTAMP"),
    1184: ("timestamptz", "TIMESTAMPTZ"),
}
_PG_TEXT: dict[int, str] = {
    25: "text",
    1043: "varchar",
    1042: "bpchar",
    18: "char",
    19: "name",
}
_PG_NUMERIC = 1700


def _map_postgres(name: str, desc: Sequence[Any]) -> ColumnType:
    oid = desc[1] if len(desc) > 1 else None
    if not isinstance(oid, int):
        return _varchar(name, "unreported", known_text=False)
    if oid in _PG_SIMPLE:
        src, duck = _PG_SIMPLE[oid]
        return ColumnType(name, src, duck)
    if oid in _PG_TEXT:
        return _varchar(name, _PG_TEXT[oid], known_text=True)
    if oid == _PG_NUMERIC:
        p = desc[4] if len(desc) > 4 else None
        s = desc[5] if len(desc) > 5 else None
        src = "numeric" if p is None else f"numeric({p},{s or 0})"
        return _decimal(name, src, p, s if s is not None else 0)
    return _varchar(name, f"oid {oid}", known_text=False)


# --- MySQL (pymysql): type_code is a FIELD_TYPE constant -----------------------------

_MY_SIMPLE: dict[int, tuple[str, str]] = {
    1: ("tinyint", "SMALLINT"),
    2: ("smallint", "INTEGER"),  # unsigned smallint exceeds SMALLINT
    3: ("int", "BIGINT"),  # unsigned int exceeds INTEGER
    9: ("mediumint", "INTEGER"),
    8: ("bigint", "BIGINT"),  # unsigned bigint widens to HUGEINT on the values
    13: ("year", "SMALLINT"),
    4: ("float", "REAL"),
    5: ("double", "DOUBLE"),
    10: ("date", "DATE"),
    14: ("date", "DATE"),
    7: ("timestamp", "TIMESTAMP"),
    12: ("datetime", "TIMESTAMP"),
}
_MY_TEXT: dict[int, str] = {
    15: "varchar",
    253: "varchar",
    254: "char",
    247: "enum",
    248: "set",
}
_MY_DECIMAL = {0, 246}


def _map_mysql(name: str, desc: Sequence[Any]) -> ColumnType:
    code = desc[1] if len(desc) > 1 else None
    if not isinstance(code, int):
        return _varchar(name, "unreported", known_text=False)
    if code in _MY_SIMPLE:
        src, duck = _MY_SIMPLE[code]
        return ColumnType(name, src, duck)
    if code in _MY_TEXT:
        return _varchar(name, _MY_TEXT[code], known_text=True)
    if code in _MY_DECIMAL:
        # pymysql reports the display length, not the precision: DECIMAL(12,4) arrives
        # as 14 (sign and point included). Derive p/s from the values instead.
        s = desc[5] if len(desc) > 5 else None
        if not isinstance(s, int) or s < 0:
            return ColumnType(name, "decimal", "DECIMAL")
        return ColumnType(name, f"decimal(?,{s})", f"DECIMAL(?,{s})")
    return _varchar(name, f"mysql type {code}", known_text=False)


# --- SQL Server (pyodbc): type_code is the Python class the driver returns -----------


def _map_sqlserver(name: str, desc: Sequence[Any]) -> ColumnType:
    code = desc[1] if len(desc) > 1 else None
    if not isinstance(code, type):
        return _varchar(name, "unreported", known_text=False)
    if code is bool:
        return ColumnType(name, "bit", "BOOLEAN")
    if code is int:
        return ColumnType(name, "int", "BIGINT")
    if code is float:
        return ColumnType(name, "float", "DOUBLE")
    if code is decimal.Decimal:
        p = desc[4] if len(desc) > 4 else None
        s = desc[5] if len(desc) > 5 else None
        return _decimal(name, f"decimal({p},{s})", p, s)
    if code is _dt.datetime:
        return ColumnType(name, "datetime", "TIMESTAMP")
    if code is _dt.date:
        return ColumnType(name, "date", "DATE")
    if code is _dt.time:
        return ColumnType(name, "time", "TIME")
    if code is str:
        return _varchar(name, "nvarchar", known_text=True)
    return _varchar(name, code.__name__, known_text=False)


_MAPPERS: dict[str, Callable[[str, Sequence[Any]], ColumnType]] = {
    "postgresql": _map_postgres,
    "mysql": _map_mysql,
    "sqlserver": _map_sqlserver,
}


def map_description(kind: str, description: Sequence[Sequence[Any]]) -> list[ColumnType]:
    """One ``ColumnType`` per DB-API ``cursor.description`` entry.

    A driver that reports no type code (a bare name) maps to ``VARCHAR`` with a note:
    unknown is recorded, never guessed from the values.
    """
    mapper = _MAPPERS.get(kind)
    out: list[ColumnType] = []
    for d in description:
        name = str(d[0])
        if mapper is None:
            out.append(_varchar(name, "unreported", known_text=False))
        else:
            out.append(mapper(name, d))
    return out


# --- serialising values to the text DuckDB casts back exactly ------------------------


class ConversionFailed(ValueError):
    """A value the column's mapped type cannot carry exactly."""


def as_text(value: Any) -> str:
    """The VARCHAR landing of a value. Same ``str()`` the connector always used."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return str(bytes(value))
    return str(value)


def _int_text(value: Any, lo: int, hi: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConversionFailed(f"non-integer value {value!r}")
    if not lo <= value <= hi:
        raise ConversionFailed(f"{value} out of range")
    return str(value)


def _float_text(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, decimal.Decimal)):
        raise ConversionFailed(f"non-numeric value {value!r}")
    f = float(value)
    if isinstance(value, decimal.Decimal) and decimal.Decimal(repr(f)) != value:
        raise ConversionFailed(f"{value} is not exactly representable")
    if math.isnan(f):
        return "nan"
    if math.isinf(f):
        return "inf" if f > 0 else "-inf"
    return repr(f)


def _bool_text(value: Any) -> str:
    if not isinstance(value, bool):
        raise ConversionFailed(f"non-boolean value {value!r}")
    return "true" if value else "false"


def _date_text(value: Any) -> str:
    if isinstance(value, _dt.datetime) or not isinstance(value, _dt.date):
        raise ConversionFailed(f"non-date value {value!r}")
    return value.isoformat()


def _timestamp_text(value: Any) -> str:
    if not isinstance(value, _dt.datetime) or value.tzinfo is not None:
        raise ConversionFailed(f"non-timestamp value {value!r}")
    return value.isoformat(sep=" ")


def _timestamptz_text(value: Any) -> str:
    if not isinstance(value, _dt.datetime) or value.utcoffset() is None:
        raise ConversionFailed(f"non-timestamptz value {value!r}")
    return value.isoformat(sep=" ")


def _time_text(value: Any) -> str:
    if not isinstance(value, _dt.time) or value.tzinfo is not None:
        raise ConversionFailed(f"non-time value {value!r}")
    return value.isoformat()


def _decimal_shape(value: Any) -> tuple[int, int, decimal.Decimal]:
    """(integer digits, scale, value) of a finite exact number."""
    if isinstance(value, bool) or not isinstance(value, (int, decimal.Decimal)):
        raise ConversionFailed(f"non-decimal value {value!r}")
    d = value if isinstance(value, decimal.Decimal) else decimal.Decimal(value)
    if not d.is_finite():
        raise ConversionFailed(f"{value} has no DECIMAL form")
    _sign, digits, exp = d.as_tuple()
    if not isinstance(exp, int):  # pragma: no cover - finite Decimals have an int exponent
        raise ConversionFailed(f"{value} has no DECIMAL form")
    scale = max(0, -exp)
    int_digits = max(0, len(digits) + exp)
    if d == 0:
        int_digits = 0
    return int_digits, scale, d


def _decimal_column(declared: str, values: list[Any]) -> tuple[str, list[str | None]]:
    """Resolve ``DECIMAL`` / ``DECIMAL(?,s)`` / ``DECIMAL(p,s)`` against the values.

    One pass: each value's shape and its plain-notation text (never ``1E+3``).
    """
    max_int = 0
    max_scale = 0
    text: list[str | None] = []
    for v in values:
        if v is None:
            text.append(None)
            continue
        n_int, n_scale, d = _decimal_shape(v)
        max_int = max(max_int, n_int)
        max_scale = max(max_scale, n_scale)
        text.append(format(d, "f"))
    inner = declared[len("DECIMAL"):].strip("()")
    if inner and "?" not in inner:
        p_str, s_str = inner.split(",")
        p, s = int(p_str), int(s_str)
        if max_scale > s or max_int > p - s:
            raise ConversionFailed(f"a value does not fit declared DECIMAL({p},{s})")
        return declared, text
    if inner:  # DECIMAL(?,s): scale declared, precision derived
        s = int(inner.split(",")[1])
        if max_scale > s:
            raise ConversionFailed(f"a value has more than the declared {s} decimal places")
    else:
        s = max_scale
    p = max(max_int + s, 1)
    if p > MAX_DECIMAL_PRECISION:
        raise ConversionFailed(f"values need {p} digits; DuckDB DECIMAL holds 38")
    return f"DECIMAL({p},{s})", text


@dataclass
class PreparedColumn:
    """One column ready to land: its final DuckDB type and its values as text."""

    name: str
    source_type: str
    duck_type: str
    values: list[str | None]
    note: str | None = None


def _serialise(duck_type: str, values: list[Any]) -> tuple[str, list[str | None]]:
    """Text for every value under ``duck_type``; raises ``ConversionFailed`` on any miss."""
    if duck_type in _INT_RANGES:
        lo, hi = _INT_RANGES[duck_type]
        try:
            return duck_type, [None if v is None else _int_text(v, lo, hi) for v in values]
        except ConversionFailed:
            # Declared an int type but a value exceeds it (MySQL UNSIGNED BIGINT): widen,
            # exactly, before giving up on the column.
            lo, hi = _INT_RANGES["HUGEINT"]
            return "HUGEINT", [None if v is None else _int_text(v, lo, hi) for v in values]
    if duck_type.startswith("DECIMAL"):
        return _decimal_column(duck_type, values)
    fn: Callable[[Any], str]
    if duck_type in ("DOUBLE", "REAL"):
        fn = _float_text
    elif duck_type == "BOOLEAN":
        fn = _bool_text
    elif duck_type == "DATE":
        fn = _date_text
    elif duck_type == "TIMESTAMP":
        fn = _timestamp_text
    elif duck_type == "TIMESTAMPTZ":
        fn = _timestamptz_text
    elif duck_type == "TIME":
        fn = _time_text
    else:
        return "VARCHAR", [None if v is None else as_text(v) for v in values]
    return duck_type, [None if v is None else fn(v) for v in values]


def prepare_columns(
    types: Sequence[ColumnType], rows: Sequence[Sequence[Any]]
) -> list[PreparedColumn]:
    """Serialise ``rows`` column by column under ``types``.

    A column where any value cannot be carried exactly lands ``VARCHAR`` (the same
    ``str()`` text the connector always produced) with a note naming why. Other
    columns are unaffected: one bad value costs its own column, not the table.
    """
    out: list[PreparedColumn] = []
    for i, ct in enumerate(types):
        values = [r[i] for r in rows]
        try:
            duck_type, text = _serialise(ct.duck_type, values)
            note = ct.note
            if duck_type == "HUGEINT" and ct.duck_type != "HUGEINT":
                note = f"{ct.name}: values exceed {ct.duck_type}; landed HUGEINT (exact)"
            elif note is not None:
                note = f"{ct.name}: {note}; landed VARCHAR"
        except ConversionFailed as exc:
            duck_type = "VARCHAR"
            text = [None if v is None else as_text(v) for v in values]
            note = (
                f"{ct.name}: source type {ct.source_type} could not be kept "
                f"({exc}); landed VARCHAR"
            )
        out.append(
            PreparedColumn(
                name=ct.name,
                source_type=ct.source_type,
                duck_type=duck_type,
                values=text,
                note=note,
            )
        )
    return out
