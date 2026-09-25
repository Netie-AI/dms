"""Curated-pack oracle row compare. Not the BIRD Mini-Dev harness.

Multiset of value tuples: column-order-insensitive, NULL equals NULL.
Numbers round to the oracle SQL ROUND scale (else exact Decimal).
ORDER BY is honoured only together with LIMIT (top-N sequence).
Read-only DuckDB. A failed oracle is the caller's ORACLE_ERROR, never OK.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_LIMIT = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_ROUND = re.compile(r"\bROUND\s*\([^,]+,\s*(\d+)\s*\)", re.IGNORECASE)


def is_select_sql(sql: str) -> bool:
    head = sql.strip().lstrip("(").strip()
    if len(head) < 6:
        return False
    return head[:6].upper() == "SELECT" or head[:4].upper() == "WITH"


def has_order_by_limit(sql: str) -> bool:
    blob = " ".join(sql.split())
    return bool(_ORDER_BY.search(blob) and _LIMIT.search(blob))


def numeric_scale_from_sql(sql: str) -> int | None:
    scales = [int(m.group(1)) for m in _ROUND.finditer(sql)]
    return max(scales) if scales else None


def envelope_rows(env: Mapping[str, Any]) -> list[Any]:
    rows = env.get("rows") or env.get("values") or []
    if not isinstance(rows, list):
        return []
    return list(rows)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _quantize(num: Decimal, scale: int) -> Decimal:
    q = Decimal(1).scaleb(-scale)
    return num.quantize(q, rounding=ROUND_HALF_UP)


def norm_cell(value: Any, scale: int | None) -> tuple[str, Any]:
    if value is None:
        return ("z", None)
    if isinstance(value, str) and not value.strip():
        return ("z", None)
    num = _as_decimal(value)
    if num is not None:
        if scale is not None:
            num = _quantize(num, scale)
        return ("n", num)
    return ("s", str(value).strip().casefold())


def _row_cells(row: Any) -> list[Any]:
    if isinstance(row, dict):
        return list(row.values())
    if isinstance(row, (list, tuple)):
        return list(row)
    return [row]


def cells_equal(left: Any, right: Any, scale: int | None) -> bool:
    return norm_cell(left, scale) == norm_cell(right, scale)


def row_equal(left: Any, right: Any, scale: int | None) -> bool:
    """Column-order-insensitive cell multiset. ponytail: O(n*m); curated rows are small."""
    a = _row_cells(left)
    b = _row_cells(right)
    if len(a) != len(b):
        return False
    used = [False] * len(b)
    for cell in a:
        found = False
        for i, other in enumerate(b):
            if used[i]:
                continue
            if cells_equal(cell, other, scale):
                used[i] = True
                found = True
                break
        if not found:
            return False
    return True


def rows_equal(
    gold: Sequence[Any],
    got: Sequence[Any],
    *,
    ordered: bool,
    scale: int | None,
) -> bool:
    if len(gold) != len(got):
        return False
    if ordered:
        return all(row_equal(a, b, scale) for a, b in zip(gold, got, strict=True))
    used = [False] * len(got)
    for row in gold:
        found = False
        for i, other in enumerate(got):
            if used[i]:
                continue
            if row_equal(row, other, scale):
                used[i] = True
                found = True
                break
        if not found:
            return False
    return True


def rows_mismatch_reason(
    got: Sequence[Any],
    gold: Sequence[Any],
    *,
    sql: str,
) -> str | None:
    """None if match; else rows_mismatch:count=<a>/<b> or rows_mismatch:values."""
    if len(got) != len(gold):
        return f"rows_mismatch:count={len(got)}/{len(gold)}"
    scale = numeric_scale_from_sql(sql)
    ordered = has_order_by_limit(sql)
    if rows_equal(gold, got, ordered=ordered, scale=scale):
        return None
    return "rows_mismatch:values"


def read_engine_clock(db_path: Path | str) -> tuple[str | None, str | None]:
    """CURRENT_DATE and TimeZone from this DuckDB file. Not the harness clock."""
    try:
        import duckdb
    except ImportError:
        return None, None
    path = Path(db_path)
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            row = con.execute(
                "SELECT CAST(CURRENT_DATE AS VARCHAR), current_setting('TimeZone')"
            ).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - clock must not fail the scorer
        return None, None
    if not row:
        return None, None
    as_of = str(row[0]).strip() if row[0] is not None else ""
    tz = str(row[1]).strip() if row[1] is not None else ""
    return (as_of or None, tz or None)


def bind_oracle_params(
    con: Any,
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Named binds for oracle SQL. Auto-fill $as_of from this connection."""
    bind: dict[str, Any] = dict(params) if params else {}
    if "$as_of" in sql and "as_of" not in bind:
        # ponytail: omitted as_of uses this connection's CURRENT_DATE.
        # Ceiling: a midnight crossing can disagree with an earlier report stamp.
        # Upgrade: always pass the recorded run date from the harness.
        row = con.execute("SELECT CURRENT_DATE").fetchone()
        bind["as_of"] = row[0] if row else None
    return bind or None


def read_schema_version(db_path: Path | str) -> str:
    try:
        import duckdb
    except ImportError as exc:
        return f"unknown:{type(exc).__name__}"
    path = Path(db_path)
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            row = con.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - metadata must not fail the scorer
        return "unknown"
    if not row:
        return "unknown"
    text = str(row[0]).strip()
    return text if text else "unknown"


def run_oracle_select(
    db_path: Path | str,
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Read-only SELECT. (rows, None) or (None, error). Never raises into OK."""
    if not is_select_sql(sql):
        return None, "not_select"
    try:
        import duckdb
    except ImportError as exc:
        return None, type(exc).__name__
    path = Path(db_path)
    try:
        con = duckdb.connect(str(path), read_only=True)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}:{exc}"
    try:
        try:
            con.execute("SET default_transaction_read_only = on")
        except Exception:  # noqa: BLE001 - best-effort
            pass
        bind = bind_oracle_params(con, sql, params)
        cur = con.execute(sql, bind) if bind else con.execute(sql)
        cols = [str(c[0]) for c in (cur.description or [])]
        out = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        return out, None
    except Exception as exc:  # noqa: BLE001
        msg = " ".join(str(exc).split())[:200]
        return None, f"{type(exc).__name__}:{msg}" if msg else type(exc).__name__
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
