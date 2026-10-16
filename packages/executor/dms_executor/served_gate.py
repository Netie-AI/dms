"""Served-result check. Runs before a confident badge on generated SQL.

Fails:

- Fan-out: served rows are not distinct on the grain the query aggregates
  to (parsed GROUP BY, else the non-aggregate output keys), or a SUM/AVG
  repeats a column inside that grain. A direct many-to-many join of two
  plain tables is the same fail. An extra result column is not a fail.
- As-of window: an INTERVAL whose anchor day is not the request as_of.
  A clock anchor means that bound day, on both sides.

- Both run on the query as written and on the query with its mergeable
  CTEs and derived tables inlined, so a join inside a CTE is checked.

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
    # warehouse_inventory.sku qualifies by the table name, which is renamed too.
    for column in tree.find_all(exp.Column):
        bare = renamed.get(str(column.table or "").lower())
        if bare is not None and not column.args.get("db"):
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
    takes a badge (map_ask_response_to_envelope).
    """
    try:
        tree = parse_one(sql or "", read=_DIALECT)
    except Exception:  # noqa: BLE001 - unparseable SQL is not evidence
        return None
    if not isinstance(tree, exp.Select):
        return None
    _unalias(tree)
    window = _as_of_window(tree, as_of)
    if window:
        return window
    many = _direct_many_to_many(tree, ontology)
    if many:
        return many
    probed = _distinct_key_probe(tree, warehouse, ontology)
    if probed:
        return probed
    # A join inside a CTE or derived table fans out the rows the outer query
    # reads. sqlglot's optimizer inlines what it can (the CTE disappears and
    # its join becomes the outer query's), and the same checks run on that.
    merged = _merged(tree, warehouse)
    if merged is None:
        return None
    return _direct_many_to_many(merged, ontology) or _distinct_key_probe(
        merged, warehouse, ontology
    )


def _merged(tree: exp.Select, warehouse: Path | str | None) -> exp.Select | None:
    """The query with its mergeable CTEs and derived tables inlined, or None.

    None when there is nothing to inline or the optimizer cannot qualify it.
    Columns are qualified against the lake's own catalog.
    """
    if tree.args.get("with") is None and not any(
        isinstance(src, exp.Subquery) for src in _sources(tree)
    ):
        return None
    path = Path(warehouse) if warehouse is not None else None
    if path is None or not path.is_file():
        return None
    try:
        from sqlglot.optimizer.eliminate_subqueries import eliminate_subqueries
        from sqlglot.optimizer.merge_subqueries import merge_subqueries
        from sqlglot.optimizer.qualify import qualify

        from dms_executor.demo_warehouse import connect_file

        con = connect_file(path)
        try:
            rows = con.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns"
            ).fetchall()
        finally:
            con.close()
        schema: dict[str, dict[str, str]] = {}
        for table, column, kind in rows:
            schema.setdefault(str(table), {})[str(column)] = str(kind)
        qualified = qualify(
            tree.copy(),
            schema=schema,
            dialect=_DIALECT,
            validate_qualify_columns=False,
            quote_identifiers=False,
        )
        merged = merge_subqueries(eliminate_subqueries(qualified))
    except Exception:  # noqa: BLE001 - nothing inlined is not evidence either way
        return None
    if not isinstance(merged, exp.Select):
        return None
    if merged.sql(dialect=_DIALECT) == tree.sql(dialect=_DIALECT):
        return None
    return merged


def _sources(tree: exp.Select) -> list[exp.Expression]:
    out: list[exp.Expression] = []
    frm = _from_this(tree)
    if frm is not None:
        out.append(frm)
    for join in tree.args.get("joins") or []:
        if isinstance(join.this, exp.Expression):
            out.append(join.this)
    return out


def _as_of_window(tree: exp.Select, as_of: str | None) -> str | None:
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


