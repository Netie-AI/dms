"""Served-result check. Runs before a confident badge on generated SQL.

Fails:

- Fan-out: joined rows are not distinct on the subject's base key
  (COUNT(*) vs COUNT(DISTINCT key), before GROUP BY), or an aggregate
  crosses a verified many-to-many link that is a direct join of two plain
  tables. An extra result column is not a fail. Duplicated subject keys
  are the wrong grain.
- As-of window: an INTERVAL whose anchor day is not the request as_of.
  A clock anchor on the same calendar day is the same window.

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
    window = _as_of_window(tree, as_of, warehouse)
    if window:
        return window
    many = _direct_many_to_many(tree, ontology)
    if many:
        return many
    return _distinct_key_probe(tree, warehouse, ontology)


def _as_of_window(
    tree: exp.Select,
    as_of: str | None,
    warehouse: Path | str | None,
) -> str | None:
    """INTERVAL anchored on a day other than the request as_of."""
    as_of_day = _day(as_of)
    clock_day = ""
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
                continue
            if not clock_day:
                clock_day = _clock_day(warehouse)
            if clock_day and as_of_day != clock_day:
                return "as_of_window"
            continue
        days = _date_literals(parent)
        if as_of_day and any(day != as_of_day for day in days):
            return "as_of_window"
    return None


def _clock_day(warehouse: Path | str | None) -> str:
    path = Path(warehouse) if warehouse is not None else None
    if path is not None and path.is_file():
        try:
            from dms_executor.demo_warehouse import connect_file

            con = connect_file(path)
            try:
                row = con.execute("SELECT CAST(CURRENT_DATE AS VARCHAR)").fetchone()
            finally:
                con.close()
            if row and row[0]:
                return str(row[0])[:10]
        except Exception:  # noqa: BLE001 - no clock, no mismatch
            return ""
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d")


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
    """COUNT(*) vs COUNT(DISTINCT subject key) on the joined rows, before GROUP BY."""
    if not (tree.args.get("joins") or []):
        return None
    source = _from_this(tree)
    table = _plain_table(source)
    if not table or not isinstance(source, exp.Table):
        return None
    key = _relation_key(ontology, table)
    if not key:
        return None
    path = Path(warehouse) if warehouse is not None else None
    if path is None or not path.is_file():
        return None
    qual = _alias_name(source) or table
    cols = ", ".join(f"{_quote(qual)}.{_quote(col)}" for col in sorted(key))
    where = tree.args.get("where")
    where_sql = f" {where.sql(dialect=_DIALECT)}" if where is not None else ""
    joins = " ".join(join.sql(dialect=_DIALECT) for join in tree.args.get("joins") or [])
    probe = (
        f"SELECT COUNT(*) AS n, COUNT(DISTINCT ({cols})) AS d "
        f"{tree.args['from'].sql(dialect=_DIALECT)} {joins}{where_sql}"
    )
    try:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(path)
        try:
            row = con.execute(probe).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - a probe error is not a fan-out
        return None
    if not row or row[0] is None or row[1] is None:
        return None
    try:
        n, distinct = int(row[0]), int(row[1])
    except (TypeError, ValueError):
        return None
    if n != distinct:
        return "fanout_subject_keys"
    return None


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
