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

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache

import duckdb
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
    "sample_under_aggregate": (
        "an aggregate reads a sample of the rows (USING SAMPLE or TABLESAMPLE), so its "
        "figure is not the table's; aggregate every row"
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
    "self_join_under_aggregate": (
        "an aggregate reads rows from a table joined to itself or to a relation built "
        "from the same table, so one value is counted once per matching row; "
        "aggregate the base rows once"
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
    #: Base tables and CTE names read to build it, with how often.
    reads: dict[str, int] = field(default_factory=dict)

    def kind(self, name: str) -> str:
        return self.cols[name] if name in self.cols else self.carried


def _sum_reads(*rels: _Rel) -> dict[str, int]:
    out: dict[str, int] = {}
    for rel in rels:
        for name, n in rel.reads.items():
            out[name] = out.get(name, 0) + n
    return out


#: The functions that add rows when used in a SELECT list. Per the base
#: session's sweep of DuckDB 1.5.6's catalog (561 functions runnable in a
#: SELECT list) these four are the only ones; not re-measured here. They parse
#: as ordinary functions, as methods (``x.unnest()``) and schema-qualified.
_ROW_ADDING_FUNCTIONS = frozenset(
    {"unnest", "unlist", "generate_subscripts", "regexp_split_to_table"}
)


def _adds_rows(node: exp.Expression) -> bool:
    if isinstance(node, exp.UDTF):
        return True
    return isinstance(node, exp.Anonymous) and str(node.name).lower() in _ROW_ADDING_FUNCTIONS


@lru_cache(maxsize=1)
def _aggregate_returns() -> dict[str, frozenset[bool]]:
    """Per aggregate name in DuckDB's own catalog: does each overload return a collection?

    Asked of the engine that runs the statement, never listed here: sqlglot does
    not type ``histogram`` or ``map_agg``, and a name list would be the next
    spelling. In-memory, reads no lake.
    """
    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            "SELECT lower(function_name), return_type FROM duckdb_functions() "
            "WHERE function_type = 'aggregate'"
        ).fetchall()
    finally:
        con.close()
    out: dict[str, set[bool]] = {}
    for name, ret in rows:
        text = str(ret or "")
        out.setdefault(str(name), set()).add(
            text.endswith("]") or text.startswith(("MAP", "STRUCT")) or text == "JSON"
        )
    return {k: frozenset(v) for k, v in out.items()}


@lru_cache(maxsize=1)
def _aggregate_macros() -> dict[str, bool]:
    """Per DuckDB macro that calls an aggregate: does it return a collection?

    ``json_group_array`` is a macro over ``string_agg``, ``geomean`` and
    ``weighted_avg`` are macros over aggregates: the aggregate catalog does not
    list them, so they read as scalar functions (verifier round on the merge).
    Read from the engine's own macro definitions, to a fixpoint so a macro over
    such a macro counts. A definition sqlglot cannot parse is taken as a
    collecting aggregate: fail closed. In-memory, reads no lake.
    """
    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            "SELECT DISTINCT lower(function_name), macro_definition FROM duckdb_functions() "
            "WHERE function_type = 'macro'"
        ).fetchall()
    finally:
        con.close()
    aggs = _aggregate_returns()
    out: dict[str, bool] = {}
    trees: list[tuple[str, exp.Expression]] = []
    for name, body in rows:
        try:
            trees.append((str(name), sqlglot.parse_one(f"SELECT {body}", read=SERVING_DIALECT)))
        except Exception:  # noqa: BLE001 - an unread definition proves nothing
            # Not parsed: an aggregate it calls must still be named in its text.
            words = set(re.findall(r"[a-z_][a-z0-9_]*", str(body).lower()))
            if words & set(aggs):
                out[str(name)] = True
    changed = True
    while changed:
        changed = False
        for name, tree in trees:
            calls, collects = False, False
            for node in tree.walk():
                if isinstance(node, exp.Anonymous):
                    called = str(node.name).lower()
                    if called in aggs:
                        calls, collects = True, collects or True in aggs[called]
                    elif called in out:
                        calls, collects = True, collects or out[called]
                elif isinstance(node, (exp.AggFunc, exp.List)):
                    calls, collects = True, collects or _collects(node)
            new = out.get(name, False) or collects
            if calls and out.get(name) != new:
                out[name] = new
                changed = True
    return out