def _distinct_key_probe(
    tree: exp.Select,
    warehouse: Path | str | None,
    ontology: Any | None,
) -> str | None:
    """Served grain, then a SUM/AVG that repeats inside that grain.

    The grain is the parsed GROUP BY when the query aggregates, otherwise
    the non-aggregate output keys. Comparing to the FROM table's key flags
    a correct one-to-many count.
    """
    if not (tree.args.get("joins") or []):
        return None
    path = Path(warehouse) if warehouse is not None else None
    if path is None or not path.is_file():
        return None
    names = _output_grain(tree)
    if names and _row_counts(path, _result_probe(tree, names)):
        return "fanout_subject_keys"
    counted = _fanned_count(tree, path, ontology)
    if counted:
        return counted
    return _fanned_measure(tree, path, ontology)


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


def _fanned_count(
    tree: exp.Select,
    path: Path,
    ontology: Any | None,
) -> str | None:
    """COUNT over joined rows that are at no joined table's grain.

    A one-to-many join keeps the many side's key unique: lines per supplier
    are one row per inventory key, transactions per category one row per
    transaction. A cross join repeats every joined key, so the count is not
    the grain of any table it names.
    """
    counts = [
        node for node in _outer_aggs(tree)
        if isinstance(node, exp.Count) and not node.args.get("distinct")
    ]
    if not counts:
        return None
    keyed = _qualified_keys(tree, ontology)
    scope = _pre_group_scope(tree)
    if not keyed or not scope:
        return None
    for qual, key in keyed:
        names = [f"{_quote(qual)}.{_quote(name)}" for name in sorted(key)]
        probe = (
            f"{_with_prefix(tree)}SELECT {_keyed_rows(names)} AS n, "
            f"COUNT(DISTINCT ({', '.join(names)})) AS d {scope}"
        )
        if not _row_counts(path, probe):
            # The rows are this table's rows; the count is its count.
            return None
    return "fanout_subject_keys"


def _qualified_keys(tree: exp.Select, ontology: Any | None) -> list[tuple[str, set[str]]]:
    """(qualifier, key) for each FROM/JOIN table whose key the ontology knows."""
    out: list[tuple[str, set[str]]] = []
    seen: set[str] = set()
    for node in _sources(tree):
        if not isinstance(node, exp.Table) or not node.name:
            continue
        table = node.name.lower()
        qual = _alias_name(node).lower() or table
        if qual in seen:
            continue
        seen.add(qual)
        key = _relation_key(ontology, table)
        if key:
            out.append((qual, key))
    return out


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


def _fanned_measure(
    tree: exp.Select,
    path: Path,
    ontology: Any | None,
) -> str | None:
    """SUM/AVG of a column that is not unique inside the aggregate grain."""
    aliases = _table_aliases(tree)
    groups = _group_column_sql(tree)
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for col in _sum_avg_columns(tree):
        table, qual = _resolve_table(col, aliases)
        if not table or not qual:
            continue
        key = _relation_key(ontology, table)
        if not key:
            continue
        sig = (qual, tuple(sorted(key)))
        if sig in seen:
            continue
        seen.add(sig)
        keys = [f"{_quote(qual)}.{_quote(name)}" for name in sorted(key)]
        parts: list[str] = []
        for piece in (*groups, *keys):
            if piece not in parts:
                parts.append(piece)
        scope = _pre_group_scope(tree)
        if not scope or not parts:
            continue
        probe = (
            f"{_with_prefix(tree)}"
            f"SELECT {_keyed_rows(keys)} AS n, COUNT(DISTINCT ({', '.join(parts)})) AS d {scope}"
        )
        if _row_counts(path, probe):
            return "fanout_subject_keys"
    return None


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


def _sum_avg_columns(tree: exp.Select) -> list[exp.Column]:
    found: list[exp.Column] = []
    for expr in tree.expressions or []:
        stack: list[exp.Expression] = [expr]
        while stack:
            node = stack.pop()
            if isinstance(node, exp.Subquery):
                continue
            if isinstance(node, (exp.Sum, exp.Avg)) and not node.args.get("distinct"):
                found.extend(
                    col for col in node.find_all(exp.Column) if isinstance(col, exp.Column)
                )
                continue
            stack.extend(node.iter_expressions())
    return found


