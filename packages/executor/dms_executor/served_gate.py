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
or probe error does not flag. ``served_check_shadow`` stays the recorder.
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
    changed = False
    renamed: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.catalog or str(table.db or "main").lower() != "main":
            continue
        if not name.startswith(_ALIAS_PREFIX) or name in _LAKE:
            continue
        lake = name[len(_ALIAS_PREFIX) :]
        if lake in _LAKE:
            table.set("this", exp.to_identifier(lake))
            changed = True
            if not table.alias:
                renamed[name] = lake
    # warehouse_inventory.sku (or main.warehouse_inventory.sku) qualifies by
    # the table name, which is renamed too.
    for column in tree.find_all(exp.Column):
        bare = renamed.get(str(column.table or "").lower())
        if bare is None or column.args.get("catalog"):
            continue
        if str(column.args.get("db") or "main").lower() == "main":
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
    qualified = _qualified(tree, path)
    # A mergeable CTE or derived table is checked as the outer query's own
    # join once sqlglot inlines it (a filtered fact CTE counted per dimension
    # is then the fact table, at its own grain). What cannot be inlined stays
    # in the tree and is checked where it stands.
    merged = _merged(qualified)
    if merged is None:
        return _fanout(qualified, path)
    if isinstance(merged, exp.Select):
        many = _direct_many_to_many(merged, ontology)
        if many:
            return many
    return _fanout(merged, path)


def _lake_schema(path: Path) -> dict[str, dict[str, str]]:
    from dms_executor.demo_warehouse import connect_file

    con = connect_file(path)
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main'"
        ).fetchall()
    finally:
        con.close()
    schema: dict[str, dict[str, str]] = {}
    for table, column, kind in rows:
        schema.setdefault(str(table), {})[str(column)] = str(kind)
    return schema


def _qualified(tree: exp.Query, path: Path) -> exp.Query:
    """Columns qualified against the lake's own catalog, or the tree as it is."""
    try:
        from sqlglot.optimizer.qualify import qualify

        out = qualify(
            tree.copy(),
            schema=_lake_schema(path),
            dialect=_DIALECT,
            validate_qualify_columns=False,
            quote_identifiers=False,
        )
    except Exception:  # noqa: BLE001 - unqualified columns resolve by single source below
        return tree.copy()
    return out if isinstance(out, exp.Query) else tree.copy()


def _merged(tree: exp.Query) -> exp.Query | None:
    """The query with its mergeable CTEs and derived tables inlined, or None."""
    if not any(
        isinstance(node, (exp.With, exp.Subquery)) for node in tree.find_all(exp.With, exp.Subquery)
    ):
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
    CTE bodies, derived tables, subqueries and UNION arms. A query that does
    not aggregate is checked on the rows it returns.
    """
    for select in tree.find_all(exp.Select):
        if _in_recursive_cte(select):
            continue
        if _select_fanout(select, path):
            return "fanout_subject_keys"
    if isinstance(tree, exp.Select) and not _outer_aggs(tree):
        names = _output_grain(tree)
        if names and _joins_under(tree) and _row_counts(path, _result_probe(tree, names)):
            return "fanout_subject_keys"
    return None


def _select_fanout(select: exp.Select, path: Path) -> bool:
    """True when an aggregate here adds up rows its joins repeated.

    A joined row set is at the grain of a base table when that table's rowid
    appears once per group. SUM, AVG and COUNT of an expression are inflated
    when every base table the expression reads repeats within a group
    (revenue as t.qty * i.price over transactions joined to inventory is per
    transaction: t stays unique). COUNT(*) is inflated when no joined base
    table stays unique. Rows an outer join leaves unmatched carry no rowid
    and are not counted against it.
    """
    targets = [
        node
        for node in _outer_aggs(select)
        if isinstance(node, (exp.Sum, exp.Avg, exp.Count)) and not node.args.get("distinct")
    ]
    if not targets:
        return False
    sources = _source_map(select)
    scope = _pre_group_scope(select)
    if not sources or not scope:
        return False
    prefix = _visible_with(select)
    groups = _group_sql(select)
    joined = bool(select.args.get("joins"))
    base = [alias for alias, (kind, _) in sources.items() if kind == "base"]
    cache: dict[str, bool] = {}

    def repeats(alias: str) -> bool:
        if alias not in cache:
            cache[alias] = _repeats(path, prefix, scope, alias, groups)
        return cache[alias]

    for agg in targets:
        arg = agg.this
        if isinstance(agg, exp.Count) and (
            arg is None or isinstance(arg, exp.Star) or not list(arg.find_all(exp.Column))
        ):
            # A count of joined rows counts pairs when a join links nothing:
            # inventory JOIN locations ON TRUE "per sku" counts locations.
            if joined and (_cartesian(select, sources) or (base and all(map(repeats, base)))):
                return True
            continue
        verdicts: list[bool] = []
        for alias, names in _arg_sources(arg, sources).items():
            kind, body = sources[alias]
            if kind == "base":
                if joined:
                    verdicts.append(repeats(alias))
            else:
                traced = _body_repeats(path, body, names, 0)
                if traced is not None:
                    verdicts.append(traced)
        if verdicts and all(verdicts):
            return True
    return False


def _cartesian(select: exp.Select, sources: dict[str, tuple[str, Any]]) -> bool:
    """A join to a table or CTE that no condition links to another source.

    ON TRUE, CROSS JOIN, or a comma join with no WHERE comparison between the
    two. Generators, unnest and LATERAL are not counted: a spine is joined
    that way on purpose.
    """
    where = select.args.get("where")
    for join in select.args.get("joins") or []:
        node = join.this
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
            alias = (_alias_name(node) or node.name).lower()
        elif isinstance(node, exp.Subquery) and node.alias:
            alias = node.alias.lower()
        else:
            continue
        if alias not in sources or join.args.get("using"):
            continue
        conditions = [c for c in (join.args.get("on"), where) if c is not None]
        if not any(_links(cond, alias, set(sources)) for cond in conditions):
            return True
    return False


def _links(condition: exp.Expression, alias: str, aliases: set[str]) -> bool:
    """A comparison in this condition reads ``alias`` and another source."""
    for node in condition.find_all(exp.Binary):
        if isinstance(node, (exp.And, exp.Or)):
            continue
        quals = {str(c.table or "").lower() for c in node.find_all(exp.Column)}
        if "" in quals and len(list(node.find_all(exp.Column))) > 1:
            return True  # unqualified columns: cannot tell, do not refuse
        if alias in quals and (quals - {alias}) & aliases:
            return True
    return False


def _arg_sources(
    arg: exp.Expression, sources: dict[str, tuple[str, Any]]
) -> dict[str, set[str]]:
    """Source alias -> column names an aggregate's argument reads from it."""
    out: dict[str, set[str]] = {}
    for column in arg.find_all(exp.Column):
        qual = str(column.table or "").lower()
        if not qual and len(sources) == 1:
            qual = next(iter(sources))
        if qual in sources:
            out.setdefault(qual, set()).add(column.name.lower())
    return out