def _pivot_kind(aggregates: list[exp.Expression]) -> str:
    """A PIVOT whose aggregate collects (``list(x)``) turns out collection columns."""
    return _COLLECTION if any(_collects(n) for a in aggregates for n in a.walk()) else ""


def _any(*rels: _Rel) -> str:
    """The worst kind any column of these relations has."""
    return _worst(*(r.carried for r in rels), *(k for r in rels for k in r.cols.values()))


def _collects(node: exp.Expression) -> bool:
    """An aggregate whose result is a collection of its inputs."""
    if isinstance(node, exp.List):
        return True  # DuckDB list(x); sqlglot does not file it under AggFunc
    if _array_query(node) is not None:
        return True  # ARRAY(SELECT ...) collects the query's rows (verifier round 2)
    if isinstance(node, exp.Anonymous):
        # An aggregate sqlglot does not type: ask the engine what its overloads return.
        name = str(node.name).lower()
        returns = _aggregate_returns().get(name)
        if returns is None:
            # A macro over an aggregate, or not an aggregate at all.
            return _aggregate_macros().get(name, False)
        if returns == {True}:
            return True
        return True in returns and any(
            isinstance(a, (exp.Array, exp.Tuple)) for a in node.expressions
        )
    if not isinstance(node, exp.AggFunc):
        return False
    if isinstance(node, (exp.Min, exp.Max)) and node.expressions:
        return True  # min(x, n) / max(x, n) return a list
    if isinstance(node, (exp.ArgMax, exp.ArgMin)) and node.args.get("count"):
        return True
    if isinstance(
        node, (exp.Quantile, exp.PercentileCont, exp.PercentileDisc, exp.ApproxQuantile)
    ) and any(isinstance(v, (exp.Array, exp.Tuple)) for v in node.args.values()):
        return True  # quantile(x, [0.25, 0.75]) returns a list
    return not isinstance(node, _SCALAR_AGGS)


def _array_query(node: exp.Expression) -> exp.Expression | None:
    """The query of ``ARRAY(SELECT ...)``: DuckDB's list() over that query's rows."""
    if isinstance(node, exp.Array) and len(node.expressions) == 1:
        q = node.expressions[0]
        if isinstance(q, exp.Subquery):
            q = q.this
        if isinstance(q, (exp.Select, *_SET_OPS)):
            return q
    return None


