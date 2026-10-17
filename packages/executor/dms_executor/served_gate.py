"""Served-result check. Runs before a confident badge on generated SQL.

Fails:

- Fan-out: served rows are not distinct on the grain the query aggregates
  to (parsed GROUP BY, else the non-aggregate output keys), or a SUM/AVG
  repeats a column inside that grain. A direct many-to-many join of two
  plain tables is the same fail. An extra result column is not a fail.
- As-of window: an INTERVAL whose anchor day is not the request as_of.
  A clock anchor means that bound day, on both sides.

- Fan-out is read from the lake, not the ontology: a base table's rowid
  that repeats within a group means its rows were multiplied by a join.
  Every aggregating SELECT is checked (outer query, CTE bodies, derived
  tables, subqueries, UNION arms), a measure read from a CTE is traced into
  its body, and the query with its mergeable CTEs inlined is checked too.

Grants are not decided here. No question words and no case ids. A parse
error does not flag; a probe that cannot run (an error, the time bound, a
busy lake) does not pass: the answer abstains as grain_unprobed. A
correlated subquery is not probed on its own (a named gap).
``served_check_shadow`` stays the recorder.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from dms_executor.demo_warehouse import DEMO_TABLES

_DIALECT = "duckdb"
_LAKE = frozenset(t.lower() for t in DEMO_TABLES)
_ALIAS_PREFIX = "warehouse_"
#: DuckDB folds identifier case for ASCII letters only; Python's lower() also
#: folds other letters (the Kelvin sign becomes k).
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _fold(name: Any) -> str:
    return str(name or "").translate(_ASCII_LOWER)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CLOCK_NAMES = frozenset({"now", "getdate", "current_date", "current_timestamp"})


def lake_alias_sql(sql: str) -> str:
    """The SQL with Cortex ``warehouse_<table>`` aliases named as lake tables.

    The engine cites its warehouse under that alias; the DMS lake holds the
    table itself. Grain and EXPLAIN checks run on the lake name so an aliased
    answer is checked, not skipped. SQL with no alias comes back unchanged.
    """
    try:
        tree = parse_one(sql or "", read=_DIALECT)
    except Exception:  # noqa: BLE001 - unparseable SQL is not evidence
        return sql
    return sql if not _unalias(tree) else tree.sql(dialect=_DIALECT)


def _unalias(tree: exp.Expression) -> bool:
    """Name Cortex's warehouse the way the lake does.

    The engine cites a lake table as ``warehouse_<table>`` or in a schema
    named ``warehouse`` (``"warehouse"."inventory"``); the grant check reads
    both as ``<table>``, and the lake holds it in ``main``.
    """
    changed = False
    renamed: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        name = table.name.translate(_ASCII_LOWER)
        db = str(table.db or "").translate(_ASCII_LOWER)
        if table.catalog or not isinstance(table.this, exp.Identifier):
            continue
        if db == _ALIAS_PREFIX.rstrip("_") and name in _LAKE:
            table.set("db", None)
            changed = True
            continue
        if db not in ("", "main") or not name.startswith(_ALIAS_PREFIX) or name in _LAKE:
            continue
        lake = name[len(_ALIAS_PREFIX) :]
        if lake in _LAKE:
            table.set("this", exp.to_identifier(lake))
            changed = True
            if not table.alias:
                renamed[name] = lake
    # warehouse_inventory.sku (or main.warehouse_inventory.sku) qualifies by
    # the table name, which is renamed too; warehouse.inventory.sku drops the
    # schema with its table.
    for column in tree.find_all(exp.Column):
        if column.args.get("catalog"):
            continue
        db = str(column.args.get("db") or "").translate(_ASCII_LOWER)
        qual = str(column.table or "").translate(_ASCII_LOWER)
        if db == _ALIAS_PREFIX.rstrip("_") and qual in _LAKE:
            column.set("db", None)
            continue
        bare = renamed.get(qual)
        if bare is not None and db in ("", "main"):
            column.set("table", exp.to_identifier(bare))
    return changed


def served_result_reason(
    sql: str,
    *,
    warehouse: Path | str | None = None,
    as_of: str | None = None,
    ontology: Any | None = None,
) -> str | None:
    """Reason code, or None when this SQL may take a badge.

    The probe executes SQL on the lake, so only SQL the one grant function
    accepted is handed to this check. Rejected SQL never reaches it and never
    takes a badge (map_ask_response_to_envelope). The fan-out checks read the
    lake itself (each base table's rowid), so they need no ontology; the
    ontology only adds the many-to-many link check.
    """
    try:
        tree = parse_one(sql or "", read=_DIALECT)
    except Exception:  # noqa: BLE001 - unparseable SQL is not evidence
        return None
    if not isinstance(tree, exp.Query):
        return None
    _unalias(tree)
    window = _as_of_window(tree, as_of)
    if window:
        return window
    if isinstance(tree, exp.Select):
        many = _direct_many_to_many(tree, ontology)
        if many:
            return many
    path = Path(warehouse) if warehouse is not None else None
    if path is None or not path.is_file():
        return None
    try:
        qualified = _qualified(tree, path)
        # A mergeable CTE or derived table is checked as the outer query's own
        # join once sqlglot inlines it (a filtered fact CTE counted per
        # dimension is then the fact table, at its own grain). What cannot be
        # inlined stays in the tree and is checked where it stands.
        merged = _merged(qualified)
        if merged is None:
            return _fanout(qualified, path)
        if isinstance(merged, exp.Select):
            many = _direct_many_to_many(merged, ontology)
            if many:
                return many
        return _fanout(merged, path)
    except _Unprobed:
        return "grain_unprobed"


def _lake_schema(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    """schema -> table -> column -> type for every user schema (main, bronze, ...).

    The probe cannot run without it, so a read that fails is unprobed.
    """
    from dms_executor.demo_warehouse import connect_serving

    try:
        con = connect_serving(path)
        try:
            rows = con.execute(
                "SELECT table_schema, table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        raise _Unprobed(type(exc).__name__) from exc
    schema: dict[str, dict[str, dict[str, str]]] = {}
    for db, table, column, kind in rows:
        schema.setdefault(str(db), {}).setdefault(str(table), {})[str(column)] = str(kind)
    return schema


def _qualified(tree: exp.Query, path: Path) -> exp.Query:
    """Columns qualified against the lake's own catalog, or the tree as it is."""
    schema = _lake_schema(path)
    try:
        from sqlglot.optimizer.qualify import qualify

        out = qualify(
            tree.copy(),
            schema=schema,
            db="main",
            dialect=_DIALECT,
            validate_qualify_columns=False,
            quote_identifiers=False,
        )
    except Exception:  # noqa: BLE001 - an unattributable measure is unprobed below
        return tree.copy()
    return out if isinstance(out, exp.Query) else tree.copy()


def _merged(tree: exp.Query) -> exp.Query | None:
    """The query with its mergeable CTEs and derived tables inlined, or None."""
    if not any(
        isinstance(node, (exp.With, exp.Subquery)) for node in tree.find_all(exp.With, exp.Subquery)
    ):
        return None
    if any(join.args.get("method") for join in tree.find_all(exp.Join)):
        # Inlining rewrites a NATURAL join's columns; check the query as written.
        return None
    try:
        from sqlglot.optimizer.eliminate_subqueries import eliminate_subqueries
        from sqlglot.optimizer.merge_subqueries import merge_subqueries

        merged = merge_subqueries(eliminate_subqueries(tree.copy()))
    except Exception:  # noqa: BLE001 - nothing inlined is not evidence either way
        return None
    if not isinstance(merged, exp.Query):
        return None
    if merged.sql(dialect=_DIALECT) == tree.sql(dialect=_DIALECT):
        return None
    return merged


def _fanout(tree: exp.Query, path: Path) -> str | None:
    """A SUM, AVG or COUNT whose rows repeat what it adds up, anywhere in the query.

    Every SELECT that aggregates is checked where it stands: the outer query,
    CTE bodies, derived tables, subqueries and UNION arms, on the aggregates
    in its SELECT list, HAVING, ORDER BY and QUALIFY. A query that does not
    aggregate is checked on the rows it returns.
    """
    for select in tree.find_all(exp.Select):
        if _in_recursive_cte(select) or _correlated(select):
            continue
        found = _select_fanout(select, path)
        if found:
            return found
    if isinstance(tree, exp.Select) and not _grouping_aggs(tree):
        names = _output_grain(tree)
        if names and _joins_under(tree) and _row_counts(path, _result_probe(tree, names)):
            return "fanout_subject_keys"
    return None


def _select_fanout(select: exp.Select, path: Path) -> str | None:
    """The reason an aggregate here adds up rows its joins repeated, or None.

    A joined row set is at the grain of a source when that source's row id
    appears once per group: a base table's rowid, or a row number the probe
    gives each CTE or derived table it reads. SUM, AVG and COUNT of an
    expression are inflated when every source the expression reads repeats
    within a group (revenue as t.qty * i.price over transactions joined to
    inventory is per transaction: t stays unique), or when the value it reads
    from a CTE was itself inflated inside that CTE. COUNT(*) is inflated when
    no joined source stays unique or a join links nothing, and it is wrong
    per parent when an outer join leaves parents with no child (each counts
    as 1). COUNT(DISTINCT) is not affected by repetition. Rows an outer join
    leaves unmatched carry no row id and are not counted against it.
    """
    targets = [node for node in _select_aggs(select) if _counts_rows(node)]
    if not targets:
        return None
    sources = _source_map(select)
    if not sources or select.args.get("from") is None:
        return None
    rids = {alias: _rid(alias, kind) for alias, (kind, _) in sources.items()}
    scope = _probe_scope(select, sources)
    prefix = _visible_with(select)
    groups = _group_sql(select)
    joined = bool(select.args.get("joins"))
    cache: dict[str, bool] = {}

    def repeats(alias: str) -> bool:
        if alias not in cache:
            cache[alias] = _repeats(path, prefix, scope, rids[alias], groups)
        return cache[alias]

    for agg in targets:
        columns = list(agg.find_all(exp.Column))
        if not columns:
            # COUNT(*), COUNT(1), SUM(1): the number of joined rows.
            if not joined:
                continue
            # A count of joined rows counts pairs when a join links nothing:
            # inventory JOIN locations ON TRUE "per sku" counts locations.
            if _cartesian(select, sources) or all(map(repeats, sources)):
                return "fanout_subject_keys"
            row_filter = agg.parent.expression if isinstance(agg.parent, exp.Filter) else None
            if _unmatched_parents(
                select, sources, groups, path, prefix, scope, rids, repeats, row_filter
            ):
                return "outer_join_count"
            continue
        if joined and any(not str(c.table or "") for c in columns) and len(sources) > 1:
            # Which source a bare column reads cannot be told: not certified.
            raise _Unprobed("unattributed_column")
        # Each additive term of each argument; a conditional count (SUM(CASE
        # WHEN i.low THEN 1 ELSE 0 END)) carries no column, so it is judged
        # by every column it reads, as a count of joined rows of that source.
        terms = [t for arg in agg.iter_expressions() for t in _value_terms(arg)] or [agg]
        for term in terms:
            verdicts: list[bool] = []
            for alias, names in _arg_sources(term, sources).items():
                kind, body = sources[alias]
                verdict = joined and repeats(alias)
                if kind == "body" and not verdict:
                    verdict = bool(_body_repeats(path, body, names, 0))
                verdicts.append(verdict)
            if verdicts and all(verdicts):
                return "fanout_subject_keys"
    return None


def _value_terms(node: exp.Expression) -> list[exp.Expression]:
    """The parts of an aggregate's argument that carry the value it adds up.

    A sum distributes over + and -, so SUM(i.q - t.q) is inflated when the
    i.q term's source repeats, whatever t does. A CASE or IF condition picks
    a branch and adds nothing: in SUM(CASE WHEN t.x THEN i.q END) only i.q is
    summed. A product is one term: t.qty * i.price is per transaction while
    t stays unique, but i.q * (CASE WHEN t.x THEN 1 ELSE 0 END) carries only
    i.q. ORDER BY inside an aggregate orders and states nothing.
    """
    if isinstance(node, exp.Column):
        return [node]
    if isinstance(node, (exp.Predicate, exp.Connector, exp.Not, exp.Sign)):
        # A comparison or a sign picks rows or flips them, like a CASE
        # condition: (t.x = 'out')::INT as a flag carries no value of its own.
        return []
    if isinstance(node, (exp.Add, exp.Sub)):
        return _value_terms(node.this) + _value_terms(node.expression)
    if isinstance(node, (exp.Paren, exp.Neg, exp.Cast, exp.TryCast, exp.Order, exp.Round)):
        # ROUND(i.q - t.q, 2) sums like its first argument.
        return _value_terms(node.this)
    if isinstance(node, exp.Case):
        branches = [branch.args.get("true") for branch in node.args.get("ifs") or []]
        branches.append(node.args.get("default"))
        return [t for b in branches if b is not None for t in _value_terms(b)]
    if isinstance(node, exp.If):
        branches = [node.args.get("true"), node.args.get("false")]
        return [t for b in branches if b is not None for t in _value_terms(b)]
    if isinstance(node, exp.Coalesce):
        return [t for arg in (node.this, *node.expressions) for t in _value_terms(arg)]
    parts: list[exp.Expression] = []
    for child in node.iter_expressions():
        parts.extend(_value_terms(child))
    if not parts:
        return []
    if len(parts) == 1:
        return parts
    # A product or any other function of several parts is one value.
    return [exp.Tuple(expressions=[part.copy() for part in parts])]


#: Aggregates whose value does not change when a row repeats.
_REPEAT_SAFE: tuple[type, ...] = tuple(
    node
    for node in (
        getattr(exp, name, None)
        for name in (
            "Min",
            "Max",
            "AnyValue",
            "ArgMax",
            "ArgMin",
            "First",
            "Last",
            "LogicalAnd",
            "LogicalOr",
            "ApproxDistinct",
            "BitwiseAndAgg",
            "BitwiseOrAgg",
        )
    )
    if isinstance(node, type)
)


def _counts_rows(node: exp.Expression) -> bool:
    """An aggregate a repeated row changes: SUM, AVG, COUNT, MEDIAN, STRING_AGG, ...

    MIN, MAX and their kind do not change, nor does COUNT(DISTINCT) or any
    other aggregate over DISTINCT values, except SUM and AVG (a DISTINCT sum
    of a repeated table is still not the table's total).
    """
    if not _is_agg(node) or isinstance(node, _REPEAT_SAFE):
        return False
    first = node.expressions[0] if isinstance(node, exp.Anonymous) and node.expressions else None
    if isinstance(node.this, exp.Distinct) or isinstance(first, exp.Distinct):
        return isinstance(node, (exp.Sum, exp.Avg))
    return True


def _single_row(body: Any) -> bool:
    """A CTE or derived query that returns at most one row.

    An aggregate with no GROUP BY (a total for a percent-of-total), a SELECT
    with no FROM (constants), or LIMIT 1.
    """
    while isinstance(body, exp.Subquery):
        body = body.this
    if not isinstance(body, exp.Select):
        return False
    limit = body.args.get("limit")
    if limit is not None and limit.expression is not None and limit.expression.sql() == "1":
        return True
    if body.args.get("group") is not None:
        return False
    return body.args.get("from") is None or bool(_grouping_aggs(body))


def _select_aggs(select: exp.Select) -> list[exp.Expression]:
    """Aggregates in the SELECT list, HAVING, ORDER BY and QUALIFY, not in subqueries."""
    found = _outer_aggs(select)
    for key in ("having", "order", "qualify"):
        node = select.args.get(key)
        if node is None:
            continue
        stack: list[exp.Expression] = [node]
        while stack:
            item = stack.pop()
            if isinstance(item, (exp.Subquery, exp.Select)):
                continue
            if _is_agg(item):
                found.append(item)
                continue
            stack.extend(item.iter_expressions())
    return found


def _rid_column(alias: str) -> str:
    """The row-number column the probe adds to a CTE or derived source.

    One name per alias, so a NATURAL join of two numbered sources does not
    join on it.
    """
    return "__dms_rid_" + re.sub(r"\W", "_", alias)


def _rid(alias: str, kind: str) -> str:
    name = exp.to_identifier(alias).sql(dialect=_DIALECT)
    return f"{name}.rowid" if kind == "base" else f"{name}.{_rid_column(alias)}"


def _probe_scope(select: exp.Select, sources: dict[str, tuple[str, Any]]) -> str:
    """FROM, JOINs and WHERE of this SELECT, each CTE or derived source numbered.

    A CTE or derived table has no rowid; wrapping it as
    ``(SELECT *, row_number() OVER () AS __dms_rid FROM <it>) AS <alias>``
    gives each of its rows one, and every column reference still binds.
    """
    probe = select.copy()

    def numbered(node: exp.Expression, alias: str) -> exp.Expression:
        inner = node.sql(dialect=_DIALECT)
        wrapped = parse_one(
            f"SELECT * FROM (SELECT *, row_number() OVER () AS {_rid_column(alias)} "
            f"FROM {inner}) AS {exp.to_identifier(alias).sql(dialect=_DIALECT)}",
            read=_DIALECT,
        )
        source = wrapped.args["from"].this
        return source.copy()

    def wrap(node: Any) -> Any:
        alias = _source_alias(node)
        if alias is not None and sources.get(alias, ("", None))[0] == "body":
            return numbered(node, alias)
        return node

    frm = probe.args.get("from")
    if frm is not None:
        frm.set("this", wrap(frm.this))
    for join in probe.args.get("joins") or []:
        join.set("this", wrap(join.this))
    return _pre_group_scope(probe)


def _source_alias(node: Any) -> str | None:
    if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
        return (_alias_name(node) or node.name).translate(_ASCII_LOWER)
    if isinstance(node, exp.Subquery) and node.alias:
        return node.alias.translate(_ASCII_LOWER)
    return None


def _all_aliases(select: exp.Select) -> set[str]:
    """Every FROM and JOIN alias, generators and unnest included."""
    out: set[str] = set()
    for node in _sources(select):
        alias = node.alias_or_name if isinstance(node, exp.Expression) else ""
        if alias:
            out.add(str(alias).translate(_ASCII_LOWER))
    return out


def _correlated(select: exp.Select) -> bool:
    """A subquery that reads a column of an enclosing query.

    Its FROM cannot be probed on its own; it is not checked (a named gap).
    """
    if select.parent is None:
        return False
    own = _all_aliases(select)
    for column in select.find_all(exp.Column):
        holder = column.parent
        while holder is not None and holder is not select and not isinstance(holder, exp.Select):
            holder = holder.parent
        if holder is not select:
            continue
        qual = str(column.table or "").translate(_ASCII_LOWER)
        if qual and qual not in own and qual not in {
            str(c.alias).translate(_ASCII_LOWER) for c in _visible_ctes(select)
        }:
            return True
    return False


def _cartesian(select: exp.Select, sources: dict[str, tuple[str, Any]]) -> bool:
    """A join to a table or CTE that no condition links to another source.

    ON TRUE, CROSS JOIN, or a comma join with no WHERE condition between the
    two. Generators, unnest and LATERAL are not counted: a spine is joined
    that way on purpose. NATURAL and USING joins link by name. A source of at
    most one row (a total, constants) repeats nothing.
    """
    where = select.args.get("where")
    aliases = _all_aliases(select)
    for join in select.args.get("joins") or []:
        alias = _source_alias(join.this)
        if alias is None or join.args.get("using") or join.args.get("method"):
            continue
        kind, body = sources.get(alias, ("", None))
        if kind == "body" and _single_row(body):
            continue
        conditions = [c for c in (join.args.get("on"), where) if c is not None]
        if not any(_links(cond, alias, aliases) for cond in conditions):
            return True
    return False


def _links(condition: exp.Expression, alias: str, aliases: set[str]) -> bool:
    """A conjunct of this condition reads ``alias`` and another source."""
    stack: list[exp.Expression] = [condition]
    while stack:
        node = stack.pop()
        while isinstance(node, exp.Paren):
            node = node.this
        if isinstance(node, exp.And):
            stack.extend([node.this, node.expression])
            continue
        columns = list(node.find_all(exp.Column))
        quals = {str(c.table or "").translate(_ASCII_LOWER) for c in columns}
        if "" in quals and len(columns) > 1:
            return True  # unqualified columns: cannot tell, do not refuse
        if alias in quals and (quals - {alias}) & aliases:
            return True
    return False


def _unmatched_parents(
    select: exp.Select,
    sources: dict[str, tuple[str, Any]],
    groups: list[str],
    path: Path,
    prefix: str,
    scope: str,
    rids: dict[str, str],
    repeats: Any,
    row_filter: exp.Expression | None = None,
) -> bool:
    """COUNT(*) of children per parent over an outer join that left a parent childless.

    Such a parent counts as 1. The count is of children when the preserved
    side repeats (one row per child), or when each group holds one preserved
    row (grouped by the parent's own key, or by a spine's own value), so a
    count per group can only be a count of children. A lookup join counted
    per attribute keeps the preserved side unique with many rows per group;
    its count is of the preserved rows and is right. A FILTER that reads the
    child side drops the childless rows (a NULL child fails it).
    """
    filtered = {
        str(c.table or "").translate(_ASCII_LOWER) for c in row_filter.find_all(exp.Column)
    } if row_filter is not None else set()
    nodes = _sources(select)
    order = [str(n.alias_or_name or "").translate(_ASCII_LOWER) for n in nodes]
    group_quals = {
        str(c.table or "").translate(_ASCII_LOWER)
        for g in groups
        for c in parse_one(g, read=_DIALECT).find_all(exp.Column)
    }
    for index, join in enumerate(select.args.get("joins") or [], start=1):
        side = str(join.args.get("side") or "").upper()
        if side not in {"LEFT", "RIGHT", "FULL"}:
            continue
        left, right = order[:index], order[index : index + 1]
        pairs = []
        if side in {"LEFT", "FULL"}:
            pairs.append((left, right))
        if side in {"RIGHT", "FULL"}:
            pairs.append((right, left))
        for preserved, nullable in pairs:
            nullable = [a for a in nullable if a in sources]
            if not preserved or not nullable:
                continue
            probed = [a for a in preserved if a in sources]
            per_child = bool(probed) and all(map(repeats, probed))
            if not per_child and groups:
                spines = [a for a in preserved if a not in sources]
                per_child = any(a in group_quals for a in spines) or (
                    bool(probed)
                    and all(_one_per_group(path, prefix, scope, rids[a], groups) for a in probed)
                )
            if not per_child:
                continue
            for alias in nullable:
                if alias in filtered:
                    continue
                probe = (
                    f"{prefix}SELECT COUNT(*) FILTER (WHERE {rids[alias]} IS NULL) AS n, "
                    f"0 AS d {scope}"
                )
                if _row_counts(path, probe):
                    return True
    return False


def _one_per_group(path: Path, prefix: str, scope: str, rid: str, groups: list[str]) -> bool:
    """Every group holds exactly one row of this source."""
    keys = ", ".join(groups)
    probe = (
        f"{prefix}SELECT COUNT(DISTINCT ({keys}, {rid})) AS n, COUNT(DISTINCT ({keys})) AS d "
        f"{scope}"
    )
    return not _row_counts(path, probe)


def _arg_sources(
    arg: exp.Expression, sources: dict[str, tuple[str, Any]]
) -> dict[str, set[str]]:
    """Source alias -> column names an aggregate's argument reads from it."""
    out: dict[str, set[str]] = {}
    for column in arg.find_all(exp.Column):
        qual = str(column.table or "").translate(_ASCII_LOWER)
        if not qual and len(sources) == 1:
            qual = next(iter(sources))
        if qual in sources:
            out.setdefault(qual, set()).add(column.name.translate(_ASCII_LOWER))
    return out


def _body_repeats(path: Path, body: Any, names: set[str], depth: int) -> bool | None:
    """Whether a CTE or derived table repeats the rows behind these columns.

    None when it cannot be told. An arm that aggregates, dedupes (DISTINCT,
    QUALIFY) or limits by window is at its own grain; it is checked as a
    SELECT in its own right.
    """
    if depth > 3:
        # Traced this far without reaching a table: not certified.
        raise _Unprobed("lineage_depth")
    arms = _union_arms(body)
    if not arms:
        return None
    result: bool | None = None
    for arm in arms:
        if _grouping_aggs(arm) or arm.args.get("group") is not None:
            result = bool(result)
            continue
        sources = _source_map(arm)
        if not sources or arm.args.get("from") is None:
            continue
        rids = {alias: _rid(alias, kind) for alias, (kind, _) in sources.items()}
        scope = _probe_scope(arm, sources)
        prefix = _visible_with(arm)
        joined = bool(arm.args.get("joins"))
        filtered = arm.args.get("distinct") is not None or arm.args.get("qualify") is not None
        for name in names:
            projection = next(
                (p for p in arm.expressions if _fold(p.alias_or_name) == name), None
            )
            if projection is None:
                continue
            verdicts: list[bool] = []
            for alias, inner in _arg_sources(projection, sources).items():
                kind, inner_body = sources[alias]
                if filtered:
                    # DISTINCT or QUALIFY keeps fewer rows: count repeats after it.
                    verdict = joined and _filtered_repeats(path, arm, prefix, scope, rids[alias])
                else:
                    verdict = joined and _repeats(path, prefix, scope, rids[alias], [])
                if kind == "body" and not verdict:
                    verdict = bool(_body_repeats(path, inner_body, inner, depth + 1))
                verdicts.append(verdict)
            if verdicts:
                if all(verdicts):
                    return True
                result = bool(result)
    return result


def _filtered_repeats(path: Path, arm: exp.Select, prefix: str, scope: str, rid: str) -> bool:
    """Whether a source's rows still repeat after the arm's DISTINCT or QUALIFY.

    SELECT DISTINCT i.sku, i.q FROM i JOIN t collapses the join's repeats;
    adding t.txn_type to the list keeps one row per type, and i repeats.
    """
    distinct = arm.args.get("distinct")
    qualify = arm.args.get("qualify")
    head = distinct.sql(dialect=_DIALECT) if distinct is not None else ""
    projections = ", ".join(p.sql(dialect=_DIALECT) for p in arm.expressions)
    tail = f" {qualify.sql(dialect=_DIALECT)}" if qualify is not None else ""
    probe = (
        f"{prefix}SELECT COUNT(*) FILTER (WHERE __dms_r IS NOT NULL) AS n, "
        f"COUNT(DISTINCT __dms_r) AS d FROM (SELECT {head} {projections}, {rid} AS __dms_r "
        f"{scope}{tail}) AS __dms_f"
    )
    return _row_counts(path, probe)


def _union_arms(body: Any) -> list[exp.Select]:
    while isinstance(body, exp.Subquery):
        body = body.this
    if isinstance(body, exp.Select):
        return [body]
    if isinstance(body, exp.SetOperation):
        return [*_union_arms(body.this), *_union_arms(body.expression)]
    return []


def _source_map(select: exp.Select) -> dict[str, tuple[str, Any]]:
    """alias -> ("base", table) or ("body", the CTE or derived query it reads)."""
    ctes = {str(cte.alias).translate(_ASCII_LOWER): cte.this for cte in _visible_ctes(select)}
    out: dict[str, tuple[str, Any]] = {}
    for node in _sources(select):
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
            alias = (_alias_name(node) or node.name).translate(_ASCII_LOWER)
            name = node.name.translate(_ASCII_LOWER)
            if not (node.db or node.catalog) and name in ctes:
                out[alias] = ("body", ctes[name])
            else:
                out[alias] = ("base", node)
        elif isinstance(node, exp.Subquery) and node.alias:
            out[node.alias.translate(_ASCII_LOWER)] = ("body", node.this)
    return out


def _repeats(path: Path, prefix: str, scope: str, rid: str, groups: list[str]) -> bool:
    """True when a source's rows appear more than once within a group."""
    distinct = ", ".join([*groups, rid])
    probe = (
        f"{prefix}SELECT COUNT(*) FILTER (WHERE {rid} IS NOT NULL) AS n, "
        f"COUNT(DISTINCT ({distinct})) FILTER (WHERE {rid} IS NOT NULL) AS d {scope}"
    )
    return _row_counts(path, probe)


def _group_sql(select: exp.Select) -> list[str]:
    """The finest grouping, as SQL: ordinals and GROUP BY ALL resolved, rollups flattened."""
    group = select.args.get("group")
    if group is None:
        return []
    projections = list(select.expressions or [])
    if group.args.get("all"):
        return [p.unalias().sql(dialect=_DIALECT) for p in projections if not _has_agg(p)]
    items: list[exp.Expression] = list(group.expressions or [])
    # ROLLUP, CUBE and GROUPING SETS: the finest grouping is every expression
    # any of their sets names; tuples and the empty set () are unwrapped.
    stack: list[Any] = [
        node for key in ("rollup", "cube", "grouping_sets") for node in group.args.get(key) or []
    ]
    while stack:
        node = stack.pop(0)
        if isinstance(node, (exp.Tuple, exp.Paren, exp.Rollup, exp.Cube, exp.GroupingSets)):
            stack[:0] = list(node.expressions) if not isinstance(node, exp.Paren) else [node.this]
            continue
        items.append(node)
    aliases = {
        _fold(p.alias): p.unalias() for p in projections if isinstance(p, exp.Alias) and p.alias
    }
    out: list[str] = []
    for item in items:
        if isinstance(item, exp.Literal) and item.is_int:
            index = int(item.name) - 1
            if 0 <= index < len(projections):
                item = projections[index].unalias()
        elif isinstance(item, exp.Column) and not item.table and _fold(item.name) in aliases:
            # GROUP BY an output alias: the probe has no SELECT list to name it.
            item = aliases[_fold(item.name)]
        text = item.sql(dialect=_DIALECT)
        if text not in out:
            out.append(text)
    return out


def _visible_ctes(node: exp.Expression) -> list[exp.CTE]:
    """CTEs this node may read, outermost first; an inner one shadows an outer one."""
    levels: list[list[exp.CTE]] = []
    own = node.args.get("with")
    if isinstance(own, exp.With):
        levels.append(list(own.expressions))
    child: Any = node
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.CTE) and isinstance(parent.parent, exp.With):
            holder = parent.parent
            earlier: list[exp.CTE] = []
            for cte in holder.expressions:
                if cte is parent:
                    break
                earlier.append(cte)
            levels.append(earlier)
            owner = holder.parent
            child = owner
            parent = owner.parent if owner is not None else None
            continue
        owned = parent.args.get("with") if isinstance(parent, exp.Expression) else None
        if isinstance(owned, exp.With) and child is not owned:
            levels.append(list(owned.expressions))
        child = parent
        parent = parent.parent
    seen: dict[str, exp.CTE] = {}
    for level in reversed(levels):
        for cte in level:
            name = str(cte.alias).translate(_ASCII_LOWER)
            seen.pop(name, None)
            seen[name] = cte
    return list(seen.values())


