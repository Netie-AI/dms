"""Compile a cube plan to dialect SQL with sqlglot.

Measure expressions and join paths come only from the approved cube.
A TO_MANY child is aggregated at its join key before it is joined, so a
measure on the base is not repeated once per child row.

ponytail: one hop from the cube base. A member whose table is not the base
and not a direct join is ``cube_member_unknown``. Upgrade: walk the join
graph when a cube needs two hops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from dms_executor.cube.model import (
    GRAINS,
    TO_MANY,
    Cube,
    CubeDimension,
    CubeJoin,
    CubeMeasure,
    CubePlan,
    CubeTimeDimension,
)

REASON_UNKNOWN = "cube_member_unknown"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_AGGS = frozenset({"sum", "count", "avg", "min", "max", "count_distinct"})
_OPS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "in"})
_SAFE = (
    exp.Column,
    exp.Identifier,
    exp.Literal,
    exp.Neg,
    exp.Paren,
    exp.Add,
    exp.Sub,
    exp.Mul,
    exp.Div,
    exp.Star,
    exp.Boolean,
    exp.Null,
)


class CubePlanError(Exception):
    """The plan is not served. ``reason`` is a code, never a cell value."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class _Piece:
    name: str
    expr: exp.Expression
    table: str
    kind: str  # dimension, time, measure


def _dialect(dialect: str) -> str:
    name = (dialect or "").strip().lower() or "duckdb"
    if name not in {"postgres", "mysql", "tsql", "sqlite", "duckdb"}:
        raise CubePlanError("cube_dialect_unknown")
    return name


def _ident(name: str) -> str:
    text = str(name or "").strip()
    if not _IDENT.match(text):
        raise CubePlanError(REASON_UNKNOWN)
    return text


def _to_many(join: CubeJoin) -> bool:
    return str(join.cardinality or "").strip() in TO_MANY


def _index_named(items: list[CubeMeasure] | list[CubeDimension] | list[CubeTimeDimension]):
    found: dict[str, CubeMeasure | CubeDimension | CubeTimeDimension] = {}
    for item in items:
        key = _ident(item.name)
        if key in found:
            raise CubePlanError("cube_member_duplicate")
        found[key] = item
    return found


def _join_to(cube: Cube, table: str) -> CubeJoin | None:
    if table == cube.base_table:
        return None
    direct = [
        join for join in cube.joins if join.from_table == cube.base_table and join.to_table == table
    ]
    if len(direct) > 1:
        raise CubePlanError("cube_join_ambiguous")
    if len(direct) == 1:
        join = direct[0]
        if str(join.cardinality) == "unverified":
            raise CubePlanError(REASON_UNKNOWN)
        return join
    raise CubePlanError(REASON_UNKNOWN)


def _safe_tree(node: exp.Expression) -> bool:
    return all(isinstance(item, _SAFE) for item in node.walk())


def _qualify(node: exp.Expression, table: str) -> exp.Expression:
    copied = node.copy()
    for col in copied.find_all(exp.Column):
        if not col.table:
            col.set("table", exp.to_identifier(table))
    return copied


def _parse_expr(text: str, table: str, dialect: str) -> exp.Expression:
    raw = str(text or "").strip()
    if raw == "*":
        return exp.Star()
    try:
        tree = sqlglot.parse_one(raw, read=dialect)
    except sqlglot.errors.SqlglotError as exc:
        raise CubePlanError("cube_expr_rejected") from exc
    if not isinstance(tree, exp.Expression) or not _safe_tree(tree):
        raise CubePlanError("cube_expr_rejected")
    return _qualify(tree, table)


def _agg(kind: str, inner: exp.Expression) -> exp.Expression:
    if kind == "sum":
        return exp.Sum(this=inner)
    if kind == "avg":
        return exp.Avg(this=inner)
    if kind == "min":
        return exp.Min(this=inner)
    if kind == "max":
        return exp.Max(this=inner)
    if kind == "count_distinct":
        return exp.Count(this=exp.Distinct(expressions=[inner]))
    if kind == "count":
        return exp.Count(this=inner)
    raise CubePlanError(REASON_UNKNOWN)


def _literal(value: object) -> exp.Expression:
    if type(value) is bool:
        return exp.Boolean(this=value)
    if type(value) is int:
        return exp.Literal.number(value)
    if type(value) is float:
        return exp.Literal.number(value)
    if isinstance(value, str):
        return exp.Literal.string(value)
    raise CubePlanError("cube_filter_invalid")