def _aggregates(node: exp.Expression) -> bool:
    """A node in aggregate position: typed, windowed, or an aggregate sqlglot does not type.

    The last is asked of DuckDB's catalog (``fsum``, ``product``, ``histogram``),
    so a scalar function sqlglot does not know (``hash``, ``date_part``) is not one.
    """
    if isinstance(node, exp.Anonymous):
        name = str(node.name).lower()
        return name in _aggregate_returns() or name in _aggregate_macros()
    return isinstance(node, (exp.AggFunc, exp.List, exp.Window))


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
        #: While a LATERAL body is analysed: the worst kind of the sources
        #: before it, which the body reads by outer reference (round 4).
        self.outer: list[str] = []

    # -- row sources ------------------------------------------------------

    def _cte(self, table: exp.Table) -> exp.CTE | None:
        if not isinstance(table.this, exp.Identifier) or table.db or table.catalog:
            return None
        name = table.name.lower()
        inside: dict[int, int] = {}  # id(WITH) -> index of the CTE the walk came out of
        node: exp.Expression | None = table.parent
        while node is not None:
            if isinstance(node, exp.CTE) and isinstance(node.parent, exp.With):
                inside[id(node.parent)] = node.parent.expressions.index(node)
            with_ = _with_of(node)
            if with_ is not None:
                # A CTE sees only the CTEs written before it (itself too when the
                # WITH is RECURSIVE); the main body sees all of them.
                limit = inside.get(id(with_), len(with_.expressions))
                if with_.args.get("recursive") and limit < len(with_.expressions):
                    limit += 1
                for cte in with_.expressions[:limit]:
                    if isinstance(cte, exp.CTE) and cte.alias.lower() == name:
                        return cte
            node = node.parent
        return None

    def _renamed(self, rel: _Rel, item: exp.Expression) -> _Rel:
        """``AS q(c1, c2)`` renames by position: no name can be followed."""
        alias = item.args.get("alias")
        if isinstance(alias, exp.TableAlias) and alias.columns:
            return _Rel(rel.repeats, {}, _any(rel), rel.reads)
        return rel

    def source(self, item: exp.Expression) -> _Rel:
        if isinstance(item, exp.Lateral) and isinstance(item.this, exp.Subquery):
            # A lateral subquery is what its body is: judged by its own rows.
            rel = self.query(item.this.this)
        elif isinstance(item, exp.UDTF):
            # VALUES, UNNEST or a table function that reads a column can only read
            # an outer one: a collection or window value passed in is refused
            # (round 7: (VALUES (list_sum(s.l || s.l))) served 15780 for 7890).
            outer = self.outer[-1] if self.outer else ""
            if outer and item.find(exp.Column) is not None:
                raise _Refuse(
                    "collection_under_function"
                    if outer == _COLLECTION
                    else "window_value_under_aggregate"
                )
            rel = _Rel(repeats="row_generator")
        elif isinstance(item, exp.Table):
            cte = self._cte(item)
            if cte is not None:
                rel = self._renamed(self.query(cte.this), cte)
            elif isinstance(item.this, exp.Identifier):
                rel = _Rel(reads={self._table_key(item): 1})  # a base table
            else:
                rel = _Rel(repeats="row_generator")  # range(n), read_csv(...)
        elif isinstance(item, exp.Subquery):
            rel = self.query(item.this)
        else:
            rel = _Rel(repeats="unrecognised_source", carried=_WINDOW)
        for pivot in item.args.get("pivots") or []:
            if not pivot.args.get("unpivot"):
                self._pivot_reads(rel, pivot.expressions)
        if item.args.get("pivots"):
            aggs = [e for p in item.args["pivots"] for e in p.expressions]
            rel = _Rel("row_turn", {}, _worst(_any(rel), _pivot_kind(aggs)), rel.reads)
        return self._renamed(rel, item)

    @staticmethod
    def _table_key(table: exp.Table) -> str:
        # The table name alone: ``inventory``, ``main.inventory`` and
        # ``lake.main.inventory`` are one table, so a self-join spelled with
        # qualifiers is still counted twice (verifier round 3). Two schemas
        # holding the same name read as one too: fail closed.
        return table.name.lower()

    @staticmethod
    def _pivot_reads(rel: _Rel, aggregates: list[exp.Expression]) -> None:
        """A PIVOT's own aggregate reads the rows of the relation it turns."""
        if not any(isinstance(n, exp.AggFunc) for a in aggregates for n in a.walk()):
            return
        if rel.repeats:
            raise _Refuse(f"{rel.repeats}_under_aggregate")
        if _any(rel):
            raise _Refuse("window_value_under_aggregate")

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
                rel = _Rel("set_operation", {}, _worst(*kinds), _sum_reads(left, right))
            elif isinstance(node, exp.Summarize):
                # SUMMARIZE aggregates every column of its input.
                inner = self.query(node.this)
                if inner.repeats or _any(inner):
                    raise _Refuse(f"{inner.repeats or 'window_value'}_under_aggregate")
                rel = _Rel(reads=inner.reads)
            elif isinstance(node, exp.Subquery):
                rel = self.source(node)
            elif isinstance(node, exp.UDTF):
                rel = _Rel(repeats="row_generator")
            elif (
                isinstance(node, exp.Alias)
                and isinstance(node.this, exp.Column)
                and not node.this.this.quoted
                and node.this.name.upper() == "TABLE"
                and not node.this.table
            ):
                # DuckDB ``TABLE t``; sqlglot reads it as ``TABLE AS t``. It is
                # ``SELECT * FROM t``, so t is followed like any FROM name: an
                # earlier CTE (a window, a ROLLUP, an UNPIVOT behind it) or a base
                # table counted toward a self-join (verifier round on the merge).
                ref = exp.to_table(str(node.alias))
                ref.parent = node
                rel = self.source(ref)
            elif (
                isinstance(node, exp.Table)
                and isinstance(node.this, exp.Identifier)
                and not node.this.quoted
                and node.name.upper() == "TABLE"
                and not node.db
                and node.alias
            ):
                # ``(TABLE t)`` as a derived table: sqlglot reads it as a table
                # named TABLE aliased t. Followed like the CTE form above.
                ref = exp.to_table(str(node.alias))
                ref.parent = node
                rel = self.source(ref)
            elif isinstance(node, exp.Pivot):
                # ``PIVOT t ON k USING SUM(v)``: the USING aggregate reads t's rows.
                inner = self.source(node.this) if node.this is not None else _Rel()
                if not node.args.get("unpivot"):
                    self._pivot_reads(inner, list(node.args.get("using") or []))
                using = list(node.args.get("using") or [])
                rel = _Rel("row_turn", {}, _worst(_any(inner), _pivot_kind(using)), inner.reads)
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
            # Any derived table after the first source may read the sources
            # before it: DuckDB binds it laterally without the keyword, and an
            # unqualified or shadowed outer reference cannot be told apart from
            # its own columns (rounds 5 and 6). Fail closed: it is analysed as
            # a LATERAL, carrying the earlier sources' kinds in and out.
            lateral = isinstance(item, exp.Lateral) or (
                bool(rels) and isinstance(item, (exp.Subquery, exp.UDTF))
            )
            if lateral:
                # Nested laterals see every enclosing source too (round 7).
                self.outer.append(
                    _worst(*(_any(r) for r in rels), self.outer[-1] if self.outer else "")
                )
                try:
                    rel = self.source(item)
                finally:
                    self.outer.pop()
            else:
                rel = self.source(item)
            if lateral:
                # A LATERAL subquery reads the sources before it, so each of its
                # columns carries the worst kind they hold: a window value passed
                # through it is still a window value (SUM over it served 428 for 107).
                prior = _worst(*(_any(r) for r in rels))
                rel = _Rel(
                    rel.repeats,
                    {k: _worst(v, prior) for k, v in rel.cols.items()},
                    _worst(rel.carried, prior),
                    rel.reads,
                )
            rels.append(rel)
            alias = str(item.alias_or_name or "").lower()
            if alias:
                # Two sources under one alias cannot be told apart: take the worse.
                seen = srcs.get(alias)
                srcs[alias] = rel if seen is None else _Rel(None, {}, _any(seen, rel))
        repeats = next((r.repeats for r in rels if r.repeats), None)
        if select.args.get("sample") is not None or any(
            isinstance(i, exp.Expression) and i.args.get("sample") is not None for i in items
        ):
            # USING SAMPLE / TABLESAMPLE: an aggregate over a sample is not the
            # table's figure (a one-table COUNT served 2 for a true 4). Listed
            # sample rows are what they say; an aggregate over them refuses.
            repeats = repeats or "sample"
        #: The kind of a column these sources hand on under a name not listed.
        src_loose = _worst(*(r.carried for r in rels))
        #: The worst kind any column of these sources has.
        src_any = _worst(*(_any(r) for r in rels))

        own = list(_own(select))
        mine = [n for n in own if n.find_ancestor(exp.Select) is select]
        if repeats and any(_aggregates(n) for n in own):
            raise _Refuse(f"{repeats}_under_aggregate")
        reads = _sum_reads(*rels)
        if any(n > 1 for n in reads.values()) and any(_aggregates(n) for n in own):
            raise _Refuse("self_join_under_aggregate")
        # A scalar subquery is one value handed to every row: an aggregate over
        # it counts it once per row (``SUM((SELECT SUM(x) FROM t))``).
        for node in own:
            if _aggregates(node) and not isinstance(node, exp.Window):
                scan = node
            elif isinstance(node, exp.Window) and isinstance(node.this, exp.Expression):
                scan = node.this
            else:
                continue
            for sub in scan.find_all(exp.Subquery):
                if sub.arg_key == "query" or isinstance(sub.parent, (exp.In, exp.Exists)):
                    continue  # a predicate's set, not a value
                raise _Refuse("window_value_under_aggregate")
        # ARRAY(q) aggregates q's rows into a list (verifier round 2): the rows
        # it reads meet the same rule as rows an aggregate reads.
        for node in mine:
            q = _array_query(node)
            if q is None:
                continue
            inner = self.query(q)
            if inner.repeats:
                raise _Refuse(f"{inner.repeats}_under_aggregate")
            if any(n > 1 for n in inner.reads.values()):
                raise _Refuse("self_join_under_aggregate")
            kind = _any(inner)
            if kind:
                raise _Refuse(
                    "collection_under_function"
                    if kind == _COLLECTION
                    else "window_value_under_aggregate"
                )
        many = _many_grouping_sets(select)
        if many and any(isinstance(n, exp.Window) for n in own):
            raise _Refuse("grouping_sets_under_aggregate")

        # Rule 4, in this SELECT: a collecting aggregate is listed as it is. So is
        # a scalar subquery that hands back a collection.
        collections: dict[str, str] = {}
        for node in mine:
            if isinstance(node, exp.Subquery) and node.arg_key not in ("this", "query"):
                inner_rel = self.query(node.this) if isinstance(node.this, exp.Select) else None
                collecting = inner_rel is not None and _any(inner_rel) == _COLLECTION
            elif isinstance(node, exp.Subquery) and isinstance(node.parent, exp.Select):
                collecting = False
            else:
                collecting = _collects(node)
            if not collecting:
                continue
            top: exp.Expression = node
            while isinstance(top.parent, _BARE):
                top = top.parent
            if top.parent is not select or top.arg_key != "expressions":
                raise _Refuse("collection_under_function")
            if isinstance(top, exp.Alias):
                collections[top.alias.lower()] = _COLLECTION

        outer = self.outer[-1] if self.outer else ""

        def col_kind(col: exp.Column, known: dict[str, str]) -> str:
            name = col.name.lower()
            table = str(col.table or "").lower()
            if table:
                rel = srcs.get(table)
                if rel is not None and not col.args.get("db"):
                    return rel.kind(name)
                # Not a source of this SELECT: a struct field, a whole-row
                # alias or an outer column. It cannot be followed.
                return _worst(src_any, known.get(table, ""), known.get(name, ""), outer)
            # A source's alias used as a column is its whole row.
            row = _any(srcs[name]) if name in srcs else ""
            return _worst(*(r.kind(name) for r in rels), known.get(name, ""), row, outer)

        # Rules 3 and 4 across SELECTs: what reads a carried value.
        if src_any or collections or outer:
            for node in own:
                if (
                    isinstance(node, (exp.Columns, exp.PositionalColumn))
                    and _worst(src_any, outer) == _COLLECTION
                ):
                    # #1 and COLUMNS(...) name a source column without a Column
                    # node: a collection reached that way may only be listed as
                    # it is (round 4: array_length(#1 || #1) served 8 for 4).
                    item = node.parent if isinstance(node.parent, exp.Alias) else node
                    if item.parent is not select or item.arg_key != "expressions":
                        raise _Refuse("collection_under_function")
                if isinstance(node, exp.Column) and not isinstance(node.this, exp.Star):
                    if col_kind(node, collections) != _COLLECTION:
                        continue
                    bare = node.parent is select or (
                        isinstance(node.parent, exp.Alias) and node.parent.parent is select
                    )
                    # The projection item itself (the column, or its alias) must sit
                    # in the SELECT list: listed as it is, never in WHERE or ORDER BY.
                    item = node.parent if isinstance(node.parent, exp.Alias) else node
                    if not bare or item.arg_key != "expressions":
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
        out = _Rel(repeats=repeats or ("grouping_sets" if many else None), reads=reads)
        if any(_adds_rows(n) for n in mine):
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
                                if name in out.cols:
                                    out.carried = _worst(out.carried, kind, out.cols[name])
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
                if name in out.cols:
                    # DuckDB renames the second ``c`` to ``c_1``: a name this
                    # relation does not list. Unlisted names take the carried kind.
                    out.carried = _worst(out.carried, kind, out.cols[name])
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