def _visible_with(node: exp.Expression) -> str:
    """A WITH clause naming the CTEs this node may read, for a probe over its FROM."""
    ctes = _visible_ctes(node)
    if not ctes:
        return ""
    recursive = any(
        isinstance(cte.parent, exp.With) and cte.parent.args.get("recursive") for cte in ctes
    )
    body = ", ".join(cte.sql(dialect=_DIALECT) for cte in ctes)
    return f"WITH {'RECURSIVE ' if recursive else ''}{body} "


def _in_recursive_cte(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.CTE) and isinstance(parent.parent, exp.With):
            if parent.parent.args.get("recursive"):
                return True
        parent = parent.parent
    return False


def _joins_under(select: exp.Select) -> bool:
    """A join in this SELECT or in a CTE or derived table it reads."""
    if select.args.get("joins"):
        return True
    for kind, body in _source_map(select).values():
        if kind == "body" and any(arm.find(exp.Join) for arm in _union_arms(body)):
            return True
    return False


def _sources(tree: exp.Select) -> list[exp.Expression]:
    out: list[exp.Expression] = []
    frm = _from_this(tree)
    if frm is not None:
        out.append(frm)
    for join in tree.args.get("joins") or []:
        if isinstance(join.this, exp.Expression):
            out.append(join.this)
    return out


