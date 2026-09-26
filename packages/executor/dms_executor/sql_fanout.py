"""FANOUT-GUARD-01: an aggregate over a join must not count a row twice.

Typed ingest made ``SUM(orders.amount)`` over ``orders JOIN items`` bind, and
it came back L2_VALIDATED at 430.00 where the orders total 200.00: every order
row was repeated once per item. Nothing in the grain gate sees that, because
the grain (one scalar) is right; the rows under it are not.

The rule, per aggregate, by sqlglot scope analysis (no word rules):

- A multiplicity-sensitive aggregate (SUM, AVG, COUNT(col), COUNT(*), and any
  other aggregate that is not MIN / MAX / a row picker / COUNT(DISTINCT))
  reads the relation(s) its argument columns come from. COUNT(*) and an
  aggregate over no column read the scope's FROM relation. A column that
  comes through a CTE or derived table is followed into it, so a join inside
  the CTE is checked the same way.
- Every other relation in the scope must be *determined* by the aggregated
  one: some equality in an ON clause or a top-level WHERE conjunct sets each
  of its key columns from relations already determined (or a constant), and
  it is UNIQUE on those columns among its rows with no NULL key (a NULL never
  equals anything, so those rows never match). Both sides of each equality
  must have the same type: a comparison that casts the key (VARCHAR '010'
  against INTEGER 10) can match several rows unique as text, so it is
  ``fan_out_unanalysable:key_type``. An equality in a LEFT JOIN's ON clause
  keys only that join's own relation, unless that relation is the aggregated
  one and the aggregate skips NULL rows (SUM / AVG / COUNT of plain
  arithmetic): an unmatched left row then carries only NULLs for it, so
  ``dimension LEFT JOIN fact`` summing the fact answers. For a real table that
  is checked against the data in the Space warehouse (read-only; cached per
  table + columns + ingest id + file stamp). A derived table or CTE is unique
  only in an allowlisted shape, and ``fan_out_unanalysable:derived_shape``
  otherwise (fail closed): plain columns of one base table with a proven key;
  GROUP BY plain, unshadowed input columns (one row per key); an ungrouped
  aggregate (one row); DISTINCT on the key (or plain columns of one table the
  data shows the key determines). None with a set-returning call (``unnest``)
  or a window in its projection.
- An aggregate reading several relations needs one of them (the start) to
  determine all the others: ``SUM(l.qty * p.price)`` over lines joined
  many-to-one to products counts each line once.
- An aggregate that reads a grouped / DISTINCT derived table's un-aggregated
  output over a join inside it is ``fan_out_unanalysable:grouped_passthrough``:
  the groups can be finer than that column's own rows.
- A relation that is not determined names the join that repeats rows:
  ``fan_out:<relation>.<column>``. A join shape the analysis cannot read
  (RIGHT / FULL / NATURAL / ASOF join, a set operation, a column it cannot
  place, no warehouse to check against) is ``fan_out_unanalysable:<why>``,
  from a closed set. Fail closed, like Cortex ``manifest.py``.

A single-table aggregate is never refused. The data check can only prove a
key unique over the whole table; a row predicate applied later only removes
rows, so it cannot break that.

Swap: a Cortex HTTP join-cardinality check behind ``fan_out_reason``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope

from dms_executor.demo_warehouse import WarehouseBusy, connect_file_readonly

_DIALECT = "duckdb"

REASON_FAN_OUT = "fan_out"
REASON_FAN_OUT_UNANALYSABLE = "fan_out_unanalysable"
FAN_OUT_REASONS = frozenset({REASON_FAN_OUT, REASON_FAN_OUT_UNANALYSABLE})

#: The tails ``fan_out_unanalysable`` writes. A closed set: nothing a model
#: chose is inside, so the customer may read them whole.
FAN_OUT_UNANALYSABLE_WHY = frozenset(
    {
        "parse",
        "not_a_query",
        "scope",
        "set_operation",
        "lateral",
        "outer_join",
        "join_method",
        "non_equi_join",
        "column_unresolved",
        "derived_key",
        "no_warehouse",
        "data_check_failed",
        "key_type",
        "grouped_passthrough",
        "derived_shape",
    }
)

#: Aggregates whose value does not change when an input row is repeated.
_INSENSITIVE: tuple[type[exp.Expression], ...] = (
    exp.Min,
    exp.Max,
    exp.AnyValue,
    exp.First,
    exp.Last,
    exp.ArgMax,
    exp.ArgMin,
    exp.LogicalAnd,
    exp.LogicalOr,
    exp.ApproxDistinct,
)


class _Refuse(Exception):  # noqa: N818 - internal control flow, carries the reason
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _unanalysable(why: str) -> _Refuse:
    assert why in FAN_OUT_UNANALYSABLE_WHY, why
    return _Refuse(f"{REASON_FAN_OUT_UNANALYSABLE}:{why}")


# --- the data check ------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
#: (warehouse, file stamp, relation, columns, ingest id) -> unique and not null.
_UNIQUE_CACHE: dict[tuple[Any, ...], bool] = {}
#: (warehouse, file stamp, relation) -> lower-cased column name -> DuckDB type.
_COLUMNS_CACHE: dict[tuple[Any, ...], dict[str, str]] = {}
_CACHE_MAX = 4096


def clear_fan_out_cache() -> None:
    with _CACHE_LOCK:
        _UNIQUE_CACHE.clear()
        _COLUMNS_CACHE.clear()


def _file_stamp(db: Path) -> tuple[int, ...]:
    out: list[int] = []
    for p in (db, db.with_name(db.name + ".wal")):
        try:
            st = p.stat()
        except OSError:
            out.extend((0, 0))
            continue
        out.extend((st.st_mtime_ns, st.st_size))
    return tuple(out)


def _bare_table(table: exp.Table) -> exp.Table:
    """The relation as the SQL names it, without alias or anything attached."""
    out = exp.Table(this=table.this.copy())
    for arg in ("db", "catalog"):
        node = table.args.get(arg)
        if node is not None:
            out.set(arg, node.copy())
    return out


def _label(table: exp.Table) -> str:
    return ".".join(p for p in (table.catalog, table.db, table.name) if p)


@dataclass
class _Probe:
    """Read-only questions to the Space warehouse, one connection per check."""

    warehouse: Path | None
    _con: Any = None
    _stamp: tuple[int, ...] | None = None
    _ingest: dict[str, str | None] = field(default_factory=dict)

    def _connect(self) -> Any:
        if self.warehouse is None or not Path(self.warehouse).is_file():
            raise _unanalysable("no_warehouse")
        if self._con is None:
            try:
                self._con = connect_file_readonly(Path(self.warehouse))
            except WarehouseBusy as exc:
                raise _unanalysable("data_check_failed") from exc
            self._stamp = _file_stamp(Path(self.warehouse))
        return self._con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def _key(self, table: exp.Table) -> tuple[Any, ...]:
        self._connect()
        return (str(Path(str(self.warehouse)).resolve()), self._stamp, _label(table).lower())

    def _ingest_id(self, table: exp.Table) -> str | None:
        label = _label(table).lower()
        if label not in self._ingest:
            ingest: str | None = None
            if (table.db or "").lower() == "bronze" and not table.catalog:
                try:
                    row = self._connect().execute(
                        "SELECT ingest_id FROM bronze._ingest_registry "
                        "WHERE lower(table_name) = ? LIMIT 1",
                        [table.name.lower()],
                    ).fetchone()
                    ingest = None if row is None or row[0] is None else str(row[0])
                except _Refuse:
                    raise
                except Exception:  # noqa: BLE001 - no registry: the file stamp still keys it
                    ingest = None
            self._ingest[label] = ingest
        return self._ingest[label]

    def columns(self, table: exp.Table) -> frozenset[str] | None:
        """Lower-cased column names, or None when there is no warehouse to ask."""
        if self.warehouse is None or not Path(self.warehouse).is_file():
            return None
        return frozenset(self.types(table))

    def types(self, table: exp.Table) -> dict[str, str]:
        """Lower-cased column name -> DuckDB type, as the warehouse declares it."""
        key = self._key(table)  # raises no_warehouse when there is none
        with _CACHE_LOCK:
            hit = _COLUMNS_CACHE.get(key)
        if hit is not None:
            return hit
        rel = _bare_table(table).sql(dialect=_DIALECT)
        try:
            rows = self._connect().execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
            found = {str(r[0]).lower(): str(r[1]).upper() for r in rows}
        except _Refuse:
            raise
        except Exception as exc:  # noqa: BLE001 - a relation it cannot read is unproven
            raise _unanalysable("data_check_failed") from exc
        with _CACHE_LOCK:
            if len(_COLUMNS_CACHE) >= _CACHE_MAX:
                _COLUMNS_CACHE.clear()
            _COLUMNS_CACHE[key] = found
        return found

    def determines(self, table: exp.Table, keys: Iterable[str], cols: Iterable[str]) -> bool:
        """``keys`` are never NULL and functionally determine ``cols``, on the data."""
        key_names = tuple(sorted({c.lower() for c in keys}))
        all_names = tuple(sorted({c.lower() for c in cols} | set(key_names)))
        rel = _bare_table(table).sql(dialect=_DIALECT)

        def idents(names: tuple[str, ...]) -> list[str]:
            return [exp.to_identifier(c, quoted=True).sql(dialect=_DIALECT) for c in names]

        any_null = " OR ".join(f"{i} IS NULL" for i in idents(key_names))
        sql = (
            f"SELECT (SELECT COUNT(*) FROM {rel} WHERE {any_null}), "
            f"(SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(idents(key_names))} "
            f"FROM {rel})), "
            f"(SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(idents(all_names))} "
            f"FROM {rel}))"
        )
        return self._cached_check(table, ("fd", key_names, all_names), sql)

    def _cached_check(self, table: exp.Table, what: tuple[Any, ...], sql: str) -> bool:
        key = (*self._key(table), what, self._ingest_id(table))
        with _CACHE_LOCK:
            hit = _UNIQUE_CACHE.get(key)
        if hit is not None:
            return hit
        try:
            row = self._connect().execute(sql).fetchone()
        except _Refuse:
            raise
        except Exception as exc:  # noqa: BLE001 - a check that cannot run proves nothing
            raise _unanalysable("data_check_failed") from exc
        if row is None:
            raise _unanalysable("data_check_failed")
        nulls, left, right = (int(v or 0) for v in row)
        ok = nulls == 0 and left == right
        with _CACHE_LOCK:
            if len(_UNIQUE_CACHE) >= _CACHE_MAX:
                _UNIQUE_CACHE.clear()
            _UNIQUE_CACHE[key] = ok
        return ok

    def unique_where_matchable(self, table: exp.Table, cols: Iterable[str]) -> bool:
        """The rows whose key has no NULL are unique on the key, on the data.

        Every key column is set by an equality, and ``NULL = x`` never holds (in
        an inner join, a WHERE conjunct or a LEFT JOIN's ON), so a row with a
        NULL key never matches and cannot repeat anything.
        """
        names = tuple(sorted({c.lower() for c in cols}))
        rel = _bare_table(table).sql(dialect=_DIALECT)
        idents = [exp.to_identifier(c, quoted=True).sql(dialect=_DIALECT) for c in names]
        none_null = " AND ".join(f"{i} IS NOT NULL" for i in idents)
        sql = (
            f"SELECT 0, COUNT(*) FILTER (WHERE {none_null}), "
            f"(SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(idents)} FROM {rel} "
            f"WHERE {none_null})) "
            f"FROM {rel}"
        )
        return self._cached_check(table, ("unique_matchable", names), sql)


# --- scope helpers -----------------------------------------------------------------

_EXTERNAL = "\x00external"


def _owner(node: exp.Expression) -> exp.Expression | None:
    return node.find_ancestor(exp.Select, exp.SetOperation)


def _own_columns(select: exp.Expression, node: exp.Expression) -> list[exp.Column]:
    return [
        c
        for c in node.find_all(exp.Column)
        if not isinstance(c.this, exp.Star) and _owner(c) is select
    ]


def _unalias(proj: exp.Expression) -> exp.Expression:
    return proj.this if isinstance(proj, exp.Alias) else proj


def _is_star(proj: exp.Expression) -> bool:
    return isinstance(proj, exp.Star) or (
        isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
    )


def _sensitive(agg: exp.Expression) -> bool:
    if isinstance(agg, _INSENSITIVE):
        return False
    return not (isinstance(agg, exp.Count) and isinstance(agg.this, exp.Distinct))


_INTEGER_TYPES = frozenset(
    {
        "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
        "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT",
    }
)


def _same_comparison_type(key_type: str | None, value_type: str | None) -> bool:
    """An equality compares the key in its own values, never through a cast.

    DuckDB casts a VARCHAR key to INTEGER to compare it with an INTEGER value,
    so ``'10'``, ``'010'`` and ``' 10'`` (unique as text) all match 10. The
    data check proves uniqueness in the key's own type only, so the two sides
    must share it; integer widths widen exactly, so they count as one type.
    Anything else is not proven.
    """
    if key_type is None or value_type is None:
        return False
    if key_type == value_type:
        return True
    return key_type in _INTEGER_TYPES and value_type in _INTEGER_TYPES


#: Functions that return a set of rows: in a projection they repeat the row
#: they sit on (DuckDB ``unnest``), so the relation has more rows than its FROM.
_SET_RETURNING: tuple[type[exp.Expression], ...] = (
    exp.Explode,
    exp.Unnest,
    exp.GenerateSeries,
    exp.Inline,
    exp.UDTF,
)
_SET_RETURNING_NAMES = frozenset(
    {
        "unnest", "explode", "explode_outer", "posexplode", "inline",
        "generate_series", "range", "generate_subscripts", "json_each", "json_tree",
        "flatten",
    }
)


def _set_returning(node: exp.Expression) -> bool:
    """``node`` holds a set-returning call anywhere (fail closed: any depth)."""
    for n in node.walk():
        if isinstance(n, _SET_RETURNING):
            return True
        if isinstance(n, exp.Anonymous) and str(n.name).lower() in _SET_RETURNING_NAMES:
            return True
    return False


def _in_window(agg: exp.Expression) -> bool:
    return isinstance(agg.find_ancestor(exp.Window, exp.Select, exp.SetOperation), exp.Window)


def _collapses(select: exp.Select) -> bool:
    """``select`` has an aggregate that collapses its rows (a window one does not)."""
    return any(
        _owner(a) is select and not _in_window(a)
        for p in select.expressions
        for a in p.find_all(exp.AggFunc)
    )


#: Nodes an aggregate argument may hold and still be NULL whenever any column
#: in it is NULL (arithmetic and casts propagate NULL; COALESCE / CASE do not).
_NULL_PROPAGATING: tuple[type[exp.Expression], ...] = (
    exp.Column,
    exp.Identifier,
    exp.Literal,
    exp.Paren,
    exp.Neg,
    exp.Add,
    exp.Sub,
    exp.Mul,
    exp.Div,
    exp.Mod,
    exp.Cast,
    exp.DataType,
    exp.DataTypeParam,
)


def _ignores_null_rows(agg: exp.Expression) -> bool:
    """SUM / AVG / COUNT of an argument that is NULL when any of its columns is.

    Such an aggregate skips a row whose columns from one relation are all
    NULL, as a LEFT JOIN's unmatched left row carries for its right side.
    """
    if not isinstance(agg, (exp.Sum, exp.Avg, exp.Count)):
        return False
    arg = agg.this
    if not isinstance(arg, exp.Expression) or isinstance(arg, (exp.Star, exp.Distinct)):
        return False
    if agg.args.get("expressions"):
        return False
    return all(isinstance(n, _NULL_PROPAGATING) for n in arg.walk())


def _is_aggregated(col: exp.Column, select: exp.Expression) -> bool:
    """``col`` sits inside an aggregate that collapses ``select``'s rows."""
    node = col.parent
    while node is not None and node is not select:
        if isinstance(node, exp.AggFunc) and _owner(node) is select:
            return not isinstance(node.parent, exp.Window)
        node = node.parent
    return False


def _conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    return list(node.flatten()) if isinstance(node, exp.And) else [node]


@dataclass
class _Analysis:
    probe: _Probe

    # -- sources ---------------------------------------------------------------

    def _sources(self, scope: Scope) -> dict[str, exp.Table | Scope]:
        out: dict[str, exp.Table | Scope] = {}
        for name, (_node, src) in scope.selected_sources.items():
            if isinstance(src, Scope) and src.scope_type == ScopeType.UDTF:
                raise _unanalysable("lateral")
            if not isinstance(src, (exp.Table, Scope)):
                raise _unanalysable("scope")
            out[str(name)] = src
        return out

    def _columns_of(self, src: exp.Table | Scope) -> frozenset[str] | None:
        if isinstance(src, exp.Table):
            return self.probe.columns(src)
        expr = src.expression
        if isinstance(expr, exp.Select) and any(_is_star(p) for p in expr.expressions):
            return None
        return frozenset(str(n).lower() for n in expr.named_selects)

    def _resolve(self, scope: Scope, col: exp.Column) -> str:
        """The local source a column reads, or ``_EXTERNAL`` (an outer / alias ref)."""
        sources = self._sources(scope)
        if col.table:
            for name in sources:
                if name.lower() == col.table.lower():
                    return name
            return _EXTERNAL
        if len(sources) == 1:
            return next(iter(sources))
        hits: list[str] = []
        for name, src in sources.items():
            cols = self._columns_of(src)
            if cols is None:
                raise _unanalysable("column_unresolved")
            if col.name.lower() in cols:
                hits.append(name)
        if len(hits) > 1:
            raise _unanalysable("column_unresolved")
        return hits[0] if hits else _EXTERNAL

    def _driving(self, scope: Scope) -> str:
        select = scope.expression
        from_ = select.args.get("from") if isinstance(select, exp.Select) else None
        if from_ is None:
            raise _unanalysable("scope")
        name = from_.this.alias_or_name
        for src in self._sources(scope):
            if src.lower() == str(name).lower():
                return src
        raise _unanalysable("scope")

    # -- which relations determine which ----------------------------------------

    def _type_of(self, scope: Scope, name: str, col: str) -> str | None:
        """The DuckDB type of ``name.col`` in ``scope``, or None when not known."""
        src = self._sources(scope)[name]
        if isinstance(src, exp.Table):
            return self.probe.types(src).get(col.lower())
        select = src.expression
        if not isinstance(select, exp.Select):
            return None
        inner: exp.Expression | None = None
        for p in select.expressions:
            if not _is_star(p) and str(p.alias_or_name).lower() == col.lower():
                inner = _unalias(p)
                break
        if inner is None and any(_is_star(p) for p in select.expressions):
            inner = exp.column(col)
        return self._value_type(src, inner)

    def _value_type(self, scope: Scope, value: exp.Expression | None) -> str | None:
        if isinstance(value, exp.Column) and not isinstance(value.this, exp.Star):
            src = self._resolve(scope, value)
            return None if src == _EXTERNAL else self._type_of(scope, src, value.name)
        if isinstance(value, exp.Cast):
            to = value.to
            if to.is_type(*(exp.DataType.INTEGER_TYPES - {exp.DataType.Type.BIT})):
                return "INTEGER"
            if to.is_type(*exp.DataType.TEXT_TYPES):
                return "VARCHAR"
            return to.sql(dialect=_DIALECT).upper()
        if isinstance(value, exp.Literal):
            if value.is_string:
                return "VARCHAR"
            return "INTEGER" if value.is_int else None
        return None

    def _equalities(
        self, scope: Scope, start: str | None = None, null_rows_ignored: bool = False
    ) -> tuple[list[tuple[str, str, frozenset[str]]], set[str], set[str]]:
        """``(source, key column, sources the value depends on)`` per usable equality.

        Plus the sources joined SEMI / ANTI, which never add rows, and the
        sources an equality would have keyed but for a type the comparison casts.

        An equality in a LEFT JOIN's ON clause filters nothing: it only chooses
        that join's own right-hand rows, so it can key that relation alone.
        Except when that right-hand relation is ``start`` and the aggregate
        skips rows where its columns are NULL (``null_rows_ignored``): every
        row carrying a ``start`` row satisfied the whole ON clause, and an
        unmatched left row carries only NULLs for ``start``, so the ON
        equalities may key the left relations too.
        """
        select = scope.expression
        assert isinstance(select, exp.Select)
        sources = self._sources(scope)
        conds: list[tuple[exp.Expression, str | None]] = [
            (c, None)
            for c in _conjuncts(
                select.args["where"].this if select.args.get("where") is not None else None
            )
        ]
        mismatched: set[str] = set()
        filtering: set[str] = set()
        seen: list[str] = [self._driving(scope)]
        eqs: list[tuple[str, str, frozenset[str]]] = []
        for join in select.args.get("joins") or []:
            name = next(
                (s for s in sources if s.lower() == str(join.this.alias_or_name).lower()), None
            )
            if name is None:
                raise _unanalysable("scope")
            if join.method:
                raise _unanalysable("join_method")
            side = str(join.side or "").upper()
            kind = str(join.kind or "").upper()
            if side in {"RIGHT", "FULL"} or (kind == "OUTER" and side != "LEFT"):
                raise _unanalysable("outer_join")
            if kind in {"SEMI", "ANTI"}:
                filtering.add(name)
                continue
            only = (
                name
                if side == "LEFT" and not (null_rows_ignored and name == start)
                else None
            )
            conds.extend((c, only) for c in _conjuncts(join.args.get("on")))
            for ident in join.args.get("using") or []:
                key = str(ident.name).lower()
                left = [
                    s for s in seen if key in (self._columns_of(sources[s]) or frozenset())
                ]
                if len(left) != 1:
                    continue
                if not _same_comparison_type(
                    self._type_of(scope, name, key), self._type_of(scope, left[0], key)
                ):
                    mismatched.update({name, left[0]})
                    continue
                eqs.append((name, key, frozenset({left[0]})))
                if only is None:
                    eqs.append((left[0], key, frozenset({name})))
            seen.append(name)
        for cond, only in conds:
            if not isinstance(cond, exp.EQ):
                continue  # a filter: it can only remove matches
            for key_side, value in ((cond.this, cond.expression), (cond.expression, cond.this)):
                if not isinstance(key_side, exp.Column) or isinstance(key_side.this, exp.Star):
                    continue
                if value.find(exp.Select, exp.AggFunc, exp.Window) is not None:
                    continue
                src = self._resolve(scope, key_side)
                if src == _EXTERNAL or (only is not None and src != only):
                    continue
                deps = {self._resolve(scope, c) for c in _own_columns(select, value)}
                deps.discard(_EXTERNAL)
                if src in deps:
                    continue
                if not _same_comparison_type(
                    self._type_of(scope, src, key_side.name), self._value_type(scope, value)
                ):
                    mismatched.add(src)
                    continue
                eqs.append((src, key_side.name.lower(), frozenset(deps)))
        return eqs, filtering, mismatched

    def _determined_from(
        self, scope: Scope, start: str, null_rows_ignored: bool = False
    ) -> None:
        """Every row of ``start`` appears at most once in ``scope``'s rows, or raise."""
        sources = self._sources(scope)
        if len(sources) <= 1:
            return
        eqs, filtering, mismatched = self._equalities(scope, start, null_rows_ignored)
        covered = {start, *filtering}

        def keys_for(name: str) -> set[str]:
            return {k for s, k, deps in eqs if s == name and deps <= covered}

        progress = True
        while progress:
            progress = False
            for name in sources:
                if name in covered:
                    continue
                if self._unique(scope, name, keys_for(name)) is None:
                    covered.add(name)
                    progress = True
        for name in sources:
            if name not in covered:
                if name in mismatched:
                    raise _unanalysable("key_type")
                blame = self._unique(scope, name, keys_for(name))
                raise _Refuse(blame or f"{REASON_FAN_OUT_UNANALYSABLE}:non_equi_join")

    # -- uniqueness -----------------------------------------------------------------

    def _unique(self, scope: Scope, name: str, keys: set[str]) -> str | None:
        """None when ``name`` is unique on ``keys`` (not null), else the named reason."""
        src = self._sources(scope)[name]
        if isinstance(src, exp.Table):
            if not keys:
                return f"{REASON_FAN_OUT_UNANALYSABLE}:non_equi_join"
            if self.probe.unique_where_matchable(src, keys):
                return None
            return f"{REASON_FAN_OUT}:{_label(src)}.{'+'.join(sorted(keys))}"
        try:
            return self._unique_derived(src, keys)
        except _Refuse as exc:
            return exc.reason

    def _unique_derived(self, sub: Scope, keys: set[str]) -> str | None:
        """None when derived table ``sub`` is provably unique on ``keys``.

        Only an allowlisted shape is provable; anything else is
        ``fan_out_unanalysable:derived_shape`` (fail closed, not recognition):

        (a) plain columns of ONE base table whose key columns the data shows
            unique and not null;
        (b) ``SELECT k.., aggregates FROM <anything> GROUP BY k..`` where every
            GROUP BY entry is a plain input column (or an ordinal naming one)
            that no projection alias shadows (DuckDB binds GROUP BY to the
            input column, not to an alias of the same name): an alias named
            like an input column but projecting something else is not
            provable when that name is grouped on or joined on (the
            compiler's ``ANY_VALUE(category) AS category`` beside the key
            is neither), nor are two outputs of one name; no
            GROUPING SETS / ROLLUP / CUBE / ALL;
        (c) an ungrouped aggregate (one row);
        (d) DISTINCT on the key, or DISTINCT plain columns of one base table
            whose key the data shows determines the rest.

        None of them may carry a set-returning call (``unnest`` repeats the row
        it sits on) or a window (a windowed aggregate is not one row) in its
        projection. A provable shape unique on other columns than the join key
        is ``derived_key``.
        """
        select = sub.expression
        if isinstance(select, exp.SetOperation):
            return f"{REASON_FAN_OUT_UNANALYSABLE}:set_operation"
        shape = f"{REASON_FAN_OUT_UNANALYSABLE}:derived_shape"
        if not isinstance(select, exp.Select) or select.args.get("laterals"):
            return shape
        projs = list(select.expressions)
        if any(_set_returning(p) or p.find(exp.Window) is not None for p in projs):
            return shape
        group = select.args.get("group")
        if group is not None:
            return self._unique_grouped(sub, projs, group, keys)
        if _collapses(select):
            return None  # (c) an ungrouped aggregate: exactly one row
        distinct = select.args.get("distinct")
        if distinct is not None:
            if distinct.args.get("on") is not None or any(_is_star(p) for p in projs):
                return shape
            outs = {str(p.alias_or_name).lower() for p in projs}
            if outs <= keys:
                return None
            return self._distinct_key(sub, projs, keys)
        return self._unique_plain(sub, projs, keys)

    def _base_table(self, sub: Scope) -> exp.Table | None:
        """The one base table ``sub`` reads, with no join and no table function."""
        select = sub.expression
        if not isinstance(select, exp.Select) or select.args.get("joins"):
            return None
        from_ = select.args.get("from")
        if from_ is None or not isinstance(from_.this, exp.Table):
            return None
        sources = self._sources(sub)
        if len(sources) != 1:
            return None
        src = next(iter(sources.values()))
        if not isinstance(src, exp.Table) or not isinstance(src.this, exp.Identifier):
            return None
        return src

    def _unique_plain(
        self, sub: Scope, projs: list[exp.Expression], keys: set[str]
    ) -> str | None:
        """(a) Plain columns of one base table: unique iff its key is, on the data."""
        if not keys:
            return f"{REASON_FAN_OUT_UNANALYSABLE}:non_equi_join"
        shape = f"{REASON_FAN_OUT_UNANALYSABLE}:derived_shape"
        table = self._base_table(sub)
        if table is None:
            return shape
        for p in projs:
            inner = _unalias(p)
            if not _is_star(p) and not (
                isinstance(inner, exp.Column) and not isinstance(inner.this, exp.Star)
            ):
                return shape
        mapped: set[str] = set()
        for key in keys:
            col = self._output_column(sub, key)
            if col is None:
                return f"{REASON_FAN_OUT_UNANALYSABLE}:derived_key"
            mapped.add(col.name.lower())
        if self.probe.unique_where_matchable(table, mapped):
            return None
        return f"{REASON_FAN_OUT}:{_label(table)}.{'+'.join(sorted(mapped))}"

    def _unique_grouped(
        self, sub: Scope, projs: list[exp.Expression], group: exp.Group, keys: set[str]
    ) -> str | None:
        """(b) Grouped exactly on plain, unshadowed input columns it outputs."""
        shape = f"{REASON_FAN_OUT_UNANALYSABLE}:derived_shape"
        derived_key = f"{REASON_FAN_OUT_UNANALYSABLE}:derived_key"
        if any(group.args.get(k) for k in ("all", "rollup", "cube", "grouping_sets")):
            return shape
        if group.find(exp.Rollup, exp.Cube, exp.GroupingSets) is not None:
            return shape
        inputs: set[str] = set()
        for src in self._sources(sub).values():
            cols = self._columns_of(src)
            if cols is None:
                return shape
            inputs |= cols
        grouped_names = {
            g.name.lower() for g in group.expressions if isinstance(g, exp.Column)
        }
        out_names = [str(p.alias_or_name).lower() for p in projs]
        if len(set(out_names)) != len(out_names):
            return shape  # two outputs of one name: which one the join reads is unproven
        for p in projs:
            if _is_star(p):
                return shape
            if (
                isinstance(p, exp.Alias)
                and p.alias.lower() in inputs
                and (p.alias.lower() in grouped_names or p.alias.lower() in keys)
            ):
                inner = p.this
                if not (
                    isinstance(inner, exp.Column)
                    and not isinstance(inner.this, exp.Star)
                    and inner.name.lower() == p.alias.lower()
                ):
                    return shape  # an alias shadowing an input column
        outs: set[str] = set()
        for g in group.expressions:
            if isinstance(g, exp.Literal) and g.is_int:
                idx = int(g.this) - 1
                if not 0 <= idx < len(projs):
                    return shape
                inner = _unalias(projs[idx])
                if not isinstance(inner, exp.Column) or isinstance(inner.this, exp.Star):
                    return shape
                outs.add(str(projs[idx].alias_or_name).lower())
                continue
            if not isinstance(g, exp.Column) or isinstance(g.this, exp.Star):
                return shape
            if g.name.lower() not in inputs:
                return shape  # binds to an alias or an outer column, not an input
            g_src = self._resolve(sub, g)
            if g_src == _EXTERNAL:
                return shape
            out: str | None = None
            for p in projs:
                inner = _unalias(p)
                if (
                    isinstance(inner, exp.Column)
                    and not isinstance(inner.this, exp.Star)
                    and inner.name.lower() == g.name.lower()
                    and self._resolve(sub, inner) == g_src
                ):
                    out = str(p.alias_or_name).lower()
                    break
            if out is None:
                return derived_key  # grouped finer than what it outputs
            outs.add(out)
        # Pre-aggregated to the join key: one row per key.
        return None if outs <= keys else derived_key

    def _distinct_key(
        self, sub: Scope, projs: list[exp.Expression], keys: set[str]
    ) -> str | None:
        """``SELECT DISTINCT k, attr FROM t`` joined on ``k``: unique iff k -> attr.

        Checked on the data: the DISTINCT rows of the whole table number exactly
        the distinct keys, and no key is NULL. Only a single real table whose
        outputs are bare columns; anything else is not proven.
        """
        derived_key = f"{REASON_FAN_OUT_UNANALYSABLE}:derived_key"
        sources = self._sources(sub)
        if len(sources) != 1:
            return derived_key
        src = next(iter(sources.values()))
        if not isinstance(src, exp.Table) or not isinstance(src.this, exp.Identifier):
            return f"{REASON_FAN_OUT_UNANALYSABLE}:derived_shape"
        by_out: dict[str, str] = {}
        for p in projs:
            inner = _unalias(p)
            if not isinstance(inner, exp.Column) or isinstance(inner.this, exp.Star):
                return derived_key
            by_out[str(p.alias_or_name).lower()] = inner.name.lower()
        if not keys <= set(by_out):
            return derived_key
        key_cols = {by_out[k] for k in keys}
        if self.probe.determines(src, key_cols, set(by_out.values())):
            return None
        return f"{REASON_FAN_OUT}:{_label(src)}.{'+'.join(sorted(key_cols))}"

    def _output_column(self, sub: Scope, name: str) -> exp.Column | None:
        """The bare column an output of a plain derived table passes through."""
        select = sub.expression
        assert isinstance(select, exp.Select)
        for p in select.expressions:
            if _is_star(p):
                continue
            if str(p.alias_or_name).lower() == name.lower():
                inner = _unalias(p)
                if isinstance(inner, exp.Column) and not isinstance(inner.this, exp.Star):
                    return inner
                return None
        if any(_is_star(p) for p in select.expressions):
            return exp.column(name)
        return None

    # -- provenance ------------------------------------------------------------------

    def _origin_ok(self, scope: Scope, name: str, cols: set[str] | None) -> None:
        """``name``'s rows are not repeated in ``scope``, nor inside ``name`` itself."""
        self._determined_from(scope, name)
        src = self._sources(scope)[name]
        if isinstance(src, Scope):
            self._through_derived(src, cols)

    def _through_derived(self, sub: Scope, cols: set[str] | None) -> None:
        select = sub.expression
        if isinstance(select, exp.SetOperation):
            raise _unanalysable("set_operation")
        if not isinstance(select, exp.Select):
            raise _unanalysable("derived_shape")
        if any(_set_returning(p) for p in select.expressions):
            # unnest in a projection repeats the row it sits on.
            raise _unanalysable("derived_shape")
        if (
            select.args.get("group") is not None
            or select.args.get("distinct") is not None
            or _collapses(select)
        ):
            self._through_grouped(sub, cols)
            return
        if cols is None:
            self._origin_ok(sub, self._driving(sub), None)
            return
        for name in cols:
            expr: exp.Expression | None = None
            for p in select.expressions:
                if not _is_star(p) and str(p.alias_or_name).lower() == name:
                    expr = _unalias(p)
                    break
            if expr is None:
                if not any(_is_star(p) for p in select.expressions):
                    raise _unanalysable("column_unresolved")
                expr = exp.column(name)
            if expr.find(exp.Select) is not None:
                raise _unanalysable("scope")
            cols_in = [expr] if isinstance(expr, exp.Column) else _own_columns(select, expr)
            by_src: dict[str, set[str]] = {}
            for c in cols_in:
                src = self._resolve(sub, c)
                if src != _EXTERNAL:
                    by_src.setdefault(src, set()).add(c.name.lower())
            if not by_src:
                self._origin_ok(sub, self._driving(sub), None)
                continue
            for src, names in by_src.items():
                self._origin_ok(sub, src, names)

    def _through_grouped(self, sub: Scope, cols: set[str] | None) -> None:
        """A grouped / DISTINCT derived table: its rows are its groups.

        Counting them (no column read) is counting groups, and its aggregates are
        checked in its own scope. A column it passes through un-aggregated is
        repeated once per group it lands in, and over a join that group can be
        finer than the column's own row (``GROUP BY i.qty, o.order_id,
        o.amount`` repeats each order once per distinct item qty). Not proven
        over a join: fail closed. Over one relation each row lands in one group.
        """
        if cols is None:
            return
        select = sub.expression
        assert isinstance(select, exp.Select)
        passthrough: set[str] = set()
        for name in cols:
            expr: exp.Expression | None = None
            for p in select.expressions:
                if not _is_star(p) and str(p.alias_or_name).lower() == name:
                    expr = _unalias(p)
                    break
            if expr is None:
                if not any(_is_star(p) for p in select.expressions):
                    raise _unanalysable("column_unresolved")
                expr = exp.column(name)
            if expr.find(exp.Select) is not None:
                raise _unanalysable("scope")
            cols_in = [expr] if isinstance(expr, exp.Column) else _own_columns(select, expr)
            for c in cols_in:
                if not _is_aggregated(c, select):
                    passthrough.add(c.name.lower())
        if not passthrough:
            return
        sources = self._sources(sub)
        if len(sources) != 1:
            raise _unanalysable("grouped_passthrough")
        self._origin_ok(sub, next(iter(sources)), passthrough)

    def _aggregate_ok(
        self, scope: Scope, by_src: dict[str, set[str]], null_rows_ignored: bool
    ) -> None:
        """Every relation the aggregate reads is un-repeated in ``scope``, or raise.

        Round 3 briefly accepted "some relation determines the others", so
        ``SUM(l.qty * p.price)`` over lines JOIN products could answer. The
        verifier showed a one-side measure could then be summed at the many
        side's grain just by adding a many-side column to the argument. Every
        source must stand on its own: that revenue shape abstains (a named
        over-refusal), never a confident wrong figure.
        """
        for name, names in by_src.items():
            self._determined_from(scope, name, null_rows_ignored)
            src = self._sources(scope)[name]
            if isinstance(src, Scope):
                self._through_derived(src, names)

    def check_scope(self, scope: Scope) -> None:
        select = scope.expression
        if not isinstance(select, exp.Select):
            return
        for agg in select.find_all(exp.AggFunc):
            if _owner(agg) is not select or not _sensitive(agg):
                continue
            by_src: dict[str, set[str]] = {}
            for col in _own_columns(select, agg):
                src = self._resolve(scope, col)
                if src != _EXTERNAL:
                    by_src.setdefault(src, set()).add(col.name.lower())
            if not by_src:
                # COUNT(*), SUM(1): the rows counted are the FROM relation's.
                self._origin_ok(scope, self._driving(scope), None)
                continue
            self._aggregate_ok(scope, by_src, _ignores_null_rows(agg))


def fan_out_reason(sql: str, warehouse: Path | None) -> str | None:
    """Named reason when an aggregate in ``sql`` may count a row more than once.

    ``None`` means every multiplicity-sensitive aggregate was analysed and no
    join in its scope (or in a CTE / derived table it reads through) can
    repeat the rows it aggregates. Read-only; runs before submit.
    """
    try:
        roots = [r for r in sqlglot.parse(sql, read=_DIALECT) if r is not None]
    except Exception:  # noqa: BLE001 - unparseable is unanalysable, by name
        return f"{REASON_FAN_OUT_UNANALYSABLE}:parse"
    if not roots:
        return f"{REASON_FAN_OUT_UNANALYSABLE}:parse"
    probe = _Probe(warehouse)
    analysis = _Analysis(probe)
    try:
        for root in roots:
            if not isinstance(root, exp.Query):
                return f"{REASON_FAN_OUT_UNANALYSABLE}:not_a_query"
            if root.find(exp.AggFunc) is None:
                continue
            try:
                scopes = traverse_scope(root)
            except Exception:  # noqa: BLE001 - a scope sqlglot cannot build is unproven
                return f"{REASON_FAN_OUT_UNANALYSABLE}:scope"
            for scope in scopes:
                analysis.check_scope(scope)
    except _Refuse as exc:
        return exc.reason
    finally:
        probe.close()
    return None


def fan_out_customer_label(reason: str) -> str:
    """The reason as the customer reads it.

    ``fan_out:<relation>.<column>`` names a granted relation and a column the
    warehouse check read, so it shows whole; an unanalysable tail shows only
    when it is from the closed set.
    """
    head, _, tail = str(reason).partition(":")
    head = head.strip()
    if head == REASON_FAN_OUT_UNANALYSABLE:
        return f"{head}:{tail}" if tail in FAN_OUT_UNANALYSABLE_WHY else head
    return str(reason).strip()


def fan_out_abstain_text(reason: str) -> str:
    """Rendered ABSTAIN text for a fan-out reason. States no figure."""
    head = str(reason).partition(":")[0].strip()
    if head == REASON_FAN_OUT:
        what = (
            "the query joins a table whose matching rows repeat, so a total or count "
            "over the join would count some rows more than once"
        )
    else:
        what = (
            "the query aggregates over a join I could not prove keeps each row once"
        )
    return (
        f"I cannot certify that answer: {what} "
        f"(gap: {fan_out_customer_label(reason)}). I am not showing a figure."
    )


__all__ = [
    "FAN_OUT_REASONS",
    "FAN_OUT_UNANALYSABLE_WHY",
    "REASON_FAN_OUT",
    "REASON_FAN_OUT_UNANALYSABLE",
    "clear_fan_out_cache",
    "fan_out_abstain_text",
    "fan_out_customer_label",
    "fan_out_reason",
]