# ---- the proof (dms#531 class change, after verifier round 8) -------------------
#
# The four rules above name a refusal. Eight verifier rounds each found a shape
# they did not name, so they no longer decide alone. A statement with an
# aggregate in it serves only when it matches the grammar below, which can show
# every base row reaches each aggregate once. Everything else abstains with one
# reason, ``unproven_single_read``. The rules above still run first, so a shape
# they name keeps its reason.
#
# Proven, per SELECT that feeds an aggregate:
#   row sources  a base table (no sample, no PIVOT), a CTE read once, or a
#                derived table that is itself proven; joined only with ON or
#                USING (no comma, CROSS, NATURAL, POSITIONAL or ASOF join); no
#                LATERAL; no base table or CTE read twice in one FROM chain.
#   grouping     a plain list of expressions (no ROLLUP, CUBE, GROUPING SETS).
#   aggregates   the scalar ones only (``_SCALAR_AGGS``, or a catalog aggregate
#                that returns a scalar); their arguments are row values: no
#                subquery, window, aggregate, star (``COUNT(*)`` aside),
#                ``COLUMNS`` or positional column in them.
#   subqueries   in WHERE, HAVING or QUALIFY only as filters; in the SELECT list
#                only as a scalar, outside any aggregate or window; each one is
#                proven on its own.
#   windows      over this SELECT's own rows. A relation that carries a window
#                value or a scalar subquery's value is never read by a SELECT
#                that aggregates or windows.
#   never        a set operation, VALUES, a table function, a row-adding
#                function, ``ARRAY(subquery)``, a recursive CTE, a sample.