def _as_of_window(tree: exp.Expression, as_of: str | None) -> str | None:
    """INTERVAL anchored on a day other than the request as_of.

    A clock (CURRENT_DATE / now()) is the bound as_of day, not the host
    date. Missing as_of cannot clear a clocked window.
    """
    as_of_day = _day(as_of)
    for node in tree.walk():
        if not isinstance(node, exp.Interval):
            continue
        parent = node.parent
        while isinstance(parent, exp.Paren):
            parent = parent.parent
        if not isinstance(parent, (exp.Sub, exp.Add)):
            continue
        if _has_clock(parent):
            if not as_of_day:
                return "as_of_window"
            continue
        days = _date_literals(parent)
        if as_of_day and any(day != as_of_day for day in days):
            return "as_of_window"
        if days and not as_of_day:
            return "as_of_window"
    return None


def _direct_many_to_many(tree: exp.Select, ontology: Any | None) -> str | None:
    """Aggregate across a verified many-to-many link joined as two plain tables.

    A table named only inside a subquery is not that join. The ontology
    compiler attaches dimensions that way, and those hops are many-to-one.
    """
    if ontology is None or not any(isinstance(node, exp.AggFunc) for node in tree.walk()):
        return None
    source = _plain_table(_from_this(tree))
    if not source:
        return None
    joined = set()
    for join in tree.args.get("joins") or []:
        other = _plain_table(getattr(join, "this", None))
        if other:
            joined.add(frozenset({source, other}))
    if not joined:
        return None
    links = getattr(ontology, "links", None) or {}
    objects = getattr(ontology, "objects", None) or {}
    for link in links.values():
        if getattr(link, "cardinality", "") != "many_to_many":
            continue
        left = objects.get(getattr(link, "from_object", ""))
        right = objects.get(getattr(link, "to_object", ""))
        if left is None or right is None:
            continue
        left_rel = str(getattr(left, "relation", "") or "").strip().lower()
        right_rel = str(getattr(right, "relation", "") or "").strip().lower()
        if not left_rel.isidentifier() or not right_rel.isidentifier():
            continue
        if frozenset({left_rel, right_rel}) in joined:
            return "fanout_many_to_many"
    return None


