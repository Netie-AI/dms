"""Served-result check. Runs before a confident badge on generated SQL.

Fails:

- Fan-out: served rows are not distinct on the grain the query aggregates
  to (parsed GROUP BY, else the non-aggregate output keys), or a SUM/AVG
  repeats a column inside that grain. A direct many-to-many join of two
  plain tables is the same fail. An extra result column is not a fail.
- As-of window: an INTERVAL whose anchor day is not the request as_of.
  A clock anchor means that bound day, on both sides.

Grants are not decided here. No question words and no case ids. A parse
or probe error does not flag. ``served_check_shadow`` stays the recorder.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

_DIALECT = "duckdb"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CLOCK_NAMES = frozenset({"now", "getdate", "current_date", "current_timestamp"})


def served_result_reason(
    sql: str,
    *,
    warehouse: Path | str | None = None,
    as_of: str | None = None,
    ontology: Any | None = None,
) -> str | None:
    """Reason code, or None when this SQL may take a badge."""
    try:
        tree = parse_one(sql or "", read=_DIALECT)
    except Exception:  # noqa: BLE001 - unparseable SQL is not evidence
        return None
    if not isinstance(tree, exp.Select):
        return None
    window = _as_of_window(tree, as_of)
    if window:
        return window
    many = _direct_many_to_many(tree, ontology)
    if many:
        return many
    return _distinct_key_probe(tree, warehouse, ontology)


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
    """COUNT that repeats the widest joined key. A one-to-many count does not.

    Lines per supplier stay one row per inventory key. A cross join repeats
    that key, so the count is not the grain it names.
    """
    counts = [
        node for node in _outer_aggs(tree)
        if isinstance(node, exp.Count) and not node.args.get("distinct")
    ]
    if not counts:
        return None
    qual, key = _widest_qualified(tree, ontology)
    if not qual or not key:
        return None
    cols = ", ".join(f"{_quote(qual)}.{_quote(name)}" for name in sorted(key))
    scope = _pre_group_scope(tree)
    if not scope:
        return None
    probe = f"SELECT COUNT(*) AS n, COUNT(DISTINCT ({cols})) AS d {scope}"
    if _row_counts(path, probe):
        return "fanout_subject_keys"
    return None


def _widest_qualified(tree: exp.Select, ontology: Any | None) -> tuple[str, set[str]]:
    best: tuple[str, set[str]] | None = None
    seen: set[str] = set()

    def consider(node: exp.Expression | None) -> None:
        nonlocal best
        if not isinstance(node, exp.Table) or not node.name:
            return
        table = node.name.lower()
        qual = _alias_name(node).lower() or table
        marker = f"{qual}:{table}"
        if marker in seen:
            return
        seen.add(marker)
        key = _relation_key(ontology, table)
        if not key:
            return
        if best is None or len(key) > len(best[1]):
            best = (qual, key)

    consider(_from_this(tree))
    for join in tree.args.get("joins") or []:
        consider(getattr(join, "this", None))
    return best or ("", set())


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
        parts: list[str] = []
        for piece in (*groups, *(f"{_quote(qual)}.{_quote(name)}" for name in sorted(key))):
            if piece not in parts:
                parts.append(piece)
        scope = _pre_group_scope(tree)
        if not scope or not parts:
            continue
        probe = (
            f"SELECT COUNT(*) AS n, COUNT(DISTINCT ({', '.join(parts)})) AS d {scope}"
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
    matches = [
        obj
        for obj in objects.values()
        if str(getattr(obj, "relation", "") or "").strip().lower() == relation
    ]
    if not matches:
        return None
    widest = max(matches, key=lambda obj: len(tuple(getattr(obj, "key", ()) or ())))
    key = {str(col).lower() for col in (getattr(widest, "key", ()) or ()) if str(col).strip()}
    return key or None


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


__all__ = ["served_result_reason"]