REASON_UNPROVEN = "unproven_single_read"
_HINTS[REASON_UNPROVEN] = (
    "this check can show each base row reaches an aggregate once only for plain "
    "reads, ON or USING joins, one GROUP BY list and scalar aggregates over row "
    "values; aggregate the base rows in that form"
)

_BAD_JOIN_METHODS = frozenset({"NATURAL", "POSITIONAL", "ASOF"})


class _Unproven(Exception):
    pass


def _aggregate_node(node: exp.Expression) -> bool:
    """In aggregate position, or a collection made from rows (``ARRAY(q)``)."""
    if isinstance(node, exp.Window):
        return True
    if _array_query(node) is not None:
        return True
    return _aggregates(node)


def _needs_proof(root: exp.Expression) -> bool:
    for node in root.walk():
        if isinstance(node, exp.Group) or _aggregate_node(node):
            return True
    return False


def _scalar_aggregate(node: exp.Expression) -> bool:
    if isinstance(node, exp.Window):
        return False
    if _collects(node):
        return False
    if isinstance(node, exp.Anonymous):
        name = str(node.name).lower()
        macros = _aggregate_macros()
        if name in macros and name not in _aggregate_returns():
            return not macros[name]  # a macro over aggregates: geomean, weighted_avg
        # Each overload's flag says "returns a collection"; scalar when none does.
        return True not in _aggregate_returns().get(str(node.name).lower(), frozenset({True}))
    return isinstance(node, _SCALAR_AGGS)


