"""Structural SQL facts. The expression is parsed, never matched as a phrase."""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def is_window(expr: str) -> bool:
    tree = _one(expr)
    return tree is not None and tree.find(exp.Window) is not None


def unsupported_reason(expr: str) -> str | None:
    """``window_metric`` or ``lod`` when the expression is not a plain scalar."""
    if not expr.strip():
        return None
    if is_window(expr):
        return "window_metric"
    if "{" in expr and _one(expr) is None:
        return "lod"
    return None


def is_query(expr: str) -> bool:
    return isinstance(_one(expr), exp.Query)


def equality_sides(expr: str) -> tuple[str, str, str, str] | None:
    """``table.column = table.column`` as four names. Anything else is None."""
    tree = _one(expr)
    if not isinstance(tree, exp.EQ):
        return None
    left, right = tree.left, tree.right
    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
        return None
    return (left.table or "", left.name or "", right.table or "", right.name or "")


def _one(expr: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(expr)
    except Exception:  # noqa: BLE001 - a construct this dialect cannot parse
        return None
