"""SAFE-01: the statement-shape layer of the one grant function.

``validate_compiled_sql`` is the only place compiled SQL is granted. This
module is a helper it calls, not a second gate. It answers one question with
the engine's own parser and one syntax tree: is this exactly one read-only
SELECT that reads at least one real table, and which real tables does it read?

What it decides, and nothing else:

- DuckDB's own statement splitter counts the statements, so a comment, a
  string, a dollar-quoted body or a nested block comment cannot hide a second
  one. More than one statement, or any statement that is not a SELECT, is
  refused. A syntax error the engine reports is passed on as ``explain:`` so
  the repair loop still sees it.
- The tree must be a SELECT or a set operation over SELECTs, with no DML, DDL,
  PRAGMA, ATTACH, COPY, INSTALL, LOAD, SET, CALL, EXPORT, row lock or SELECT
  INTO node anywhere in it.
- A table position may hold a name or a generator (range, generate_series,
  unnest). A file reader, a quoted path, a URL or any other table function is
  refused.
- Real tables are resolved scope by scope, so a CTE name is not a table and a
  CTE that shadows a name in one scope does not hide the same name used as a
  real table in another.
- A query whose output does not come from at least one real table (SELECT 42,
  a VALUES list, generators alone) has no data authority and is refused.

Grants (which tables, per Space) stay in ``validate_compiled_sql`` and in the
OPEN PR that restructures them; this layer only reports the table names.

Reasons are codes. They never carry a table, function or path the SQL named.
"""

from __future__ import annotations

from typing import Any

import duckdb
from sqlglot import exp, parse
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

#: Table-position generators that carry no data and read no file.
_GENERATORS = frozenset({"range", "generate_series"})

#: Nodes that may not appear anywhere in a granted query. Looked up by name so
#: a sqlglot release that drops one does not break import. Statements that can
#: only be a root (INSTALL, LOAD) are already refused by the root check.
_FORBIDDEN = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Create",
        "Drop",
        "Alter",
        "Command",
        "Copy",
        "Merge",
        "Set",
        "Pragma",
        "Attach",
        "Detach",
        "Use",
        "Describe",
        "Show",
        "Summarize",
        "Into",
        "Lock",
        "LoadData",
        "Export",
        "Transaction",
        "Commit",
        "Rollback",
        "Analyze",
        "Declare",
        "Kill",
        "Cache",
        "Uncache",
        "TruncateTable",
        "Grant",
        "Refresh",
    )
    if hasattr(exp, name)
)

_SELECT_TYPE = "SELECT"


def _bare(name: str) -> str:
    return str(name or "").strip().lower()


def _is_name(table: exp.Table) -> bool:
    return isinstance(table.this, exp.Identifier)


def _cte_of(scope: Scope, table: exp.Table) -> Scope | None:
    """The CTE scope this table reads, or None when it is a real table."""
    if table.db:
        return None
    want = _bare(table.name)
    for name, cte in scope.cte_sources.items():
        if _bare(name) == want:
            return cte
    return None


def _is_generator(table: exp.Table) -> bool:
    inner = table.this
    if isinstance(inner, exp.GenerateSeries):
        return True
    if isinstance(inner, exp.Anonymous):
        return _bare(inner.name) in _GENERATORS
    return False


def _engine_refusal(sql: str) -> str | None:
    """Statement count and kind, by the engine that will run the text."""
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.ParserException as exc:
        # The engine cannot read it, so nothing can run. The repair loop wants
        # the engine's message, which EXPLAIN reproduces.
        return f"explain:{type(exc).__name__}"
    except Exception:  # noqa: BLE001 - fail closed on anything else
        return "hostile_sql:sql_not_analyzable"
    if len(statements) > 1:
        return "hostile_sql:multi_statement"
    if not statements:
        return "hostile_sql:statement_not_allowed"
    kind = str(getattr(statements[0].type, "name", statements[0].type)).upper()
    if kind != _SELECT_TYPE:
        return "hostile_sql:statement_not_allowed"
    return None


def _reads_data(scope: Scope, seen: set[int]) -> bool:
    """True when this query's output is built from at least one real table."""
    if id(scope) in seen:
        return False
    seen.add(id(scope))
    if scope.union_scopes:
        # Every branch must read a table: a literal branch adds an invented row.
        return all(_reads_data(s, seen) for s in scope.union_scopes)
    for table in scope.tables:
        if not _is_name(table):
            continue
        cte = _cte_of(scope, table)
        if cte is None:
            return True
        if _reads_data(cte, seen):
            return True
    children: list[Scope] = [
        *scope.derived_table_scopes,
        *scope.subquery_scopes,
        *scope.udtf_scopes,
    ]
    return any(_reads_data(c, seen) for c in children)


def select_shape(sql: str) -> tuple[str | None, frozenset[str]]:
    """(refusal, real tables). A refusal is a reason code, or None to continue.

    ``real tables`` are lowercase bare names with no CTE aliases. It is empty
    whenever there is a refusal.
    """
    text = sql or ""
    why = _engine_refusal(text)
    if why:
        return why, frozenset()
    try:
        trees = [t for t in parse(text, read="duckdb") if t is not None]
    except (SqlglotError, ValueError, TypeError, RecursionError):
        return "hostile_sql:sql_not_analyzable", frozenset()
    if len(trees) != 1:
        # Two parsers disagree on the statement count: do not guess.
        return "hostile_sql:multi_statement", frozenset()
    tree: Any = trees[0]
    if isinstance(tree, exp.Subquery):
        tree = tree.unnest()
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return "hostile_sql:statement_not_allowed", frozenset()
    if tree.find(*_FORBIDDEN) is not None:
        return "hostile_sql:statement_not_allowed", frozenset()
    for table in tree.find_all(exp.Table):
        if _is_name(table) or _is_generator(table):
            continue
        # A file reader, a quoted path, a URL or any other table function.
        return "hostile_sql:path_not_allowed", frozenset()
    try:
        scopes = traverse_scope(tree)
    except Exception:  # noqa: BLE001 - fail closed
        return "hostile_sql:sql_not_analyzable", frozenset()
    if not scopes:
        return "hostile_sql:sql_not_analyzable", frozenset()
    real: set[str] = set()
    for scope in scopes:
        for table in scope.tables:
            if not _is_name(table):
                continue
            if _cte_of(scope, table) is not None:
                continue
            real.add(_bare(table.name))
    if not _reads_data(scopes[-1], set()):
        return "hostile_sql:no_data_source", frozenset()
    return None, frozenset(real)


__all__ = ["select_shape"]