def _output_grain(tree: exp.Select) -> list[str]:
    """Non-aggregate output names. Empty when the select is only aggregates."""
    names: list[str] = []
    for expr in tree.expressions or []:
        if _has_agg(expr):
            continue
        name = str(expr.alias_or_name or "").strip()
        if name and name != "*":
            names.append(name)
    return names


def _result_probe(tree: exp.Select, names: list[str]) -> str:
    cols = ", ".join(_quote(name) for name in names)
    return (
        f"SELECT COUNT(*) AS n, COUNT(DISTINCT ({cols})) AS d "
        f"FROM ({tree.sql(dialect=_DIALECT)}) _g"
    )


#: DuckDB aggregates sqlglot leaves as plain function calls.
_ANON_AGGS = frozenset(
    {
        "mode",
        "mean",
        "sem",
        "kurtosis_pop",
        "histogram",
        "histogram_exact",
        "regr_slope",
        "regr_intercept",
        "regr_r2",
        "regr_avgx",
        "regr_avgy",
        "product",
        "entropy",
        "kurtosis",
        "skewness",
        "fsum",
        "sumkahan",
        "kahan_sum",
        "favg",
        "mad",
        "quantile",
        "quantile_disc",
        "approx_quantile",
        "reservoir_quantile",
        "group_concat",
        "listagg",
        "bit_xor",
        "regr_count",
        "regr_sxx",
        "regr_syy",
        "regr_sxy",
    }
)