def _compare(op: str, left: exp.Expression, value: object) -> exp.Expression:
    if op == "in":
        if not isinstance(value, list) or not value:
            raise CubePlanError("cube_filter_invalid")
        return exp.In(this=left, expressions=[_literal(item) for item in value])
    made = _literal(value)
    if op == "eq":
        return exp.EQ(this=left, expression=made)
    if op == "neq":
        return exp.NEQ(this=left, expression=made)
    if op == "gt":
        return exp.GT(this=left, expression=made)
    if op == "gte":
        return exp.GTE(this=left, expression=made)
    if op == "lt":
        return exp.LT(this=left, expression=made)
    if op == "lte":
        return exp.LTE(this=left, expression=made)
    raise CubePlanError(REASON_UNKNOWN)


def _on(join: CubeJoin, right_table: str) -> exp.Expression:
    if len(join.from_columns) != len(join.to_columns) or not join.from_columns:
        raise CubePlanError(REASON_UNKNOWN)
    parts: list[exp.Expression] = []
    for left, right in zip(join.from_columns, join.to_columns, strict=True):
        parts.append(
            exp.EQ(
                this=exp.column(_ident(left), table=_ident(join.from_table)),
                expression=exp.column(_ident(right), table=right_table),
            )
        )
    return parts[0] if len(parts) == 1 else exp.and_(*parts)


def _piece_expr(piece: _Piece, dialect: str, grain: str | None) -> exp.Expression:
    if piece.kind == "measure":
        raise CubePlanError("cube_expr_rejected")
    expr = piece.expr.copy()
    if piece.kind == "time" and grain:
        return exp.DateTrunc(this=expr, unit=exp.Literal.string(grain))
    return expr


