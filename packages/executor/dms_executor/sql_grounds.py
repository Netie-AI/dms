"""Parsed-SQL structure for the served-check and the intent-spec checker.

Ported from ``sql_grounds`` at 91b67c8b, then extended for ONE-PATH-CHECK
step 2. sqlglot parses the statement in the dialect the caller passes
(extract engine when the caller omits it). Swap: a Cortex HTTP structural
check with the same result shape. This module does not map question words
to conjuncts and holds no word list.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlglot import exp, parse

_DIALECT = "duckdb"
CHECKER_VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_COMPARISON = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
_CMP_OP = {
    exp.EQ: "eq",
    exp.NEQ: "neq",
    exp.GT: "gt",
    exp.GTE: "gte",
    exp.LT: "lt",
    exp.LTE: "lte",
}
_RANK_FN = (
    (exp.RowNumber, "row_number"),
    (exp.DenseRank, "dense_rank"),
    (exp.Rank, "rank"),
)


@dataclass(frozen=True)
class FilterConjunct:
    """One top-level AND conjunct of WHERE, HAVING, QUALIFY, or JOIN ON."""

    column: str
    operator: str
    literals: tuple[str, ...]
    polarity: str


@dataclass(frozen=True)
class DroppedConjunct:
    """A predicate the check refused to treat as a filter, with the reason."""

    sql: str
    reason: str


@dataclass(frozen=True)
class RankWindow:
    """ROW_NUMBER / RANK / DENSE_RANK and the direction of its ORDER BY."""

    function: str
    direction: str
    alias: str


@dataclass(frozen=True)
class RankBound:
    """A comparison that limits a rank window (``rn = 3``, ``ROW_NUMBER() <= 3``)."""

    function: str
    direction: str
    operator: str
    literals: tuple[str, ...]
    alias: str


@dataclass(frozen=True)
class SqlGrounds:
    """Outer-query structure. ``unclear`` means the text did not parse as
    one SELECT (including more than one statement). Callers record it.
    """

    conjuncts: tuple[FilterConjunct, ...]
    measures: tuple[str, ...]
    order_by: tuple[tuple[str, str], ...]
    limit: int | None
    offset: int | None
    unclear: bool
    dropped: tuple[DroppedConjunct, ...] = ()
    group_by: tuple[str, ...] = ()
    derived_limits: tuple[int, ...] = ()
    rank_windows: tuple[RankWindow, ...] = ()
    rank_bounds: tuple[RankBound, ...] = ()
    # Set when a predicate is always false. Callers must not serve that ask.
    contradiction: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "checker_version": CHECKER_VERSION,
            "unclear": self.unclear,
            "contradiction": self.contradiction,
            "conjuncts": [
                {
                    "column": item.column,
                    "operator": item.operator,
                    "literals": list(item.literals),
                    "polarity": item.polarity,
                }
                for item in self.conjuncts
            ],
            "dropped": [
                {"sql": item.sql, "reason": item.reason} for item in self.dropped
            ],
            "measures": list(self.measures),
            "group_by": list(self.group_by),
            "order_by": [
                {"expression": expression, "direction": direction}
                for expression, direction in self.order_by
            ],
            "limit": self.limit,
            "offset": self.offset,
            "derived_limits": list(self.derived_limits),
            "rank_windows": [
                {
                    "function": item.function,
                    "direction": item.direction,
                    "alias": item.alias,
                }
                for item in self.rank_windows
            ],
            "rank_bounds": [
                {
                    "function": item.function,
                    "direction": item.direction,
                    "operator": item.operator,
                    "literals": list(item.literals),
                    "alias": item.alias,
                }
                for item in self.rank_bounds
            ],
        }


def _unclear() -> SqlGrounds:
    return SqlGrounds((), (), (), None, None, True)


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _no_column(node: exp.Expression) -> bool:
    return not any(isinstance(child, exp.Column) for child in node.walk())


def _column_key(node: exp.Expression) -> tuple[str, str] | None:
    node = _unwrap(node)
    if not isinstance(node, exp.Column):
        return None
    name = str(node.name or "").casefold()
    if not name:
        return None
    return (str(node.table or "").casefold(), name)


def _literal_atom(node: exp.Expression | None) -> tuple[str, Any] | None:
    """A typed constant, or None when the node is not one literal."""
    if not isinstance(node, exp.Expression):
        return None
    node = _unwrap(node)
    if isinstance(node, exp.Null):
        return ("null", None)
    if isinstance(node, exp.Boolean):
        return ("bool", bool(node.this))
    if isinstance(node, exp.Literal):
        if node.is_string:
            return ("str", str(node.this))
        try:
            return ("num", float(str(node.this)))
        except ValueError:
            return None
    return None


def _like_match(text: str, pattern: str) -> bool:
    """SQL LIKE with ``%`` and ``_`` only.

    ponytail: no ESCAPE clause. Ceiling: a pattern that quotes a wildcard.
    Upgrade: sqlglot's own like simplifier if one lands in this pin.
    """
    n, m = len(text), len(pattern)
    prev = [False] * (m + 1)
    prev[0] = True
    for col in range(1, m + 1):
        if pattern[col - 1] == "%":
            prev[col] = prev[col - 1]
        else:
            break
    for row in range(1, n + 1):
        cur = [False] * (m + 1)
        for col in range(1, m + 1):
            mark = pattern[col - 1]
            if mark == "%":
                cur[col] = cur[col - 1] or prev[col]
            elif mark == "_" or mark == text[row - 1]:
                cur[col] = prev[col - 1]
        prev = cur
    return prev[m]


def _cmp_atoms(op: type[exp.Expression], left: Any, right: Any) -> bool:
    if op is exp.EQ:
        return left == right
    if op is exp.NEQ:
        return left != right
    if op is exp.GT:
        return left > right
    if op is exp.GTE:
        return left >= right
    if op is exp.LT:
        return left < right
    return left <= right


def _predicate_truth(node: exp.Expression) -> bool | None:
    """True when the predicate is always true, False when always false.

    None when it depends on a row. ``NOT`` flips a known truth. An ``OR``
    is not folded, so a false literal inside one is not a contradiction.
    """
    node = _unwrap(node)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return False
    if isinstance(node, exp.Not):
        inner = _predicate_truth(node.this)
        if inner is None:
            return None
        return not inner
    if isinstance(node, exp.Is):
        if not _no_column(node):
            return None
        left_null = isinstance(_unwrap(node.this), exp.Null)
        right = node.expression
        right_null = right is not None and isinstance(_unwrap(right), exp.Null)
        if left_null and right_null:
            return True
        if left_null or right_null:
            return False
        return None
    if isinstance(node, exp.In) and node.args.get("query") is None:
        if not _no_column(node):
            return None
        left = _literal_atom(node.this)
        if left is None or left[0] == "null":
            return None
        members: list[tuple[str, Any]] = []
        for expr in node.expressions or []:
            atom = _literal_atom(expr)
            if atom is None:
                return None
            members.append(atom)
        if any(atom == left for atom in members):
            return True
        if any(atom[0] != left[0] for atom in members):
            return None
        return False
    if isinstance(node, (exp.Like, exp.ILike)):
        if not _no_column(node):
            return None
        left = _literal_atom(node.this)
        right = _literal_atom(node.args.get("expression"))
        if left is None or right is None or left[0] != "str" or right[0] != "str":
            return None
        text, pat = str(left[1]), str(right[1])
        if isinstance(node, exp.ILike):
            text, pat = text.casefold(), pat.casefold()
        return _like_match(text, pat)
    if isinstance(node, exp.Between):
        if not _no_column(node):
            return None
        subj = _literal_atom(node.this)
        low = _literal_atom(node.args.get("low"))
        high = _literal_atom(node.args.get("high"))
        if (
            subj is None
            or low is None
            or high is None
            or subj[0] == "null"
            or len({subj[0], low[0], high[0]}) != 1
        ):
            return None
        return low[1] <= subj[1] <= high[1]
    if isinstance(node, _COMPARISON):
        lhs = _unwrap(node.this)
        other = node.expression
        if not isinstance(other, exp.Expression):
            return None
        rhs = _unwrap(other)
        lk, rk = _column_key(lhs), _column_key(rhs)
        if lk and rk and lk == rk:
            if isinstance(node, (exp.EQ, exp.GTE, exp.LTE)):
                return True
            if isinstance(node, (exp.NEQ, exp.GT, exp.LT)):
                return False
            return None
        if not (_no_column(lhs) and _no_column(rhs)):
            return None
        la, ra = _literal_atom(lhs), _literal_atom(rhs)
        if la is None or ra is None or la[0] == "null" or ra[0] == "null" or la[0] != ra[0]:
            return None
        return _cmp_atoms(type(node), la[1], ra[1])
    return None


def _same_column_cmp(node: exp.Expression) -> bool:
    node = _unwrap(node)
    if not isinstance(node, _COMPARISON):
        return False
    left, right = _column_key(node.this), _column_key(node.expression)
    return bool(left and right and left == right)


def _percent_like(node: exp.Expression) -> bool:
    """``column LIKE '%'`` (and ``%%``). A column-free pattern is not this."""
    node = _unwrap(node)
    if not isinstance(node, (exp.Like, exp.ILike)):
        return False
    if _no_column(node.this):
        return False
    expr = node.args.get("expression")
    if not isinstance(expr, exp.Literal) or not expr.is_string:
        return False
    text = str(expr.this)
    return bool(text) and set(text) <= {"%"}


def _constant_reason(node: exp.Expression) -> str | None:
    """Why a decided predicate is not a filter. None when it can still match a row."""
    node = _unwrap(node)
    if isinstance(node, exp.Not) and _percent_like(node.this):
        return "contradiction"
    if _percent_like(node):
        return "vacuous"
    truth = _predicate_truth(node)
    if truth is True:
        return "vacuous" if _same_column_cmp(node) else "tautology"
    if truth is False:
        return "contradiction"
    if _no_column(node):
        return "constant"
    return None


def _is_join_key(node: exp.Expression) -> bool:
    node = _unwrap(node)
    if not isinstance(node, exp.EQ):
        return False
    left, right = _unwrap(node.this), _unwrap(node.expression)
    return isinstance(left, exp.Column) and isinstance(right, exp.Column)


def _contains_case(node: exp.Expression) -> bool:
    return any(isinstance(child, exp.Case) for child in node.walk())


def _blocker_reason(node: exp.Expression) -> str | None:
    root = _unwrap(node)
    if isinstance(root, exp.Or):
        return "or"
    if isinstance(root, exp.Exists):
        return "exists"
    if isinstance(root, exp.In) and root.args.get("query") is not None:
        return "in_subquery"
    for child in root.walk():
        if child is root:
            continue
        if isinstance(child, exp.Or):
            return "or"
        if isinstance(child, exp.Exists):
            return "exists"
        if isinstance(child, (exp.Union, exp.Intersect)):
            return "union"
        if isinstance(child, exp.In) and child.args.get("query") is not None:
            return "in_subquery"
        if isinstance(child, exp.Not):
            return "nested_not"
    return None


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


def _rank_name(window: exp.Window) -> str | None:
    fn = window.this
    for cls, name in _RANK_FN:
        if isinstance(fn, cls):
            return name
    return None


def _window_direction(window: exp.Window) -> str:
    order = window.args.get("order")
    if order is None or not order.expressions:
        return "asc"
    item = order.expressions[0]
    if isinstance(item, exp.Ordered) and item.args.get("desc"):
        return "desc"
    return "asc"


def _rank_bound(
    node: exp.Expression, alias_windows: dict[str, RankWindow]
) -> RankBound | None:
    node = _unwrap(node)
    subject: exp.Expression | None
    op: str
    if isinstance(node, exp.Between):
        subject = _unwrap(node.this)
        op = "between"
        other_constant = _no_column(_unwrap(node.args.get("low") or node)) and _no_column(
            _unwrap(node.args.get("high") or node)
        )
    elif isinstance(node, _COMPARISON):
        subject = _unwrap(node.this)
        op = _CMP_OP[type(node)]
        other = node.expression
        other_constant = other is not None and _no_column(_unwrap(other))
    else:
        return None
    if isinstance(subject, exp.Window):
        name = _rank_name(subject)
        if name is None:
            return None
        return RankBound(name, _window_direction(subject), op, _literals(node), "")
    if isinstance(subject, exp.Column) and other_constant:
        alias = str(subject.name or "").casefold()
        win = alias_windows.get(alias)
        if win is None:
            return None
        return RankBound(win.function, win.direction, op, _literals(node), alias)
    return None


def _pack_kept(node: exp.Expression) -> FilterConjunct | None:
    node = _unwrap(node)
    if isinstance(node, exp.Column):
        return _pack(node, "pos", "bool")
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
        if isinstance(inner, (exp.Like, exp.ILike)):
            return _pack(inner, "neg", "not_like")
        if isinstance(inner, exp.Between):
            return _pack(inner, "neg", "not_between")
        if isinstance(inner, exp.Column):
            return _pack(inner, "neg", "bool")
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
    if isinstance(node, exp.Between):
        return _pack(node, "pos", "between")
    if isinstance(node, exp.Is):
        return _pack(node, "pos", "is_null")
    return None


def _drop(node: exp.Expression, reason: str, dialect: str) -> DroppedConjunct:
    try:
        text = node.sql(dialect=dialect)
    except Exception:  # noqa: BLE001 — a dropped predicate still needs a reason
        text = type(node).__name__
    return DroppedConjunct(text, reason)


def _consider(
    node: exp.Expression,
    *,
    dialect: str,
    on_join: bool,
    alias_windows: dict[str, RankWindow],
) -> tuple[FilterConjunct | None, DroppedConjunct | None, RankBound | None]:
    node = _unwrap(node)
    bound = _rank_bound(node, alias_windows)
    if bound is not None:
        return None, None, bound
    if _contains_case(node):
        return None, _drop(node, "case", dialect), None
    decided = _constant_reason(node)
    if decided:
        return None, _drop(node, decided, dialect), None
    if on_join and _is_join_key(node):
        return None, _drop(node, "join_key", dialect), None
    blocker = _blocker_reason(node)
    if blocker:
        return None, _drop(node, blocker, dialect), None
    kept = _pack_kept(node)
    if kept is None:
        return None, _drop(node, "unparsed", dialect), None
    return kept, None, None


def _split_and(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    node = _unwrap(node)
    if isinstance(node, exp.And):
        return _split_and(node.this) + _split_and(node.expression)
    return [node]


def _grounding_select(tree: exp.Expression) -> exp.Select | None:
    """One SELECT. A union, except, or other root is unclear."""
    if isinstance(tree, exp.Select):
        return tree
    return None


def _cte_selects(select: exp.Select) -> list[exp.Select]:
    with_ = select.args.get("with")
    if with_ is None:
        return []
    found: list[exp.Select] = []
    for cte in with_.expressions or []:
        inner = cte.this
        if isinstance(inner, exp.Subquery):
            inner = inner.this
        if isinstance(inner, exp.Select):
            found.append(inner)
            found.extend(_cte_selects(inner))
    return found


def _from_subqueries(select: exp.Select) -> list[exp.Select]:
    nodes: list[Any] = []
    frm = select.args.get("from")
    if frm is not None and frm.this is not None:
        nodes.append(frm.this)
    for join in select.args.get("joins") or []:
        if join.this is not None:
            nodes.append(join.this)
    found: list[exp.Select] = []
    for node in nodes:
        if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Select):
            found.append(node.this)
            found.extend(_from_subqueries(node.this))
    return found


def _predicate_nodes(select: exp.Select) -> list[tuple[exp.Expression, bool]]:
    out: list[tuple[exp.Expression, bool]] = []
    where = select.args.get("where")
    if where is not None:
        out.extend((node, False) for node in _split_and(where.this))
    having = select.args.get("having")
    if having is not None:
        out.extend((node, False) for node in _split_and(having.this))
    qualify = select.args.get("qualify")
    if qualify is not None:
        out.extend((node, False) for node in _split_and(qualify.this))
    for join in select.args.get("joins") or []:
        out.extend((node, True) for node in _split_and(join.args.get("on")))
    return out


def _scalar_selects(node: exp.Expression) -> list[exp.Select]:
    found: list[exp.Select] = []
    for child in node.walk():
        if isinstance(child, exp.Subquery) and isinstance(child.this, exp.Select):
            found.append(child.this)
    return found


def _windows_in(select: exp.Select) -> list[RankWindow]:
    found: list[RankWindow] = []
    for proj in select.expressions or []:
        alias = ""
        node = proj
        if isinstance(proj, exp.Alias):
            alias = str(proj.alias or "")
            node = proj.this
        if isinstance(node, exp.Window):
            name = _rank_name(node)
            if name:
                found.append(RankWindow(name, _window_direction(node), alias.casefold()))
    qualify = select.args.get("qualify")
    if qualify is not None:
        for child in qualify.walk():
            if isinstance(child, exp.Window):
                name = _rank_name(child)
                if name:
                    found.append(RankWindow(name, _window_direction(child), ""))
    return found


def _collect(
    select: exp.Select,
    dialect: str,
    alias_windows: dict[str, RankWindow],
    seen: set[int],
) -> tuple[list[FilterConjunct], list[DroppedConjunct], list[RankBound]]:
    if id(select) in seen:
        return [], [], []
    seen.add(id(select))
    kept: list[FilterConjunct] = []
    dropped: list[DroppedConjunct] = []
    bounds: list[RankBound] = []
    for node, on_join in _predicate_nodes(select):
        item, why, bound = _consider(
            node, dialect=dialect, on_join=on_join, alias_windows=alias_windows
        )
        if item is not None:
            kept.append(item)
            for sub in _scalar_selects(node):
                k2, d2, b2 = _collect(sub, dialect, alias_windows, seen)
                kept.extend(k2)
                dropped.extend(d2)
                bounds.extend(b2)
        if why is not None:
            dropped.append(why)
        if bound is not None:
            bounds.append(bound)
    return kept, dropped, bounds


def _measures(select: exp.Select, dialect: str) -> tuple[str, ...]:
    found: list[str] = []
    for proj in select.expressions or []:
        node = proj.this if isinstance(proj, exp.Alias) else proj
        if any(isinstance(child, exp.AggFunc) for child in node.walk()):
            found.append(node.sql(dialect=dialect))
    return tuple(found)


def _order_by(select: exp.Select, dialect: str) -> tuple[tuple[str, str], ...]:
    order = select.args.get("order")
    if order is None:
        return ()
    rows: list[tuple[str, str]] = []
    for item in order.expressions or []:
        expr = item.this if isinstance(item, exp.Ordered) else item
        direction = "desc" if isinstance(item, exp.Ordered) and item.args.get("desc") else "asc"
        rows.append((expr.sql(dialect=dialect), direction))
    return tuple(rows)


def _group_by(select: exp.Select, dialect: str) -> list[str]:
    group = select.args.get("group")
    if group is None:
        return []
    return [expr.sql(dialect=dialect) for expr in group.expressions or []]


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


def _dedupe(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def _nested_limits(select: exp.Select) -> list[int]:
    out: list[int] = []
    for child in _from_subqueries(select) + _cte_selects(select):
        number = _limit_int(child.args.get("limit"))
        if number is not None:
            out.append(number)
        out.extend(_nested_limits(child))
    return out


def sql_grounds(sql: str, *, dialect: str | None = None) -> SqlGrounds:
    """One statement's filters, order, limit, and group. Unparseable SQL is unclear.

    ``dialect`` is the sqlglot name (``postgres``, ``mysql``, ``tsql``,
    ``duckdb``). Omitted means the extract engine.
    """
    read = dialect or _DIALECT
    try:
        trees = [tree for tree in parse(sql or "", read=read) if tree is not None]
    except Exception:  # noqa: BLE001 — unparseable SQL grounds nothing
        return _unclear()
    if len(trees) != 1:
        return _unclear()
    select = _grounding_select(trees[0])
    if select is None:
        return _unclear()
    scopes = [select, *_cte_selects(select), *_from_subqueries(select)]
    windows: list[RankWindow] = []
    for scope in scopes:
        windows.extend(_windows_in(scope))
    alias_windows = {item.alias: item for item in windows if item.alias}
    kept: list[FilterConjunct] = []
    dropped: list[DroppedConjunct] = []
    bounds: list[RankBound] = []
    seen: set[int] = set()
    for scope in [select, *_cte_selects(select)]:
        k, d, b = _collect(scope, read, alias_windows, seen)
        kept.extend(k)
        dropped.extend(d)
        bounds.extend(b)
    groups: list[str] = []
    for scope in scopes:
        groups.extend(_group_by(scope, read))
    dropped_t = tuple(dropped)
    contradiction = (
        "contradiction" if any(item.reason == "contradiction" for item in dropped_t) else None
    )
    return SqlGrounds(
        tuple(kept),
        _measures(select, read),
        _order_by(select, read),
        _limit_int(select.args.get("limit")),
        _limit_int(select.args.get("offset")),
        False,
        dropped_t,
        _dedupe(groups),
        tuple(_nested_limits(select)),
        tuple(windows),
        tuple(bounds),
        contradiction,
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