def _is_agg(node: exp.Expression) -> bool:
    if isinstance(node, (exp.AggFunc, exp.List)):
        return True
    return isinstance(node, exp.Anonymous) and str(node.name).translate(_ASCII_LOWER) in _ANON_AGGS


def _grouping_aggs(tree: exp.Select) -> list[exp.Expression]:
    """Aggregates that collapse rows. A window function (RANK, LAG, SUM OVER)
    keeps every row, so it does not put a body at its own grain."""
    return [node for node in _outer_aggs(tree) if not isinstance(node.parent, exp.Window)]


def _outer_aggs(tree: exp.Select) -> list[exp.Expression]:
    found: list[exp.Expression] = []
    for expr in tree.expressions or []:
        stack: list[exp.Expression] = [expr]
        while stack:
            node = stack.pop()
            if isinstance(node, (exp.Subquery, exp.Select)):
                continue
            if _is_agg(node):
                found.append(node)
                continue
            stack.extend(node.iter_expressions())
    return found


class _Unprobed(Exception):
    """A grain probe that could not run. The answer is not certified."""


#: A probe re-runs the query's own joins; past this it is stopped, unprobed.
_PROBE_TIMEOUT_S = 5.0


def _row_counts(path: Path, probe: str) -> bool:
    """True when the probe's two counts differ. Raises _Unprobed when it cannot run.

    Probes read through the serving cursor (read-only, no ingest lease), so
    they neither wait behind serving reads nor block them, and a probe that
    errors or runs past the bound does not pass.
    """
    import threading

    from dms_executor.demo_warehouse import connect_serving

    try:
        con = connect_serving(path)
    except Exception as exc:  # noqa: BLE001
        raise _Unprobed(type(exc).__name__) from exc
    timer = threading.Timer(_PROBE_TIMEOUT_S, _interrupt, (con,))
    try:
        timer.start()
        row = con.execute(probe).fetchone()
    except Exception as exc:  # noqa: BLE001
        raise _Unprobed(type(exc).__name__) from exc
    finally:
        timer.cancel()
        con.close()
    if not row or row[0] is None or row[1] is None:
        return False
    try:
        return int(row[0]) != int(row[1])
    except (TypeError, ValueError):
        return False


