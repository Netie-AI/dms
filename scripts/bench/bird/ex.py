"""Official Mini-Dev EX, plus a normalised variant.

``calculate_ex`` is the comparison in bird-bench/mini_dev
``evaluation/evaluation_ex.py``: a set of row tuples. Column order is the
tuple order. Duplicate rows collapse. There is no numeric tolerance.

The normalised variant canonicalises cells before the same set comparison.
It is reported beside the official number. It does not replace it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

_QUANT = Decimal("0.000001")


def calculate_ex(predicted_res: Sequence[Any], ground_truth_res: Sequence[Any]) -> int:
    """Same comparison as bird-bench/mini_dev evaluation_ex.calculate_ex."""
    res = 0
    if set(predicted_res) == set(ground_truth_res):
        res = 1
    return res


def result_tuples(rows: Sequence[Any]) -> list[tuple[Any, ...]]:
    """Row tuples in column order. A dict uses insertion order."""
    out: list[tuple[Any, ...]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(tuple(row.values()))
        elif isinstance(row, tuple):
            out.append(row)
        elif isinstance(row, list):
            out.append(tuple(row))
        elif isinstance(row, (str, bytes)):
            out.append((row,))
        else:
            try:
                out.append(tuple(row))
            except TypeError:
                out.append((row,))
    return out


def _canon_number(num: Decimal) -> Decimal:
    if num == num.to_integral_value():
        return num.to_integral_value()
    return num.quantize(_QUANT)


def canonical_cell(value: Any) -> tuple[str, Any]:
    """One comparable cell. Blank text is null. Numbers snap to 6 decimal places."""
    if value is None:
        return ("z", None)
    if isinstance(value, bool):
        return ("n", Decimal(int(value)))
    if isinstance(value, int):
        return ("n", Decimal(value))
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ("s", str(value))
        return ("n", _canon_number(Decimal(str(value))))
    if isinstance(value, Decimal):
        return ("n", _canon_number(value))
    if isinstance(value, bytes):
        return ("b", value)
    text = str(value).strip()
    if not text:
        return ("z", None)
    try:
        num = Decimal(text)
    except InvalidOperation:
        return ("s", text.casefold())
    return ("n", _canon_number(num))


def canonical_tuples(rows: Sequence[Any]) -> list[tuple[Any, ...]]:
    return [tuple(canonical_cell(cell) for cell in row) for row in result_tuples(rows)]


def calculate_ex_normalised(predicted_res: Sequence[Any], ground_truth_res: Sequence[Any]) -> int:
    """Set comparison after canonical cells. Column order still matters."""
    return calculate_ex(canonical_tuples(predicted_res), canonical_tuples(ground_truth_res))


def nearest_rank(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile. Empty input has no percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(pct / 100.0 * len(ordered))
    index = min(len(ordered), max(1, rank)) - 1
    return float(ordered[index])


def self_check() -> None:
    assert calculate_ex([(1, "a")], [("a", 1)]) == 0
    assert calculate_ex([(1,), (1,)], [(1,)]) == 1
    assert calculate_ex([(1.0,)], [(1.1,)]) == 0
    assert calculate_ex([], []) == 1
    assert calculate_ex_normalised([("Alpha",)], [("alpha",)]) == 1
    assert calculate_ex([("Alpha",)], [("alpha",)]) == 0
    assert calculate_ex_normalised([(1.0,)], [(Decimal("1.0000004"),)]) == 1
    assert calculate_ex([(1.0,)], [(Decimal("1.0000004"),)]) == 0
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 95) == 4.0
    assert nearest_rank([], 50) is None
