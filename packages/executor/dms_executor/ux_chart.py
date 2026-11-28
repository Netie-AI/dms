"""UX-CHART-01. Default off.

Off, ``attach_ux_chart`` returns the same envelope object. Text, rows,
values, and chart are not copied and not rewritten.

On, a row listing (``Found N row(s).`` plus ``key=value`` lines) becomes
one sentence: the top cell, the full-column total, and the row count.
Figures are grouped. Currency comes from the column suffix or a value
unit. The total is added to ``values`` only when that column is numeric
on every row, so a later E4 / E13 check still cites a cell or that sum.
Rows are left as the caller stored them. The question is not read.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Any

UX_CHART_ENV = "DMS_UX_CHART"
_FOUND = re.compile(r"^Found \d+ row\(s\)\.$")
_ISO_DATE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_UNIT = (
    ("_myr", "RM "),
    ("_usd", "USD "),
    ("_eur", "EUR "),
    ("_gbp", "GBP "),
    ("_sgd", "SGD "),
)


def ux_chart_enabled() -> bool:
    """True only when ``DMS_UX_CHART`` is the digit 1. Unset is off."""
    return os.environ.get(UX_CHART_ENV, "0").strip() == "1"


def attach_ux_chart(env: dict[str, Any]) -> dict[str, Any]:
    """Flag off: the same object. Flag on: a copy with ``ux_chart`` set."""
    if not ux_chart_enabled():
        return env
    out = dict(env)
    out["ux_chart"] = True
    if out.get("abstained") is True:
        return out
    rows = out.get("rows")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        return out
    values = out.get("values")
    incoming = list(values) if isinstance(values, list) else []
    rewritten = rewrite_listing(str(out.get("text") or ""), rows, incoming)
    if rewritten is None:
        return out
    text, new_values = rewritten
    out["text"] = text
    out["values"] = new_values
    return out


def rewrite_listing(
    text: str,
    rows: list[dict[str, Any]],
    values: list[Any],
) -> tuple[str, list[dict[str, Any]]] | None:
    """Replace a pure row listing. None when the text is not one."""
    kept: list[str] = []
    saw = False
    for line in text.split("\n"):
        if line == "":
            kept.append(line)
            continue
        if _FOUND.match(line):
            saw = True
            continue
        if line.startswith("  - "):
            if not _line_is_row(line[4:], rows):
                return None
            saw = True
            continue
        kept.append(line)
    if not saw:
        return None
    sentence, new_values = summary_sentence(rows, values)
    lead = "\n".join(kept).strip()
    if lead:
        return f"{lead}\n{sentence}", new_values
    return sentence, new_values


def summary_sentence(
    rows: list[dict[str, Any]],
    values: list[Any],
) -> tuple[str, list[dict[str, Any]]]:
    """One sentence from cells. Does not drop a row."""
    keys = _keys(rows)
    nums = [key for key in keys if _kind(rows, key) == "numeric"]
    cats = [key for key in keys if _kind(rows, key) == "category"]
    dates = [key for key in keys if _kind(rows, key) == "date"]
    copied = [dict(item) for item in values if isinstance(item, dict)]
    count = len(rows)
    if not nums:
        return f"{count} rows.", copied
    measure = nums[0]
    prefix = _prefix(measure, copied)
    best: dict[str, Any] | None = None
    best_n: float | None = None
    for row in rows:
        num = _num(row.get(measure))
        if num is None:
            continue
        if best_n is None or num > best_n:
            best = row
            best_n = num
    if best is None or best_n is None:
        return f"{count} rows.", copied
    top_shown = float(f"{best_n:.2f}")
    top_fmt = prefix + _grouped(top_shown)
    name_col = cats[0] if cats else (dates[0] if dates else None)
    raw_name = best.get(name_col) if name_col else None
    name = str(raw_name).strip() if raw_name not in (None, "") else ""
    total = _column_sum(rows, measure)
    if total is not None and not _known(copied, total):
        copied.append({"id": _fresh_id(copied), "value": total, "label": measure})
    if total is None or _close(total, top_shown):
        if name:
            return f"{name} is {top_fmt}.", copied
        return f"{top_fmt}.", copied
    total_fmt = prefix + _grouped(total)
    if name:
        return (
            f"{name} is highest at {top_fmt}, total {total_fmt} across {count} rows.",
            copied,
        )
    return (f"Highest is {top_fmt}, total {total_fmt} across {count} rows.", copied)


def _keys(rows: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    return keys


def _kind(rows: list[dict[str, Any]], key: str) -> str:
    kind: str | None = None
    for row in rows:
        value = row.get(key)
        if value is None or value == "":
            continue
        if _num(value) is not None:
            nxt = "numeric"
        elif isinstance(value, str) and _ISO_DATE.match(value.strip()):
            nxt = "date"
        elif isinstance(value, str) and value.strip():
            nxt = "category"
        else:
            return "other"
        if kind and kind != nxt:
            return "other"
        kind = nxt
    return kind or "other"


def _column_sum(rows: list[dict[str, Any]], key: str) -> float | None:
    """Sum only when every cell in the column is numeric. No zero pad."""
    total = 0.0
    for row in rows:
        num = _num(row.get(key))
        if num is None:
            return None
        total += num
    if not rows:
        return None
    return float(f"{total:.2f}")


def _prefix(column: str, values: list[dict[str, Any]]) -> str:
    for item in values:
        if str(item.get("label") or "") != column:
            continue
        unit = str(item.get("unit") or "").strip().upper()
        if unit in {"MYR", "RM"}:
            return "RM "
        if unit in {"USD", "EUR", "GBP", "SGD"}:
            return f"{unit} "
    low = column.lower()
    for suffix, prefix in _UNIT:
        if low.endswith(suffix):
            return prefix
    return ""


def _grouped(number: float) -> str:
    return f"{number:,.2f}"


def _known(values: list[dict[str, Any]], number: float) -> bool:
    for item in values:
        num = _num(item.get("value"))
        if num is not None and _close(num, number):
            return True
    return False


def _fresh_id(values: list[dict[str, Any]]) -> str:
    used = {str(item.get("id")) for item in values}
    n = len(values)
    while f"v{n}" in used:
        n += 1
    return f"v{n}"


def _line_is_row(body: str, rows: list[dict[str, Any]]) -> bool:
    pairs: list[tuple[str, str]] = []
    for part in body.split(", "):
        if "=" not in part:
            return False
        key, raw = part.split("=", 1)
        if not key:
            return False
        pairs.append((key, raw))
    if not pairs:
        return False
    return any(all(_cell_matches(row, key, raw) for key, raw in pairs) for row in rows)


def _cell_matches(row: dict[str, Any], key: str, raw: str) -> bool:
    if key not in row:
        return False
    cell = row[key]
    if str(cell) == raw:
        return True
    num = _num(cell)
    if num is None:
        return False
    try:
        return _close(num, float(raw))
    except ValueError:
        return False


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _close(left: float, right: float) -> bool:
    return abs(left - right) < 0.011 or abs(left - right) / max(abs(right), 1e-9) < 1e-4