class _Proof:
    def __init__(self, root: exp.Expression) -> None:
        self.root = root
        self.cte_reads: dict[int, int] = {}

    # -- queries ----------------------------------------------------------------
    def query(self, node: exp.Expression, scope: dict[str, exp.CTE]) -> bool:
        """Prove a query. Returns True when its output carries a non-row value."""
        if isinstance(node, exp.Subquery):
            if node.args.get("sample") or node.args.get("pivots"):
                raise _Unproven
            if any(node.args.get(k) for k in ("joins", "laterals")):
                raise _Unproven
            return self.query(node.this, scope)
        ref = _table_form(node)
        if ref is not None:
            return self.source(ref, scope, {})
        if not isinstance(node, exp.Select):
            raise _Unproven  # set operations, VALUES, anything else
        scope = self._scope(node, scope)
        return self.select(node, scope)

    def _scope(self, node: exp.Expression, scope: dict[str, exp.CTE]) -> dict[str, exp.CTE]:
        with_ = _with_of(node)
        if with_ is None:
            return scope
        if with_.args.get("recursive"):
            raise _Unproven
        out = dict(scope)
        for cte in with_.expressions:
            if not isinstance(cte, exp.CTE):
                raise _Unproven
            out[str(cte.alias_or_name).lower()] = cte
        return out

    # -- one SELECT -------------------------------------------------------------
    def select(self, select: exp.Select, scope: dict[str, exp.CTE]) -> bool:
        if select.args.get("sample") or select.args.get("pivots"):
            raise _Unproven
        if select.args.get("laterals"):
            raise _Unproven
        group = select.args.get("group")
        if group is not None and _many_grouping_sets(select):
            raise _Unproven
        from_ = select.args.get("from") or select.args.get("from_")
        items: list[exp.Expression] = [from_.this] if from_ is not None else []
        for join in select.args.get("joins") or []:
            method = str(join.args.get("method") or "").upper()
            kind = str(join.args.get("kind") or "").upper()
            if method in _BAD_JOIN_METHODS or kind == "CROSS":
                raise _Unproven
            if not join.args.get("on") and not join.args.get("using"):
                raise _Unproven
            if join.args.get("pivots"):
                raise _Unproven
            items.append(join.this)
        reads: dict[str, int] = {}
        carries = False
        for item in items:
            carries = self.source(item, scope, reads) or carries
        if any(n > 1 for n in reads.values()):
            raise _Unproven
        uses = self.own(select, scope)
        if carries and uses["aggregates"]:
            raise _Unproven
        return carries or uses["carries"]

    def source(
        self, item: exp.Expression, scope: dict[str, exp.CTE], reads: dict[str, int]
    ) -> bool:
        ref = _table_form(item)
        if ref is not None:
            return self.source(ref, scope, reads)
        if isinstance(item, exp.Table):
            held = ("sample", "pivots", "joins", "laterals", "rows_from")
            if any(item.args.get(k) for k in held):
                raise _Unproven
            if not isinstance(item.this, exp.Identifier):
                raise _Unproven  # a table function: range, read_csv, ...
            name = str(item.name).lower()
            cte = scope.get(name) if not item.args.get("db") else None
            if cte is not None:
                key = f"cte:{id(cte)}"
                reads[key] = reads.get(key, 0) + 1
                self.cte_reads[id(cte)] = self.cte_reads.get(id(cte), 0) + 1
                if self.cte_reads[id(cte)] > 1:
                    raise _Unproven
                # A CTE sees the CTEs before it, as written; the outer scope is a
                # superset, which is enough for a proof of single reads.
                return self.query(cte.this, scope)
            parts = (item.args.get("catalog"), item.args.get("db"), item.this)
            key = ".".join(str(p.name).lower() for p in parts if p)
            reads[key] = reads.get(key, 0) + 1
            return False
        if isinstance(item, exp.Subquery):
            return self.query(item, scope)
        raise _Unproven  # VALUES, UNNEST, LATERAL, anything else

    def own(self, select: exp.Select, scope: dict[str, exp.CTE]) -> dict[str, bool]:
        aggregates = False
        carries = False
        window_fns: set[int] = set()
        for key, value in select.args.items():
            if key in _SOURCE_ARGS:
                continue
            for item in value if isinstance(value, list) else [value]:
                if not isinstance(item, exp.Expression):
                    continue
                for node in self._walk(item):
                    if isinstance(node, (exp.Select, *_SET_OPS, exp.Values)):
                        if not isinstance(node, exp.Select):
                            raise _Unproven
                        if not self._placed_subquery(node, key, select):
                            raise _Unproven
                        carries = carries or key == "expressions"
                        self.query(node, scope)
                        continue
                    if _adds_rows(node) or isinstance(node, (exp.Unnest, exp.Explode)):
                        raise _Unproven
                    if _array_query(node) is not None:
                        raise _Unproven
                    if isinstance(node, exp.Window):
                        aggregates = carries = True
                        fn = node.this
                        if isinstance(fn, exp.Expression) and _aggregates(fn):
                            if not _scalar_aggregate(fn):
                                raise _Unproven
                            window_fns.add(id(fn))
                            # Over this SELECT's rows or groups: an aggregate may sit
                            # in a window's argument (``SUM(SUM(x)) OVER ()``).
                            _row_values(fn, nested_ok=True)
                    elif id(node) not in window_fns and _aggregates(node):
                        aggregates = True
                        if not _scalar_aggregate(node):
                            raise _Unproven
                        _row_values(node, nested_ok=False)
        return {"aggregates": aggregates, "carries": carries}

    @staticmethod
    def _walk(item: exp.Expression) -> Iterator[exp.Expression]:
        """Every node of ``item``, but not inside a nested query (proven on its own)."""
        stack = [item]
        while stack:
            node = stack.pop()
            yield node
            if isinstance(node, (exp.Select, *_SET_OPS, exp.Values)):
                continue
            stack.extend(c for c in node.iter_expressions())

    def _placed_subquery(self, node: exp.Expression, key: str, select: exp.Select) -> bool:
        """A nested query used as a filter, or as a scalar outside any aggregate."""
        if key in ("where", "having", "qualify"):
            return True
        if key != "expressions":
            return False
        holder = node.parent
        if isinstance(holder, exp.Subquery):
            holder = holder.parent
        walker = holder
        while walker is not None and walker is not select:
            if isinstance(walker, exp.Window) or _aggregates(walker):
                return False
            if _array_query(walker) is not None:
                return False
            walker = walker.parent
        return True