def compile_plan(plan: CubePlan, cube: Cube, *, dialect: str) -> str:
    """SQL for ``plan`` against ``cube``. Raises ``CubePlanError`` to refuse."""
    read = _dialect(dialect)
    if cube.status != "approved":
        raise CubePlanError(REASON_UNKNOWN)
    base = _ident(cube.base_table)
    measures = _index_named(list(cube.measures))
    dimensions = _index_named(list(cube.dimensions))
    times = _index_named(list(cube.time_dimensions))
    if not plan.measures:
        raise CubePlanError("cube_plan_incomplete")

    grain = None
    if plan.time_dimension is not None:
        grain = str(plan.time_dimension.grain or "").strip().lower()
        if grain not in GRAINS:
            raise CubePlanError(REASON_UNKNOWN)

    pieces: list[_Piece] = []
    for name in plan.dimensions:
        item = dimensions.get(_ident(name))
        if not isinstance(item, CubeDimension):
            raise CubePlanError(REASON_UNKNOWN)
        pieces.append(
            _Piece(
                item.name,
                _parse_expr(item.expr, _ident(item.table), read),
                _ident(item.table),
                "dimension",
            )
        )
    if plan.time_dimension is not None:
        item = times.get(_ident(plan.time_dimension.name))
        if not isinstance(item, CubeTimeDimension):
            raise CubePlanError(REASON_UNKNOWN)
        pieces.append(
            _Piece(
                item.name,
                _parse_expr(item.expr, _ident(item.table), read),
                _ident(item.table),
                "time",
            )
        )
    measure_pieces: list[tuple[CubeMeasure, exp.Expression]] = []
    for name in plan.measures:
        item = measures.get(_ident(name))
        if not isinstance(item, CubeMeasure) or item.agg not in _AGGS:
            raise CubePlanError(REASON_UNKNOWN)
        table = _ident(item.table)
        inner = _parse_expr(item.expr, table, read)
        if item.agg != "count" and isinstance(inner, exp.Star):
            raise CubePlanError("cube_expr_rejected")
        measure_pieces.append((item, inner))

    def _table_join(table: str) -> CubeJoin | None:
        if table == base:
            return None
        return _join_to(cube, table)

    child_tables: set[str] = set()
    for piece in pieces:
        join = _table_join(piece.table)
        if join is not None and _to_many(join):
            child_tables.add(piece.table)
    for item, _inner in measure_pieces:
        join = _table_join(_ident(item.table))
        if join is not None and _to_many(join):
            child_tables.add(_ident(item.table))

    parent_measure = any(_ident(item.table) not in child_tables for item, _inner in measure_pieces)
    child_group = any(piece.table in child_tables and piece.kind != "measure" for piece in pieces)
    if parent_measure and child_group:
        # A base measure broken out by a TO_MANY dimension would repeat the
        # parent row. That allocation is a guess, so it is not compiled.
        raise CubePlanError("fanout_grain")

    filters_for: dict[str, list[exp.Expression]] = {}
    for item in plan.filters:
        member = _ident(item.member)
        op = str(item.op or "").strip().lower()
        if op not in _OPS:
            raise CubePlanError(REASON_UNKNOWN)
        host = _filter_host(member, pieces, dimensions, times, grain, read)
        filters_for.setdefault(host[0], []).append(_compare(op, host[1], item.value))

    needed: set[str] = set(filters_for)
    for piece in pieces:
        if piece.table != base:
            needed.add(piece.table)
    for item, _inner in measure_pieces:
        table = _ident(item.table)
        if table != base:
            needed.add(table)
    needed.discard(base)

    joins: dict[str, CubeJoin] = {}
    for table in sorted(needed):
        join = _join_to(cube, table)
        if join is None:
            raise CubePlanError(REASON_UNKNOWN)
        joins[table] = join
        if _to_many(join):
            child_tables.add(table)

    outer_dims: list[exp.Expression] = []
    outer_groups: list[exp.Expression] = []
    for piece in pieces:
        if piece.table in child_tables:
            continue
        shown = _piece_expr(piece, read, grain)
        outer_groups.append(shown.copy())
        outer_dims.append(shown.as_(piece.name))

    outer_measures: list[exp.Expression] = []
    child_selects: dict[str, list[exp.Expression]] = {}
    child_groups: dict[str, list[exp.Expression]] = {}
    child_where: dict[str, list[exp.Expression]] = {}

    for table, join in joins.items():
        if not _to_many(join):
            continue
        keys: list[exp.Expression] = []
        grouped: list[exp.Expression] = []
        for col in join.to_columns:
            col_name = _ident(col)
            keys.append(exp.column(col_name, table=table).as_(col_name))
            grouped.append(exp.column(col_name, table=table))
        child_selects[table] = keys
        child_groups[table] = grouped
        child_where[table] = list(filters_for.get(table, []))

    for piece in pieces:
        if piece.table not in child_tables:
            continue
        shown = _piece_expr(piece, read, grain)
        child_groups[piece.table].append(shown.copy())
        child_selects[piece.table].append(shown.as_(piece.name))
        outer_groups.append(exp.column(piece.name, table=f"{piece.table}_agg"))
        outer_dims.append(exp.column(piece.name, table=f"{piece.table}_agg").as_(piece.name))

    for item, inner in measure_pieces:
        table = _ident(item.table)
        label = _ident(item.name)
        if table in child_tables:
            child_selects[table].append(_agg(item.agg, inner).as_(label))
            outer_measures.append(exp.Sum(this=exp.column(label, table=f"{table}_agg")).as_(label))
        else:
            outer_measures.append(_agg(item.agg, inner).as_(label))

    query = exp.select(*outer_dims, *outer_measures).from_(base)
    for table, join in joins.items():
        if _to_many(join):
            continue
        query = query.join(table, on=_on(join, table), join_type="INNER")
        if filters_for.get(table):
            for pred in filters_for[table]:
                query = query.where(pred)
    if filters_for.get(base):
        for pred in filters_for[base]:
            query = query.where(pred)

    for table, join in joins.items():
        if not _to_many(join):
            continue
        alias = f"{table}_agg"
        sub = exp.select(*child_selects[table]).from_(table)
        for pred in child_where.get(table, []):
            sub = sub.where(pred)
        child_sub = sub.group_by(*child_groups[table]).subquery(alias)
        query = query.join(child_sub, on=_on(join, alias), join_type="LEFT")

    if outer_groups:
        query = query.group_by(*outer_groups)
    orders: list[exp.Expression] = []
    selected = {piece.name for piece in pieces}
    selected.update(_ident(item.name) for item, _inner in measure_pieces)
    for item in plan.order:
        name = _ident(item.member)
        if name not in selected:
            raise CubePlanError(REASON_UNKNOWN)
        direction = str(item.direction or "asc").strip().lower()
        if direction not in {"asc", "desc"}:
            raise CubePlanError(REASON_UNKNOWN)
        ordered = exp.column(name)
        orders.append(ordered.asc() if direction == "asc" else ordered.desc())
    if orders:
        query = query.order_by(*orders)
    if plan.limit is not None:
        if type(plan.limit) is not int or plan.limit < 0:
            raise CubePlanError("cube_plan_incomplete")
        query = query.limit(plan.limit)
    return query.sql(dialect=read)


def _filter_host(
    member: str,
    pieces: list[_Piece],
    dimensions: dict[str, CubeMeasure | CubeDimension | CubeTimeDimension],
    times: dict[str, CubeMeasure | CubeDimension | CubeTimeDimension],
    grain: str | None,
    dialect: str,
) -> tuple[str, exp.Expression]:
    for piece in pieces:
        if piece.name == member:
            return piece.table, _piece_expr(piece, dialect, grain)
    dim = dimensions.get(member)
    if isinstance(dim, CubeDimension):
        table = _ident(dim.table)
        return table, _parse_expr(dim.expr, table, dialect)
    timed = times.get(member)
    if isinstance(timed, CubeTimeDimension):
        table = _ident(timed.table)
        expr = _parse_expr(timed.expr, table, dialect)
        if grain:
            expr = exp.DateTrunc(this=expr, unit=exp.Literal.string(grain))
        return table, expr
    raise CubePlanError(REASON_UNKNOWN)
