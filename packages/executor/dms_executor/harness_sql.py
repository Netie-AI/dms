"""Execution-time SQL guard for the provider harness.

Off unless ``DMS_HARNESS_SEC`` is the digit 1. The statement type comes from
the sqlglot tree. Grants are an allow-list of relations (and, when supplied,
columns). Nothing here matches question wording.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import sqlglot
from dms_core.harness_sec import enabled, mask_sample_rows, note_audit
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.grant_struct import _file_reader, serve_gap

_ROW_CAP = 1000
_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class SqlScope:
    tables: frozenset[str]
    columns: Mapping[str, frozenset[str]] | None = None


_scope: ContextVar[SqlScope | None] = ContextVar("dms_harness_sql_scope", default=None)


def bind_scope(
    tables: set[str],
    columns: Mapping[str, set[str]] | None = None,
) -> Token[SqlScope | None]:
    cols = None
    if columns is not None:
        cols = {str(key): frozenset(values) for key, values in columns.items()}
    return _scope.set(SqlScope(tables=frozenset(tables), columns=cols))


def reset_scope(token: Token[SqlScope | None]) -> None:
    _scope.reset(token)


def current_scope() -> SqlScope | None:
    return _scope.get()


def row_cap() -> int:
    raw = os.environ.get("DMS_HARNESS_ROW_CAP", "").strip()
    if raw.isdigit() and 0 < int(raw) <= 100_000:
        return int(raw)
    return _ROW_CAP


def timeout_s() -> float:
    raw = os.environ.get("DMS_HARNESS_SQL_TIMEOUT_MS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw) / 1000.0
    return _TIMEOUT_S


def _trees(sql: str, dialect: str) -> list[exp.Expression] | None:
    try:
        parsed = sqlglot.parse(sql, read=dialect)
    except SqlglotError:
        return None
    return [tree for tree in parsed if tree is not None]


def _cte_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            names.add(str(alias).lower())
    return names


def base_tables(tree: exp.Expression) -> list[str]:
    """Relation names that are not CTE aliases."""
    ctes = _cte_names(tree)
    found: list[str] = []
    for table in tree.find_all(exp.Table):
        name = (table.name or "").strip()
        if not name or name.lower() in ctes:
            continue
        if name not in found:
            found.append(name)
    return found


def _bare_star(expr: exp.Expression) -> bool:
    if isinstance(expr, exp.Star):
        return True
    return isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star)


def _column_gap(
    tree: exp.Expression,
    columns: Mapping[str, frozenset[str]] | None,
    tables: list[str],
) -> str | None:
    if columns is None:
        return None
    allowed = {
        str(key).lower(): {item.lower() for item in values}
        for key, values in columns.items()
    }
    known = [name.lower() for name in tables]
    for select in tree.find_all(exp.Select):
        for expr in select.expressions:
            if _bare_star(expr):
                return "sql_column_not_granted"
    for col in tree.find_all(exp.Column):
        name = (col.name or "").lower()
        if not name:
            continue
        table = (col.table or "").lower()
        if table:
            grant = allowed.get(table)
            if not grant or name not in grant:
                return "sql_column_not_granted"
            continue
        if len(known) != 1:
            return "sql_column_not_granted"
        grant = allowed.get(known[0])
        if not grant or name not in grant:
            return "sql_column_not_granted"
    return None


def guard_sql(
    sql: str,
    *,
    tables: set[str],
    columns: Mapping[str, frozenset[str] | set[str]] | None = None,
    dialect: str | None = None,
) -> str | None:
    """None when the statement may run. A code when it must not.

    Flag off returns None without parsing, so callers stay on the old path.
    """
    if not enabled():
        return None
    read = dialect or SERVING_DIALECT
    trees = _trees(sql or "", read)
    if trees is None:
        return "sql_not_analyzable"
    if len(trees) != 1:
        return "multi_statement"
    tree = trees[0]
    if not isinstance(tree, (exp.Query, exp.Values)):
        return "statement_not_allowed"
    if _file_reader(tree):
        return "sql_relation_not_granted"
    colmap: Mapping[str, frozenset[str]] | None
    if columns is None:
        colmap = None
    else:
        colmap = {str(key): frozenset(val) for key, val in columns.items()}
    gap = serve_gap(sql, grantable=set(tables), dialect=read)
    bases = base_tables(tree)
    if gap == "ungranted" and not bases:
        gap = None
    if gap:
        return gap
    return _column_gap(tree, colmap, bases)


def cited_tables(sql: str, dialect: str | None = None) -> list[str]:
    trees = _trees(sql or "", dialect or SERVING_DIALECT)
    if not trees or len(trees) != 1:
        return []
    return base_tables(trees[0])


def _interrupted(exc: BaseException) -> bool:
    return "interrupt" in type(exc).__name__.lower()


def fetch_capped(
    con: Any,
    sql: str,
    *,
    timeout: float | None = None,
    row_cap_n: int | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Run one statement with a wall-clock cap and a row cap.

    ponytail: DuckDB here has no statement_timeout GUC. A timer calls
    ``interrupt`` and the rows are dropped if the deadline fired. Upgrade
    to the engine timeout when the driver exposes one.
    """
    limit = row_cap() if row_cap_n is None else row_cap_n
    wait = timeout_s() if timeout is None else timeout
    fired = {"hit": False}

    def _arm() -> None:
        fired["hit"] = True
        interrupt = getattr(con, "interrupt", None)
        if callable(interrupt):
            try:
                interrupt()
            except Exception:
                return

    timer = threading.Timer(wait, _arm)
    timer.start()
    try:
        cur = con.execute(sql)
        desc = cur.description or []
        cols = [item[0] for item in desc]
        raw = cur.fetchmany(limit + 1)
    except Exception as exc:
        if fired["hit"] or _interrupted(exc):
            return None, "statement_timeout"
        raise
    finally:
        timer.cancel()
    if fired["hit"]:
        return None, "statement_timeout"
    if len(raw) > limit:
        return None, "row_cap"
    rows = [dict(zip(cols, row, strict=True)) for row in raw]
    return mask_sample_rows(rows), None


def audit_run(*, prompt: str, tables: list[str], cost: int = 0) -> None:
    if not enabled():
        return
    actor = os.environ.get("DMS_ACTOR_USER_ID", "").strip() or "unknown"
    note_audit(actor=actor, prompt=prompt, tables=tables, cost=cost)
