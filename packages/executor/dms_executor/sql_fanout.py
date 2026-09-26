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
  it is UNIQUE and NOT NULL on those columns. For a real table that is
  checked against the data in the Space warehouse (read-only; cached per
  table + columns + ingest id + file stamp). A derived table is unique on its
  GROUP BY columns (pre-aggregated to the key), on its outputs when DISTINCT
  (or on a key the data shows determines the other DISTINCT outputs), and on
  anything when it is an ungrouped aggregate (one row).
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
_COLUMNS_CACHE: dict[tuple[Any, ...], frozenset[str]] = {}
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
        key = self._key(table)
        with _CACHE_LOCK:
            hit = _COLUMNS_CACHE.get(key)
        if hit is not None:
            return hit
        sql = f"SELECT * FROM {_bare_table(table).sql(dialect=_DIALECT)} LIMIT 0"
        try:
            cur = self._connect().execute(sql)
            names = frozenset(str(d[0]).lower() for d in (cur.description or []))
        except _Refuse:
            raise
        except Exception as exc:  # noqa: BLE001 - a relation it cannot read is unproven
            raise _unanalysable("data_check_failed") from exc
        with _CACHE_LOCK:
            if len(_COLUMNS_CACHE) >= _CACHE_MAX:
                _COLUMNS_CACHE.clear()
            _COLUMNS_CACHE[key] = names
        return names

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

    def unique_not_null(self, table: exp.Table, cols: Iterable[str]) -> bool:
        """``COUNT(*) = COUNT(DISTINCT key)`` and no key column NULL, on the data."""
        names = tuple(sorted({c.lower() for c in cols}))
        rel = _bare_table(table).sql(dialect=_DIALECT)
        idents = [exp.to_identifier(c, quoted=True).sql(dialect=_DIALECT) for c in names]
        any_null = " OR ".join(f"{i} IS NULL" for i in idents)
        sql = (
            f"SELECT COUNT(*) FILTER (WHERE {any_null}), COUNT(*), "
            f"(SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(idents)} FROM {rel})) "
            f"FROM {rel}"
        )
        return self._cached_check(table, ("unique", names), sql)


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

    def _equalities(self, scope: Scope) -> tuple[list[tuple[str, str, frozenset[str]]], set[str]]:
        """``(source, key column, sources the value depends on)`` per usable equality.

        Plus the sources joined SEMI / ANTI, which never add rows.
        """
        select = scope.expression
        assert isinstance(select, exp.Select)
        sources = self._sources(scope)
        conds: list[exp.Expression] = _conjuncts(
            select.args["where"].this if select.args.get("where") is not None else None
        )
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
            conds.extend(_conjuncts(join.args.get("on")))
            for ident in join.args.get("using") or []:
                key = str(ident.name).lower()
                left = [
                    s for s in seen if key in (self._columns_of(sources[s]) or frozenset())
                ]
                if len(left) == 1:
                    eqs.append((name, key, frozenset({left[0]})))
                    eqs.append((left[0], key, frozenset({name})))
            seen.append(name)
        for cond in conds:
            if not isinstance(cond, exp.EQ):
                continue  # a filter: it can only remove matches
            for key_side, value in ((cond.this, cond.expression), (cond.expression, cond.this)):
                if not isinstance(key_side, exp.Column) or isinstance(key_side.this, exp.Star):
                    continue
                if value.find(exp.Select, exp.AggFunc, exp.Window) is not None:
                    continue
                src = self._resolve(scope, key_side)
                if src == _EXTERNAL:
                    continue
                deps = {self._resolve(scope, c) for c in _own_columns(select, value)}
                deps.discard(_EXTERNAL)
                if src in deps:
                    continue
                eqs.append((src, key_side.name.lower(), frozenset(deps)))
        return eqs, filtering

    def _determined_from(self, scope: Scope, start: str) -> None:
        """Every row of ``start`` appears at most once in ``scope``'s rows, or raise."""
        sources = self._sources(scope)
        if len(sources) <= 1:
            return
        eqs, filtering = self._equalities(scope)
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
                blame = self._unique(scope, name, keys_for(name))
                raise _Refuse(blame or f"{REASON_FAN_OUT_UNANALYSABLE}:non_equi_join")

    # -- uniqueness -----------------------------------------------------------------

    def _unique(self, scope: Scope, name: str, keys: set[str]) -> str | None:
        """None when ``name`` is unique on ``keys`` (not null), else the named reason."""
        src = self._sources(scope)[name]
        if isinstance(src, exp.Table):
            if not keys:
                return f"{REASON_FAN_OUT_UNANALYSABLE}:non_equi_join"
            if self.probe.unique_not_null(src, keys):
                return None
            return f"{REASON_FAN_OUT}:{_label(src)}.{'+'.join(sorted(keys))}"
        try:
            return self._unique_derived(src, keys)
        except _Refuse as exc:
            return exc.reason

    def _unique_derived(self, sub: Scope, keys: set[str]) -> str | None:
        select = sub.expression
        if not isinstance(select, exp.Select):
            return f"{REASON_FAN_OUT_UNANALYSABLE}:set_operation"
        derived_key = f"{REASON_FAN_OUT_UNANALYSABLE}:derived_key"
        projs = list(select.expressions)
        group = select.args.get("group")
        if group is not None:
            if any(group.args.get(k) for k in ("all", "rollup", "cube", "grouping_sets")):
                return derived_key
            outs: set[str] = set()
            for g in group.expressions:
                out = self._group_output(projs, g)
                if out is None:
                    return derived_key
                outs.add(out)
            # Pre-aggregated to the join key: one row per key.
            return None if outs <= keys else derived_key
        if any(
            _owner(a) is select for p in projs for a in p.find_all(exp.AggFunc)
        ):
            return None  # an ungrouped aggregate: exactly one row
        distinct = select.args.get("distinct")
        if distinct is not None:
            if distinct.args.get("on") is not None or any(_is_star(p) for p in projs):
                return derived_key
            outs = {str(p.alias_or_name).lower() for p in projs}
            if outs <= keys:
                return None
            return self._distinct_key(sub, projs, keys)
        if not keys:
            return f"{REASON_FAN_OUT_UNANALYSABLE}:non_equi_join"
        origin: str | None = None
        mapped: set[str] = set()
        for key in keys:
            col = self._output_column(sub, key)
            if col is None:
                return derived_key
            src = self._resolve(sub, col)
            if src == _EXTERNAL or (origin is not None and src != origin):
                return derived_key
            origin = src
            mapped.add(col.name.lower())
        assert origin is not None
        blame = self._unique(sub, origin, mapped)
        if blame:
            return blame
        self._determined_from(sub, origin)
        return None

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
        if not isinstance(src, exp.Table):
            return derived_key
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

    @staticmethod
    def _group_output(projs: list[exp.Expression], g: exp.Expression) -> str | None:
        """The output name a GROUP BY key is projected as, or None."""
        if isinstance(g, exp.Literal) and g.is_int:
            idx = int(g.this) - 1
            return str(projs[idx].alias_or_name).lower() if 0 <= idx < len(projs) else None
        for p in projs:
            if isinstance(p, exp.Alias) and isinstance(g, exp.Column) and not g.table and (
                p.alias.lower() == g.name.lower()
            ):
                return p.alias.lower()
        for p in projs:
            if _unalias(p) == g:
                return str(p.alias_or_name).lower()
        return None

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
        if not isinstance(select, exp.Select):
            raise _unanalysable("set_operation")
        if select.args.get("group") is not None or select.args.get("distinct") is not None:
            return  # its rows are its own: its aggregates are checked in its own scope
        if any(_owner(a) is select for p in select.expressions for a in p.find_all(exp.AggFunc)):
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
            for src, names in by_src.items():
                self._origin_ok(scope, src, names)


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