def _table_form(node: exp.Expression) -> exp.Table | None:
    """DuckDB ``TABLE t`` (``SELECT * FROM t``), as sqlglot reads its two spellings."""
    named = ""
    if (
        isinstance(node, exp.Alias)
        and isinstance(node.this, exp.Column)
        and not node.this.this.quoted
        and node.this.name.upper() == "TABLE"
        and not node.this.table
    ):
        named = str(node.alias)
    elif (
        isinstance(node, exp.Table)
        and isinstance(node.this, exp.Identifier)
        and not node.this.quoted
        and node.name.upper() == "TABLE"
        and not node.db
        and node.alias
    ):
        named = str(node.alias)
    if not named:
        return None
    ref = exp.to_table(named)
    ref.parent = node
    return ref


def _row_values(agg: exp.Expression, *, nested_ok: bool) -> None:
    """An aggregate's arguments are values of one row: no query, window, star
    (``COUNT(*)`` aside), ``COLUMNS``, positional column, or (outside a window's
    own function) another aggregate."""
    if isinstance(agg, exp.Count) and isinstance(agg.this, exp.Star):
        return
    for arg in agg.iter_expressions():
        for node in arg.walk():
            if isinstance(node, (exp.Select, *_SET_OPS, exp.Values, exp.Window)):
                raise _Unproven
            if isinstance(node, (exp.Star, exp.Columns, exp.PositionalColumn)):
                if not (isinstance(node, exp.Star) and isinstance(node.parent, exp.Count)):
                    raise _Unproven
            if _aggregates(node) and (not nested_ok or not _scalar_aggregate(node)):
                raise _Unproven


def _prove(root: exp.Expression) -> None:
    proof = _Proof(root)
    proof.query(root, {})


def repeated_input_reason(sql: str, dialect: str | None = SERVING_DIALECT) -> str | None:
    """``repeated_input:<why>`` when an aggregate can read one input value twice.

    None when every aggregate in ``sql`` reads rows that were only filtered,
    computed per row, or grouped once. See the module docstring for the class
    and for what it cannot see.
    """
    try:
        # The statement runs on the serving engine: no dialect means its dialect.
        roots = [r for r in sqlglot.parse(sql, read=dialect or SERVING_DIALECT) if r is not None]
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
    if _needs_proof(root):
        try:
            _prove(root)
        except Exception:  # noqa: BLE001 - _Unproven, or a proof we cannot finish
            return f"{REASON_REPEATED_INPUT}:{REASON_UNPROVEN} ({_HINTS[REASON_UNPROVEN]})"
    return None


__all__ = ["REASON_REPEATED_INPUT", "REASON_UNPROVEN", "repeated_input_reason"]
