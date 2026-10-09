"""Parsed-SQL structure for the served-check shadow (ONE-PATH-CHECK-01).

Ported from ``sql_grounds`` at 91b67c8b. sqlglot parses one statement.
Swap: a Cortex HTTP structural check with the same result shape. This
module does not map question words to conjuncts and holds no word list.
A failure here is data on the envelope, never a serve or abstain.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from dms_executor.demo_warehouse import SERVING_DIALECT

_DIALECT = SERVING_DIALECT
CHECKER_VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True)
class FilterConjunct:
    """One top-level AND conjunct of the outer WHERE, HAVING, or JOIN ON."""

    column: str
    operator: str
    literals: tuple[str, ...]
    polarity: str


@dataclass(frozen=True)
class SqlGrounds:
    """Outer-query structure. ``unclear`` means the statement did not parse
    or the root is not one SELECT. Callers record it. They do not abstain.
    """

    conjuncts: tuple[FilterConjunct, ...]
    measures: tuple[str, ...]
    order_by: tuple[tuple[str, str], ...]
    limit: int | None
    offset: int | None
    unclear: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "checker_version": CHECKER_VERSION,
            "unclear": self.unclear,
            "conjuncts": [
                {
                    "column": item.column,
                    "operator": item.operator,
                    "literals": list(item.literals),
                    "polarity": item.polarity,
                }
                for item in self.conjuncts
            ],
            "measures": list(self.measures),
            "order_by": [
                {"expression": expression, "direction": direction}
                for expression, direction in self.order_by
            ],
            "limit": self.limit,
            "offset": self.offset,
        }


def _unclear() -> SqlGrounds:
    return SqlGrounds((), (), (), None, None, True)


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


# Binary comparisons. A side is constant when it holds no column node.
_COMPARISON = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _no_column(node: exp.Expression) -> bool:
    return not any(isinstance(child, exp.Column) for child in node.walk())


def _is_tautology(node: exp.Expression) -> bool:
    node = _unwrap(node)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, _COMPARISON):
        left, right = _unwrap(node.this), _unwrap(node.expression)
        # Both sides constant: 1=1, 'X'='Y', 1<>2, 2>1. No literal list.
        if _no_column(left) and _no_column(right):
            return True
        if (
            isinstance(node, (exp.EQ, exp.NEQ))
            and isinstance(left, exp.Column)
            and isinstance(right, exp.Column)
        ):
            return str(left.name).lower() == str(right.name).lower()
    return False


def _has_blocker(node: exp.Expression) -> bool:
    """OR, subquery, nested NOT, or a constant comparison voids the conjunct."""
    root = _unwrap(node)
    for child in root.walk():
        if isinstance(child, (exp.Or, exp.Exists, exp.Subquery, exp.Union, exp.Intersect)):
            return True
        if isinstance(child, exp.Select):
            return True
        if isinstance(child, exp.In) and child.args.get("query") is not None:
            return True
        if isinstance(child, exp.Not) and child is not root:
            return True
        if isinstance(child, _COMPARISON) and child is not root and _is_tautology(child):
            return True
    return False


def _column_label(node: exp.Expression) -> str:
    for child in node.walk():
        if isinstance(child, exp.Column):
            name = str(child.name or "")
            table = str(child.table or "")
            return f"{table}.{name}" if table else name
    return ""


def _literals(node: exp.Expression) -> tuple[str, ...]:
    found: list[str] = []
    for child in node.walk():
        if isinstance(child, exp.Literal):
            found.append(str(child.this))
        elif isinstance(child, exp.Boolean):
            found.append("true" if child.this else "false")
    return tuple(found)


def _pack(node: exp.Expression, polarity: str, op: str) -> FilterConjunct:
    return FilterConjunct(_column_label(node), op, _literals(node), polarity)


def _conjunct_from(node: exp.Expression) -> FilterConjunct | None:
    node = _unwrap(node)
    if _is_tautology(node) or _has_blocker(node):
        return None
    if isinstance(node, exp.NEQ):
        return _pack(node, "neg", "neq")
    if isinstance(node, exp.Not):
        inner = _unwrap(node.this)
        if isinstance(inner, exp.Is):
            return _pack(inner, "pos", "is_not_null")
        if isinstance(inner, exp.In) and inner.args.get("query") is None:
            return _pack(inner, "neg", "not_in")
        if isinstance(inner, exp.EQ):
            return _pack(inner, "neg", "neq")
        # NOT LIKE and any other NOT ground nothing.
        return None
    if isinstance(node, exp.EQ):
        return _pack(node, "pos", "eq")
    if isinstance(node, exp.In) and node.args.get("query") is None:
        return _pack(node, "pos", "in")
    if isinstance(node, (exp.Like, exp.ILike)):
        return _pack(node, "pos", "like")
    if isinstance(node, exp.GT):
        return _pack(node, "pos", "gt")
    if isinstance(node, exp.GTE):
        return _pack(node, "pos", "gte")
    if isinstance(node, exp.LT):
        return _pack(node, "pos", "lt")
    if isinstance(node, exp.LTE):
        return _pack(node, "pos", "lte")
    if isinstance(node, exp.Is):
        return _pack(node, "pos", "is_null")
    return None


def _split_and(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    node = _unwrap(node)
    if isinstance(node, exp.And):
        return _split_and(node.this) + _split_and(node.expression)
    return [node]


def _grounding_select(tree: exp.Expression) -> exp.Select | None:
    """Outer statement only. A union, except, or subquery root is unclear."""
    if isinstance(tree, exp.Select):
        return tree
    return None


def _measures(select: exp.Select) -> tuple[str, ...]:
    found: list[str] = []
    for proj in select.expressions or []:
        node = proj.this if isinstance(proj, exp.Alias) else proj
        if any(isinstance(child, exp.AggFunc) for child in node.walk()):
            found.append(node.sql(dialect=_DIALECT))
    return tuple(found)


def _order_by(select: exp.Select) -> tuple[tuple[str, str], ...]:
    order = select.args.get("order")
    if order is None:
        return ()
    rows: list[tuple[str, str]] = []
    for item in order.expressions or []:
        expr = item.this if isinstance(item, exp.Ordered) else item
        direction = "desc" if isinstance(item, exp.Ordered) and item.args.get("desc") else "asc"
        rows.append((expr.sql(dialect=_DIALECT), direction))
    return tuple(rows)


def _limit_int(node: exp.Expression | None) -> int | None:
    if node is None:
        return None
    expr = node.args.get("expression")
    if not isinstance(expr, exp.Literal) or expr.is_string:
        return None
    try:
        number = int(str(expr.this))
    except ValueError:
        return None
    if number < 0:
        return None
    return number


def sql_grounds(sql: str) -> SqlGrounds:
    """Outer WHERE / HAVING / JOIN ON conjuncts. Unparseable SQL is unclear."""
    try:
        tree = parse_one(sql or "", read=_DIALECT)
    except Exception:  # noqa: BLE001 — unparseable SQL grounds nothing
        return _unclear()
    if tree is None:
        return _unclear()
    select = _grounding_select(tree)
    if select is None:
        return _unclear()
    nodes: list[exp.Expression] = []
    where = select.args.get("where")
    if where is not None:
        nodes.extend(_split_and(where.this))
    having = select.args.get("having")
    if having is not None:
        nodes.extend(_split_and(having.this))
    for join in select.args.get("joins") or []:
        nodes.extend(_split_and(join.args.get("on")))
    conjuncts = [item for node in nodes if (item := _conjunct_from(node)) is not None]
    return SqlGrounds(
        tuple(conjuncts),
        _measures(select),
        _order_by(select),
        _limit_int(select.args.get("limit")),
        _limit_int(select.args.get("offset")),
        False,
    )


def served_check_shadow(sql: str) -> dict[str, Any]:
    """Structured shadow. An unexpected failure is ``error``, never a raise."""
    try:
        return sql_grounds(sql).as_dict()
    except Exception as exc:  # noqa: BLE001 — shadow must not change the serve
        return {
            "checker_version": CHECKER_VERSION,
            "error": f"{type(exc).__name__}: {exc}",
        }