def _has_agg(node: exp.Expression) -> bool:
    return any(isinstance(child, exp.AggFunc) for child in node.walk())


def _group_column_sql(tree: exp.Select) -> list[str]:
    group = tree.args.get("group")
    if group is None:
        return []
    out: list[str] = []
    for expr in group.expressions or []:
        if isinstance(expr, exp.Column):
            out.append(expr.sql(dialect=_DIALECT))
    return out


def _pre_group_scope(tree: exp.Select) -> str:
    frm = tree.args.get("from")
    if frm is None:
        return ""
    where = tree.args.get("where")
    where_sql = f" {where.sql(dialect=_DIALECT)}" if where is not None else ""
    joins = " ".join(join.sql(dialect=_DIALECT) for join in tree.args.get("joins") or [])
    return f"{frm.sql(dialect=_DIALECT)} {joins}{where_sql}"


def _keyed_rows(keys: list[str]) -> str:
    """Rows that carry this table's key. An outer join's unmatched rows carry none."""
    present = " AND ".join(f"{key} IS NOT NULL" for key in keys)
    return f"COUNT(*) FILTER (WHERE {present})"


def _with_prefix(tree: exp.Select) -> str:
    """The query's WITH clause, so a probe over its FROM can name its CTEs."""
    holder = tree.args.get("with")
    return f"{holder.sql(dialect=_DIALECT)} " if holder is not None else ""


def _table_aliases(tree: exp.Select) -> dict[str, str]:
    out: dict[str, str] = {}

    def add(node: exp.Expression | None) -> None:
        if not isinstance(node, exp.Table) or not node.name:
            return
        table = node.name.lower()
        alias = _alias_name(node).lower() or table
        out[alias] = table
        out[table] = table

    add(_from_this(tree))
    for join in tree.args.get("joins") or []:
        add(getattr(join, "this", None))
    return out


def _resolve_table(col: exp.Column, aliases: dict[str, str]) -> tuple[str, str]:
    qual = str(col.table or "").lower()
    if qual and qual in aliases:
        return aliases[qual], qual
    if not qual and len(set(aliases.values())) == 1:
        table = next(iter(set(aliases.values())))
        return table, table
    return "", ""


def _relation_key(ontology: Any | None, relation: str) -> set[str] | None:
    objects = getattr(ontology, "objects", None) or {}
    matches = [obj for obj in objects.values() if _object_table(obj) == relation]
    if not matches:
        return None
    widest = max(matches, key=lambda obj: len(tuple(getattr(obj, "key", ()) or ())))
    key = {str(col).lower() for col in (getattr(widest, "key", ()) or ()) if str(col).strip()}
    return key or None


def _object_table(obj: Any) -> str:
    """The table an ontology object's rows are, or "".

    A plain relation name, or a subquery that only projects one table's rows
    (``SELECT *, CAST(ts AS DATE) AS day FROM transactions``): its key is that
    table's. A subquery that filters, groups, dedupes or joins is not.
    """
    relation = str(getattr(obj, "relation", "") or "").strip()
    if relation.replace("_", "").isalnum():
        return relation.lower()
    try:
        tree = parse_one(relation, read=_DIALECT)
    except Exception:  # noqa: BLE001 - not a relation this check can read
        return ""
    while isinstance(tree, exp.Subquery):
        tree = tree.this
    if not isinstance(tree, exp.Select):
        return ""
    if any(
        tree.args.get(arg)
        for arg in ("joins", "where", "group", "having", "distinct", "limit", "with")
    ) or any(isinstance(node, exp.AggFunc) for node in tree.walk()):
        return ""
    return _plain_table(_from_this(tree)) or ""


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