def _interrupt(con: Any) -> None:
    try:
        con.interrupt()
    except Exception:  # noqa: BLE001 - the probe already finished
        pass


def _has_agg(node: exp.Expression) -> bool:
    return any(isinstance(child, exp.AggFunc) for child in node.walk())


def _pre_group_scope(tree: exp.Select) -> str:
    frm = tree.args.get("from")
    if frm is None:
        return ""
    where = tree.args.get("where")
    where_sql = f" {where.sql(dialect=_DIALECT)}" if where is not None else ""
    joins = " ".join(join.sql(dialect=_DIALECT) for join in tree.args.get("joins") or [])
    return f"{frm.sql(dialect=_DIALECT)} {joins}{where_sql}"


def _from_this(select: exp.Select) -> exp.Expression | None:
    frm = select.args.get("from")
    if frm is None:
        return None
    return frm.this


def _plain_table(node: exp.Expression | None) -> str | None:
    if isinstance(node, exp.Table) and node.name:
        return node.name.translate(_ASCII_LOWER)
    return None


def _alias_name(table: exp.Table) -> str:
    alias = table.alias
    if isinstance(alias, str):
        return alias
    return ""


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _has_clock(node: exp.Expression) -> bool:
    for child in node.walk():
        if isinstance(child, (exp.CurrentDate, exp.CurrentTimestamp)):
            return True
        if isinstance(child, exp.Anonymous) and _fold(child.name) in _CLOCK_NAMES:
            return True
    return False


def _date_literals(node: exp.Expression) -> list[str]:
    days: list[str] = []
    for child in node.walk():
        if isinstance(child, exp.Literal) and child.is_string:
            day = _day(str(child.this))
            if day:
                days.append(day)
    return days


def _day(value: str | None) -> str:
    match = _DAY.match(str(value or "").strip())
    return match.group(0) if match else ""


__all__ = ["lake_alias_sql", "served_result_reason"]