def _body_repeats(path: Path, body: Any, names: set[str], depth: int) -> bool | None:
    """Whether a CTE or derived table repeats the rows behind these columns.

    None when it cannot be told. An aggregating arm is at its own grain (and
    is checked as a SELECT in its own right).
    """
    if depth > 3:
        return None
    arms = _union_arms(body)
    if not arms:
        return None
    result: bool | None = None
    for arm in arms:
        if _outer_aggs(arm) or arm.args.get("group") is not None:
            result = bool(result)
            continue
        sources = _source_map(arm)
        scope = _pre_group_scope(arm)
        if not sources or not scope:
            continue
        prefix = _visible_with(arm)
        for name in names:
            projection = next(
                (p for p in arm.expressions if p.alias_or_name.lower() == name), None
            )
            if projection is None:
                continue
            verdicts: list[bool] = []
            for alias, inner in _arg_sources(projection, sources).items():
                kind, inner_body = sources[alias]
                if kind == "base":
                    if arm.args.get("joins"):
                        verdicts.append(_repeats(path, prefix, scope, alias, []))
                    else:
                        verdicts.append(False)
                else:
                    traced = _body_repeats(path, inner_body, inner, depth + 1)
                    if traced is not None:
                        verdicts.append(traced)
            if verdicts:
                if all(verdicts):
                    return True
                result = bool(result)
    return result


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
    ctes = {str(cte.alias).lower(): cte.this for cte in _visible_ctes(select)}
    out: dict[str, tuple[str, Any]] = {}
    for node in _sources(select):
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
            alias = (_alias_name(node) or node.name).lower()
            name = node.name.lower()
            if not (node.db or node.catalog) and name in ctes:
                out[alias] = ("body", ctes[name])
            else:
                out[alias] = ("base", node)
        elif isinstance(node, exp.Subquery) and node.alias:
            out[node.alias.lower()] = ("body", node.this)
    return out


def _repeats(path: Path, prefix: str, scope: str, alias: str, groups: list[str]) -> bool:
    """True when this table's rows appear more than once within a group."""
    rid = f"{exp.to_identifier(alias).sql(dialect=_DIALECT)}.rowid"
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
    for key in ("rollup", "cube", "grouping_sets"):
        for node in group.args.get(key) or []:
            items.extend(node.expressions if hasattr(node, "expressions") else [node])
    out: list[str] = []
    for item in items:
        if isinstance(item, exp.Literal) and item.is_int:
            index = int(item.name) - 1
            if 0 <= index < len(projections):
                item = projections[index].unalias()
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
            name = str(cte.alias).lower()
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


def _outer_aggs(tree: exp.Select) -> list[exp.Expression]:
    found: list[exp.Expression] = []
    for expr in tree.expressions or []:
        stack: list[exp.Expression] = [expr]
        while stack:
            node = stack.pop()
            if isinstance(node, exp.Subquery):
                continue
            if isinstance(node, exp.AggFunc):
                found.append(node)
                continue
            stack.extend(node.iter_expressions())
    return found


def _row_counts(path: Path, probe: str) -> bool:
    """True when the probe row count and the distinct grain differ."""
    try:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(path)
        try:
            row = con.execute(probe).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - a probe error is not a fan-out
        return False
    if not row or row[0] is None or row[1] is None:
        return False
    try:
        return int(row[0]) != int(row[1])
    except (TypeError, ValueError):
        return False


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
        return node.name.lower()
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
        if isinstance(child, exp.Anonymous) and str(child.name or "").lower() in _CLOCK_NAMES:
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
