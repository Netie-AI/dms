"""TOTAL-GUARD-01 (dms#531): no input value may reach an aggregate twice.

Rule 16's join allowlist decides what may be joined. One read of one table
has nothing to join and passes it, and can still multiply its own rows before
an aggregate reads them: ``SUM`` over the rows of a ``ROLLUP`` counts every
value once per grouping set, ``SUM`` over an ``UNPIVOT`` once per turned
column, ``SUM`` over a window total once per row.

This is the class rule for that, an allowlist read off the parsed statement.
Between a row source and an aggregate, a SELECT may filter rows, compute a
value per row, and partition the rows into ONE set of groups. So:

1. A SELECT with more than one grouping set (any GROUP BY that is not a plain
   list of expressions) is a listing. Nothing may aggregate its rows: no
   window function in it, no aggregate or window function in a SELECT that
   reads it.
2. The same for a relation with a PIVOT or UNPIVOT on it, a set operation, a
   row generator and a recursive CTE.
3. A window value (and a scalar subquery's value) may be listed, filtered on,
   grouped by and ordered by. It is never the argument of an aggregate or of
   another window function. Default deny: over a relation that carries such a
   value, an aggregate's argument must be a plain column the relation projects
   under an explicit name from a clean expression. A star, a positional
   reference, a renamed column list, a struct field or an unnamed column
   cannot be followed and refuses.
4. A collection built by an aggregate (``list``, ``array_agg``, anything in
   aggregate position outside ``_SCALAR_AGGS``) may be listed as it is. It is
   never the operand of a function or operator: ``list_sum(list(x) ||
   list(x))`` is a UNION ALL written as a list.

"Aggregate" is a position, not a name: a function sqlglot does not type is
treated as one (``fsum``, ``product``).

What this cannot see, stated: scalar arithmetic the statement wrote.
``SUM(x) + SUM(x)``, ``SUM(x) * 2`` and ``SUM(x + x)`` are the same function
of the table and no structure tells them apart from a wanted figure. Joins
are rule 16's. A figure made smaller (a sample, a LIMIT under the aggregate)
is not this class.

Pure structure: nothing here executes SQL or reads the lake. Fail closed: a
statement the analysis cannot finish refuses.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from dms_executor.demo_warehouse import SERVING_DIALECT

REASON_REPEATED_INPUT = "repeated_input"

_WINDOW = "window_value"
_COLLECTION = "collection"

#: What the retry is told, per refusal. The code before the bracket is stable.
_HINTS = {
    "grouping_sets_under_aggregate": (
        "an aggregate reads rows from more than one grouping set, so one value is "
        "counted once per set; aggregate the base rows once, or list the grouped rows"
    ),
    "row_turn_under_aggregate": (
        "an aggregate reads the rows of a PIVOT or UNPIVOT, so one value can be "
        "counted once per turned column; aggregate the base rows"
    ),
    "set_operation_under_aggregate": (
        "an aggregate reads the rows of a set operation, so one value can be counted "
        "once per branch; aggregate the base rows"
    ),
    "row_generator_under_aggregate": (
        "an aggregate reads rows a generator repeated; aggregate the base rows"
    ),
    "recursion_under_aggregate": (
        "an aggregate reads the rows of a recursive CTE; aggregate the base rows"
    ),
    "unrecognised_source_under_aggregate": (
        "an aggregate reads a row source this check cannot follow"
    ),
    "window_value_under_aggregate": (
        "an aggregate reads a value a window function or scalar subquery computed "
        "over many rows, so it is counted once per row; aggregate the base column"
    ),
    "collection_under_function": (
        "a function or operator is applied to a collection an aggregate built, which "
        "can repeat its values; aggregate the base column directly"
    ),
}

#: Aggregates whose result is one scalar. Every other aggregate is treated as
#: returning a collection of its inputs (rule 4), so a new one refuses by default.
_SCALAR_AGGS: tuple[type[exp.Expression], ...] = (
    exp.Sum,
    exp.Count,
    exp.CountIf,
    exp.Avg,
    exp.Min,
    exp.Max,
    exp.Median,
    exp.Stddev,
    exp.StddevPop,
    exp.StddevSamp,
    exp.Variance,
    exp.VariancePop,
    exp.Quantile,
    exp.PercentileCont,
    exp.PercentileDisc,
    exp.ApproxDistinct,
    exp.ApproxQuantile,
    exp.AnyValue,
    exp.First,
    exp.Last,
    exp.ArgMax,
    exp.ArgMin,
    exp.LogicalAnd,
    exp.LogicalOr,
    exp.BitwiseAndAgg,
    exp.BitwiseOrAgg,
    exp.BitwiseXorAgg,
    exp.Corr,
    exp.CovarPop,
    exp.CovarSamp,
    exp.Grouping,
    # Window-only functions sqlglot files under AggFunc.
    exp.Rank,
    exp.DenseRank,
    exp.PercentRank,
    exp.CumeDist,
    exp.Ntile,
    exp.Lag,
    exp.Lead,
    exp.FirstValue,
    exp.LastValue,
    exp.NthValue,
)

_SET_OPS = (exp.Union, exp.Except, exp.Intersect)
#: SELECT arguments that name row sources. Everything else is the SELECT's own.
_SOURCE_ARGS = frozenset({"from", "from_", "joins", "with", "with_", "laterals"})
#: What may sit between a collecting aggregate and its SELECT (rule 4).
_BARE = (exp.Alias, exp.Paren, exp.Window, exp.Filter)


class _Refuse(Exception):
    pass


def _worst(*kinds: str) -> str:
    if _COLLECTION in kinds:
        return _COLLECTION
    return _WINDOW if _WINDOW in kinds else ""


@dataclass
class _Rel:
    """What a SELECT that reads this relation must know about it."""

    #: Why a value can appear in more than one of its rows, or None.
    repeats: str | None = None
    #: Explicitly named output columns: "" clean, else the kind of value.
    cols: dict[str, str] = field(default_factory=dict)
    #: The kind any column NOT in ``cols`` is taken to have (default deny).
    carried: str = ""

    def kind(self, name: str) -> str:
        return self.cols[name] if name in self.cols else self.carried


def _any(*rels: _Rel) -> str:
    """The worst kind any column of these relations has."""
    return _worst(*(r.carried for r in rels), *(k for r in rels for k in r.cols.values()))


def _collects(node: exp.Expression) -> bool:
    """An aggregate whose result is a collection of its inputs."""
    if isinstance(node, exp.List):
        return True  # DuckDB list(x); sqlglot does not file it under AggFunc
    if not isinstance(node, exp.AggFunc):
        return False
    if isinstance(node, (exp.Min, exp.Max)) and node.expressions:
        return True  # min(x, n) / max(x, n) return a list
    if isinstance(node, (exp.ArgMax, exp.ArgMin)) and node.args.get("count"):
        return True
    return not isinstance(node, _SCALAR_AGGS)


def _aggregates(node: exp.Expression) -> bool:
    """A node in aggregate position: typed, collecting, windowed, or not typed at all."""
    return isinstance(node, (exp.AggFunc, exp.List, exp.Anonymous, exp.Window))


def _many_grouping_sets(select: exp.Select) -> bool:
    group = select.args.get("group")
    if group is None:
        return False
    if any(v for k, v in group.args.items() if k not in ("expressions", "all")):
        return True
    return group.find(exp.Rollup, exp.Cube, exp.GroupingSets) is not None


def _own(select: exp.Select) -> Iterator[exp.Expression]:
    """Every node of the SELECT outside its row sources.

    Expression-position subqueries are included whole: DuckDB binds an
    aggregate whose arguments are all outer columns to the outer SELECT, so
    ``(SELECT SUM(q.w))`` aggregates the rows this SELECT reads.
    """
    for key, value in select.args.items():
        if key in _SOURCE_ARGS:
            continue
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, exp.Expression):
                yield from item.walk()


def _with_of(node: exp.Expression) -> exp.With | None:
    found = node.args.get("with") or node.args.get("with_")
    return found if isinstance(found, exp.With) else None


class _Guard:
    def __init__(self) -> None:
        self.done: dict[int, _Rel] = {}
        self.open: set[int] = set()

    # -- row sources ------------------------------------------------------

    def _cte(self, table: exp.Table) -> exp.CTE | None:
        if not isinstance(table.this, exp.Identifier) or table.db or table.catalog:
            return None
        name = table.name.lower()
        node: exp.Expression | None = table.parent
        while node is not None:
            with_ = _with_of(node)
            if with_ is not None:
                for cte in with_.expressions:
                    if isinstance(cte, exp.CTE) and cte.alias.lower() == name:
                        return cte
            node = node.parent
        return None

    def _renamed(self, rel: _Rel, item: exp.Expression) -> _Rel:
        """``AS q(c1, c2)`` renames by position: no name can be followed."""
        alias = item.args.get("alias")
        if isinstance(alias, exp.TableAlias) and alias.columns:
            return _Rel(rel.repeats, {}, _any(rel))
        return rel

    def source(self, item: exp.Expression) -> _Rel:
        if isinstance(item, exp.UDTF):
            rel = _Rel(repeats="row_generator")
        elif isinstance(item, exp.Table):
            cte = self._cte(item)
            if cte is not None:
                rel = self._renamed(self.query(cte.this), cte)
            elif isinstance(item.this, exp.Identifier):
                rel = _Rel()  # a base table
            else:
                rel = _Rel(repeats="row_generator")  # range(n), read_csv(...)
        elif isinstance(item, exp.Subquery):
            rel = self.query(item.this)
        else:
            rel = _Rel(repeats="unrecognised_source", carried=_WINDOW)
        if item.args.get("pivots"):
            rel = _Rel("row_turn", {}, _worst(rel.carried, *rel.cols.values()))
        return self._renamed(rel, item)

    def query(self, node: exp.Expression) -> _Rel:
        key = id(node)
        if key in self.done:
            return self.done[key]
        if key in self.open:
            return _Rel(repeats="recursion", carried=_WINDOW)
        self.open.add(key)
        try:
            if isinstance(node, exp.Select):
                rel = self.select(node)
            elif isinstance(node, _SET_OPS):
                left, right = self.query(node.this), self.query(node.expression)
                kinds = [left.carried, right.carried, *left.cols.values(), *right.cols.values()]
                rel = _Rel("set_operation", {}, _worst(*kinds))
            elif isinstance(node, exp.Summarize):
                # SUMMARIZE aggregates every column of its input.
                inner = self.query(node.this)
                if inner.repeats or _any(inner):
                    raise _Refuse(f"{inner.repeats or 'window_value'}_under_aggregate")
                rel = _Rel()
            elif isinstance(node, exp.Subquery):
                rel = self.source(node)
            elif isinstance(node, exp.UDTF):
                rel = _Rel(repeats="row_generator")
            elif isinstance(node, exp.Pivot):
                rel = _Rel(repeats="row_turn")
            else:
                rel = _Rel(repeats="unrecognised_source", carried=_WINDOW)
        finally:
            self.open.discard(key)
        self.done[key] = rel
        return rel

    # -- one SELECT -------------------------------------------------------

    def select(self, select: exp.Select) -> _Rel:
        srcs: dict[str, _Rel] = {}
        rels: list[_Rel] = []
        items: list[exp.Expression] = []
        from_ = select.args.get("from") or select.args.get("from_")
        if from_ is not None:
            items.append(from_.this)
        items += [j.this for j in select.args.get("joins") or []]
        items += list(select.args.get("laterals") or [])
        for item in items:
            rel = self.source(item)
            rels.append(rel)
            alias = str(item.alias_or_name or "").lower()
            if alias:
                # Two sources under one alias cannot be told apart: take the worse.
                seen = srcs.get(alias)
                srcs[alias] = rel if seen is None else _Rel(None, {}, _any(seen, rel))
        repeats = next((r.repeats for r in rels if r.repeats), None)
        #: The kind of a column these sources hand on under a name not listed.
        src_loose = _worst(*(r.carried for r in rels))
        #: The worst kind any column of these sources has.
        src_any = _worst(*(_any(r) for r in rels))

        own = list(_own(select))
        mine = [n for n in own if n.find_ancestor(exp.Select) is select]
        if repeats and any(_aggregates(n) for n in own):
            raise _Refuse(f"{repeats}_under_aggregate")
        # Rule 3, same SELECT: a scalar subquery's value is read once per row of
        # this SELECT when an aggregate takes it as an argument.
        for node in mine:
            if not _aggregates(node):
                continue
            scan = node.this if isinstance(node, exp.Window) else node
            if isinstance(scan, exp.Expression) and scan.find(exp.Select) is not None:
                raise _Refuse("window_value_under_aggregate")
        many = _many_grouping_sets(select)
        if many and any(isinstance(n, exp.Window) for n in own):
            raise _Refuse("grouping_sets_under_aggregate")

        # Rule 4, in this SELECT: a collecting aggregate is listed as it is.
        collections: dict[str, str] = {}
        for node in mine:
            if not _collects(node):
                continue
            top: exp.Expression = node
            while isinstance(top.parent, _BARE):
                top = top.parent
            if top.parent is not select or top.arg_key != "expressions":
                raise _Refuse("collection_under_function")
            if isinstance(top, exp.Alias):
                collections[top.alias.lower()] = _COLLECTION

        def col_kind(col: exp.Column, known: dict[str, str]) -> str:
            name = col.name.lower()
            table = str(col.table or "").lower()
            if table:
                rel = srcs.get(table)
                if rel is not None and not col.args.get("db"):
                    return rel.kind(name)
                # Not a source of this SELECT: a struct field, a whole-row
                # alias or an outer column. It cannot be followed.
                return _worst(src_any, known.get(table, ""), known.get(name, ""))
            # A source's alias used as a column is its whole row.
            row = _any(srcs[name]) if name in srcs else ""
            return _worst(*(r.kind(name) for r in rels), known.get(name, ""), row)

        # Rules 3 and 4 across SELECTs: what reads a carried value.
        if src_any or collections:
            for node in own:
                if isinstance(node, exp.Column) and not isinstance(node.this, exp.Star):
                    if col_kind(node, collections) != _COLLECTION:
                        continue
                    bare = node.parent is select or (
                        isinstance(node.parent, exp.Alias) and node.parent.parent is select
                    )
                    if not bare or (node.parent or node).arg_key != "expressions":
                        raise _Refuse("collection_under_function")
                    continue
                if not _aggregates(node):
                    continue
                # A window function's arguments count. Its partition and order do not.
                scan = node.this if isinstance(node, exp.Window) else node
                if not isinstance(scan, exp.Expression):
                    continue
                for arg in scan.walk():
                    if isinstance(arg, exp.Column) and not isinstance(arg.this, exp.Star):
                        kind = col_kind(arg, collections)
                    elif isinstance(arg, (exp.Star, exp.Columns, exp.PositionalColumn)):
                        # COUNT(*) counts rows, and a window adds none.
                        counts_rows = isinstance(arg, exp.Star) and isinstance(
                            arg.parent, exp.Count
                        )
                        kind = "" if counts_rows else src_any
                    else:
                        continue
                    if kind == _COLLECTION:
                        raise _Refuse("collection_under_function")
                    if kind:
                        raise _Refuse("window_value_under_aggregate")

        # What this SELECT hands on.
        out = _Rel(repeats=repeats or ("grouping_sets" if many else None))
        if any(isinstance(n, exp.UDTF) for n in mine):
            out.repeats = out.repeats or "row_generator"
        known: dict[str, str] = dict(collections)
        for _ in range(len(select.expressions) + 1):
            before = dict(known)
            out.cols, out.carried = {}, ""
            for proj in select.expressions:
                inner = proj.unalias()
                if (
                    isinstance(inner, (exp.Star, exp.Columns))
                    or (isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star))
                    or proj.find(exp.Columns) is not None
                ):
                    if isinstance(inner, exp.Star) and not any(inner.args.values()):
                        # A plain star hands every source column on under its name.
                        out.carried = _worst(out.carried, src_loose)
                        for rel in rels:
                            for name, kind in rel.cols.items():
                                out.cols[name] = _worst(out.cols.get(name, ""), kind)
                    else:
                        # EXCLUDE / REPLACE / RENAME / COLUMNS(...) / q.*: not followed.
                        out.carried = _worst(out.carried, src_any, self._value_kind(proj))
                    continue
                kind = self._value_kind(proj)
                if not kind:
                    kind = _worst(
                        *(
                            col_kind(c, known)
                            for c in proj.find_all(exp.Column)
                            if c.find_ancestor(exp.Select) is select
                        )
                    )
                    if proj.find(exp.PositionalColumn) is not None:
                        kind = _worst(kind, src_any)
                name = (
                    proj.alias.lower()
                    if isinstance(proj, exp.Alias)
                    else (inner.name.lower() if isinstance(inner, exp.Column) else "")
                )
                if not name:
                    out.carried = _worst(out.carried, kind)  # an unnamed value
                    continue
                out.cols[name] = _worst(out.cols.get(name, ""), kind)
                known[name] = _worst(known.get(name, ""), out.cols[name])
            if known == before:
                break
        return out

    @staticmethod
    def _value_kind(proj: exp.Expression) -> str:
        if any(_collects(n) for n in proj.walk()):
            return _COLLECTION
        if proj.find(exp.Window, exp.Select) is not None:
            return _WINDOW
        return ""


def repeated_input_reason(sql: str, dialect: str = SERVING_DIALECT) -> str | None:
    """``repeated_input:<why>`` when an aggregate can read one input value twice.

    None when every aggregate in ``sql`` reads rows that were only filtered,
    computed per row, or grouped once. See the module docstring for the class
    and for what it cannot see.
    """
    try:
        roots = [r for r in sqlglot.parse(sql, read=dialect) if r is not None]
    except Exception:  # noqa: BLE001
        return f"{REASON_REPEATED_INPUT}:parse"
    if len(roots) != 1:
        return f"{REASON_REPEATED_INPUT}:statements"
    root = roots[0]
    guard = _Guard()
    try:
        guard.query(root)
        for select in root.find_all(exp.Select):
            guard.query(select)
    except _Refuse as why:
        code = str(why)
        hint = _HINTS.get(code)
        return f"{REASON_REPEATED_INPUT}:{code}" + (f" ({hint})" if hint else "")
    except Exception:  # noqa: BLE001 - an analysis we cannot finish proves nothing
        return f"{REASON_REPEATED_INPUT}:unanalysable"
    return None


__all__ = ["REASON_REPEATED_INPUT", "repeated_input_reason"]
