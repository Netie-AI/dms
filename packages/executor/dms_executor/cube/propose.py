"""Draft cubes from approved measures and relationships. Steward review only.

A draft is ``status=proposed``. This module does not approve and does not
serve. When ``relationships`` is present it is the join list (R2-AUTO-ONTO-01
cardinality). Otherwise approved link items are used. An unverified link is
skipped: the cardinality is not guessed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlglot
from sqlglot import exp

from dms_executor.cube.model import (
    TO_MANY,
    Cube,
    CubeDimension,
    CubeJoin,
    CubeMeasure,
    CubeTimeDimension,
)

_TIME_TYPES = frozenset({"date", "timestamp", "datetime", "timestamptz"})
_SAFE_CARD = frozenset({"many_to_one", "one_to_one"})
_AGG = {
    exp.Sum: "sum",
    exp.Avg: "avg",
    exp.Min: "min",
    exp.Max: "max",
}


def draft_cubes(
    items: Sequence[Mapping[str, Any]],
    relationships: Sequence[Mapping[str, Any]] | None = None,
) -> list[Cube]:
    """One proposed cube per grain that has an approved measure."""
    objects = _objects(items)
    measures = _measures(items, objects)
    given = _joins_from_relationships(relationships) if relationships is not None else None
    by_grain: dict[str, list[CubeMeasure]] = {}
    for measure in measures:
        by_grain.setdefault(measure.table, []).append(measure)
    drafts: list[Cube] = []
    for table, grouped in sorted(by_grain.items()):
        obj = _object_for_table(objects, table)
        name = str(obj.get("name") or table) if obj else table
        dims, times = _attributes(obj, table)
        if given is not None:
            cube_joins = [join for join in given if join.from_table == table]
        else:
            cube_joins = _joins_from_links(items, objects, table)
        for other in _object_tables(objects):
            if other == table:
                continue
            extra_dims, extra_times = _attributes(objects.get(other), other)
            if any(join.to_table == other for join in cube_joins):
                dims.extend(extra_dims)
                times.extend(extra_times)
        drafts.append(
            Cube(
                name=name,
                base_table=table,
                measures=grouped,
                dimensions=dims,
                time_dimensions=times,
                joins=cube_joins,
                description=str((obj or {}).get("description") or ""),
                status="proposed",
            )
        )
    return drafts


def _approved(item: Mapping[str, Any]) -> bool:
    status = item.get("status")
    if status is None:
        return True
    return str(status) == "approved"


def _payload(item: Mapping[str, Any]) -> dict[str, Any]:
    raw = item.get("payload")
    if isinstance(raw, dict):
        return raw
    return dict(item)


def _objects(items: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for item in items:
        if not _approved(item) or str(item.get("kind") or "") != "object":
            continue
        body = _payload(item)
        name = str(body.get("name") or "").strip()
        relation = str(body.get("relation") or name).strip()
        if name and relation:
            found[name] = body
            found[relation] = body
    return found


def _object_for_table(objects: dict[str, dict[str, Any]], table: str) -> dict[str, Any] | None:
    return objects.get(table)


def _object_tables(objects: dict[str, dict[str, Any]]) -> list[str]:
    tables: list[str] = []
    for body in objects.values():
        relation = str(body.get("relation") or body.get("name") or "").strip()
        if relation and relation not in tables:
            tables.append(relation)
    return tables


def _measures(
    items: Sequence[Mapping[str, Any]], objects: dict[str, dict[str, Any]]
) -> list[CubeMeasure]:
    out: list[CubeMeasure] = []
    for item in items:
        if not _approved(item) or str(item.get("kind") or "") != "measure":
            continue
        body = _payload(item)
        name = str(body.get("name") or "").strip()
        grain = str(body.get("grain") or "").strip()
        obj = objects.get(grain)
        table = str((obj or {}).get("relation") or grain).strip()
        expression = str(body.get("expression") or body.get("expr") or "").strip()
        if not name or not table or not expression:
            continue
        agg, expr = _split_agg(expression, str(body.get("agg") or ""))
        out.append(
            CubeMeasure(
                name=name,
                expr=expr,
                table=table,
                agg=agg,  # type: ignore[arg-type]
                description=str(body.get("description") or ""),
            )
        )
    return out


def _split_agg(expression: str, declared: str) -> tuple[str, str]:
    named = declared.strip().lower()
    if named in {"sum", "count", "avg", "min", "max", "count_distinct"}:
        return named, _bare(expression)
    try:
        tree = sqlglot.parse_one(expression)
    except sqlglot.errors.SqlglotError:
        return "sum", _bare(expression)
    if isinstance(tree, exp.Count):
        if isinstance(tree.this, exp.Distinct) and tree.this.expressions:
            return "count_distinct", _bare(tree.this.expressions[0].sql())
        inner = tree.this.sql() if tree.this is not None else "*"
        return "count", _bare(inner)
    for kind, label in _AGG.items():
        if isinstance(tree, kind) and tree.this is not None:
            return label, _bare(tree.this.sql())
    return "sum", _bare(expression)


def _bare(sql: str) -> str:
    """Drop table qualifiers. The cube measure names its own table."""
    try:
        tree = sqlglot.parse_one(sql)
    except sqlglot.errors.SqlglotError:
        return sql
    for col in tree.find_all(exp.Column):
        col.set("table", None)
    return tree.sql()


def _attributes(
    obj: dict[str, Any] | None, table: str
) -> tuple[list[CubeDimension], list[CubeTimeDimension]]:
    dims: list[CubeDimension] = []
    times: list[CubeTimeDimension] = []
    if not obj:
        return dims, times
    raw = obj.get("attributes")
    if not isinstance(raw, list):
        return dims, times
    for attr in raw:
        if not isinstance(attr, dict):
            continue
        column = str(attr.get("source_column") or attr.get("name") or "").strip()
        if not column:
            continue
        kind = str(attr.get("type") or attr.get("data_type") or "").strip().lower()
        description = str(attr.get("display_name") or attr.get("description") or "")
        if kind in _TIME_TYPES:
            times.append(
                CubeTimeDimension(name=column, expr=column, table=table, description=description)
            )
        else:
            dims.append(
                CubeDimension(name=column, expr=column, table=table, description=description)
            )
    return dims, times


def _cardinality(raw: object, *, one_to_one: bool) -> str | None:
    text = str(raw or "").strip()
    if text in TO_MANY or text in _SAFE_CARD:
        return text
    if text.lower() == "to_many":
        return "TO_MANY"
    if one_to_one:
        return "one_to_one"
    return None


def _joins_from_relationships(rows: Sequence[Mapping[str, Any]]) -> list[CubeJoin]:
    out: list[CubeJoin] = []
    for row in rows:
        card = _cardinality(row.get("cardinality"), one_to_one=bool(row.get("one_to_one")))
        join = _join_row(row, card)
        if join is not None:
            out.append(join)
    return out


def _joins_from_links(
    items: Sequence[Mapping[str, Any]],
    objects: dict[str, dict[str, Any]],
    base: str,
) -> list[CubeJoin]:
    out: list[CubeJoin] = []
    for item in items:
        if not _approved(item) or str(item.get("kind") or "") != "link":
            continue
        body = _payload(item)
        card = _cardinality(body.get("cardinality"), one_to_one=bool(body.get("one_to_one")))
        if card is None:
            continue
        oriented = _orient_link(body, objects, card, base)
        if oriented is not None:
            out.append(oriented)
    return out


def _columns(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _join_row(row: Mapping[str, Any], card: str | None) -> CubeJoin | None:
    if card is None:
        return None
    left = str(row.get("from_table") or "").strip()
    right = str(row.get("to_table") or "").strip()
    left_cols = _columns(row.get("from_columns"))
    right_cols = _columns(row.get("to_columns"))
    if not left or not right or not left_cols or len(left_cols) != len(right_cols):
        return None
    return CubeJoin(
        from_table=left,
        from_columns=left_cols,
        to_table=right,
        to_columns=right_cols,
        cardinality=card,  # type: ignore[arg-type]
    )


def _relation(objects: dict[str, dict[str, Any]], name: str) -> str:
    body = objects.get(name)
    if not body:
        return name
    return str(body.get("relation") or name)


def _orient_link(
    body: Mapping[str, Any],
    objects: dict[str, dict[str, Any]],
    card: str,
    base: str,
) -> CubeJoin | None:
    """Orient one link from ``base``.

    many_to_one from order_items to orders, with base orders, is TO_MANY
    toward order_items (the compiler pre-aggregates that child). The same
    link with base order_items stays many_to_one toward orders.
    """
    src = _relation(objects, str(body.get("from_object") or ""))
    dst = _relation(objects, str(body.get("to_object") or ""))
    src_cols = _columns(body.get("from_columns"))
    dst_cols = _columns(body.get("to_columns"))
    if not src or not dst or not src_cols or len(src_cols) != len(dst_cols):
        return None
    if card == "many_to_one" and base == dst:
        return CubeJoin(
            from_table=dst,
            from_columns=dst_cols,
            to_table=src,
            to_columns=src_cols,
            cardinality="TO_MANY",
        )
    if base != src:
        return None
    kept = "many_to_one" if card == "many_to_one" else card
    return CubeJoin(
        from_table=src,
        from_columns=src_cols,
        to_table=dst,
        to_columns=dst_cols,
        cardinality=kept,  # type: ignore[arg-type]
    )
