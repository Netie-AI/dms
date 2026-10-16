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
- Reading a table is not enough: every UNION arm must return at least one
  value computed from a real table's rows, traced through CTEs, derived
  tables, column lists, scalar subqueries and functions, so ``SELECT 4242
  FROM t`` is refused however the literal is wrapped. ``literal_figure`` adds
  the engine's column types after EXPLAIN: an arm whose numbers are all
  literals is refused, a literal label beside a computed figure is not.
- Not covered, named so nobody reads more into it: a literal number beside a
  computed one (``SELECT sku, 4242 AS revenue, qty``) and zero-weight
  arithmetic (``4242 + 0 * SUM(q)``). Those need a figure verifier.

Grants (which tables, per Space) stay in ``validate_compiled_sql`` and in the
OPEN PR that restructures them; this layer only reports the table names.

Reasons are codes. They never carry a table, function or path the SQL named.
"""

from __future__ import annotations

from collections.abc import Sequence
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


#: Engine result types that state a figure. DECIMAL(p,s) is matched by prefix.
_NUMERIC_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "DOUBLE",
    }
)
#: Deeper than this the lineage walk stops and counts the value as data.
_MAX_DEPTH = 32


def _named_output(scope: Scope, name: str) -> list[Any] | None:
    """The projection(s) a source scope exposes under ``name``, one per UNION arm.

    A column list on the CTE or derived table (``x(v)``, ``s(v)``) renames by
    position. None when the scope exposes no such name (a star, or unknown).
    """
    want = _bare(name)
    arms = _arms(scope)
    parent = scope.expression.parent
    renamed = [_bare(c) for c in getattr(parent, "alias_column_names", None) or []]
    if want in renamed:
        index = renamed.index(want)
    else:
        # UNION arms line up by position; the first arm names the columns.
        names = [_bare(p.alias_or_name) for p in arms[0].expression.selects]
        if names.count(want) != 1:
            return None
        index = names.index(want)
    out: list[Any] = []
    for arm in arms:
        selects = list(arm.expression.selects)
        if index >= len(selects):
            return None
        out.append((selects[index], arm))
    return out


def _arms(scope: Scope) -> list[Scope]:
    """The SELECT scopes whose rows this scope returns, nested UNIONs flattened."""
    if not scope.union_scopes:
        return [scope]
    return [arm for child in scope.union_scopes for arm in _arms(child)]


def _real_table(source: Any) -> bool:
    return isinstance(source, exp.Table) and _is_name(source) and not _is_generator(source)


def _source_derived(source: Any, depth: int) -> bool:
    """Does a FROM source carry rows of a real table into this scope?"""
    if isinstance(source, exp.Table):
        return _real_table(source)
    if isinstance(source, Scope):
        return _scope_derived(source, depth + 1)
    return False


def _column_derived(column: exp.Column, scope: Scope, depth: int) -> bool:
    sources = scope.sources
    if column.table:
        picked = [v for k, v in sources.items() if _bare(k) == _bare(column.table)]
    else:
        picked = list(sources.values())
    if isinstance(column.this, exp.Star):
        return any(_source_derived(src, depth) for src in picked)
    for src in picked:
        if isinstance(src, Scope):
            named = _named_output(src, column.name)
            if named is not None:
                return all(_derived(p, arm, depth + 1) for p, arm in named)
    if any(_real_table(src) for src in picked):
        return True
    if not picked and scope.parent is not None:
        # A correlated reference to the enclosing query.
        return _column_derived(column, scope.parent, depth + 1)
    # Bound by the engine but not traceable here (a generator column, a lateral
    # alias, a source column list this walk does not model). A generator column
    # is not data; anything else counts as data rather than refuse it.
    return not any(isinstance(src, exp.Table) and _is_generator(src) for src in picked)


def _child_scope(scope: Scope, node: Any) -> Scope | None:
    inner = node.this if isinstance(node, exp.Subquery) else node
    for child in scope.subquery_scopes:
        if child.expression is inner:
            return child
    return None


def _derived(node: Any, scope: Scope, depth: int = 0) -> bool:
    """True when this value is computed from rows of a real table.

    A literal, or functions and arithmetic of literals, is not, however deep
    it is wrapped in CTEs, derived tables or scalar subqueries. A count or a
    window over real rows is. Zero-weight arithmetic (``4242 + 0 * SUM(q)``)
    reads a column and passes: this is a shape check, not a figure verifier.
    """
    if depth > _MAX_DEPTH:
        return True
    if isinstance(node, (exp.Subquery, exp.Select, exp.SetOperation)):
        child = _child_scope(scope, node)
        return child is not None and _scope_derived(child, depth + 1)
    if isinstance(node, (exp.Count, exp.Window)):
        return _reads_data(scope, set())
    if isinstance(node, exp.Star):
        return any(_source_derived(src, depth) for src in scope.sources.values())
    if isinstance(node, exp.Column):
        return _column_derived(node, scope, depth)
    return any(_derived(child, scope, depth) for child in node.iter_expressions())


def _scope_derived(scope: Scope, depth: int = 0) -> bool:
    """Some output of every UNION arm is computed from a real table's rows."""
    if depth > _MAX_DEPTH:
        return True
    return all(any(_derived(p, arm, depth) for p in arm.expression.selects) for arm in _arms(scope))


def _null_only(node: Any) -> bool:
    """NULL, or casts of NULL: no figure, though the engine types it INTEGER."""
    leaves = [n for n in node.walk() if isinstance(n, (exp.Literal, exp.Null))]
    return bool(leaves) and all(isinstance(n, exp.Null) for n in leaves)


def _numeric(type_name: str) -> bool:
    text = str(type_name).upper()
    return text in _NUMERIC_TYPES or text.startswith("DECIMAL")


def _root_scope(sql: str) -> Scope | None:
    try:
        tree: Any = parse(sql, read="duckdb")[0]
        if isinstance(tree, exp.Subquery):
            tree = tree.unnest()
        scopes = traverse_scope(tree)
    except Exception:  # noqa: BLE001 - select_shape already refused anything unreadable
        return None
    return scopes[-1] if scopes else None


def literal_figure(sql: str, types: Sequence[Any]) -> bool:
    """True when a UNION arm returns a number nothing computed and no number that was.

    ``types`` are the engine's result column types. ``SELECT sku, 0 AS revenue
    FROM t`` is refused: its only figure is invented. ``SELECT sku, 2024 AS
    year, SUM(q) FROM t`` passes: a label beside a computed figure. An arm
    with a star is not judged here, because its column positions are the
    engine's. Call only after ``select_shape`` passed.
    """
    root = _root_scope(sql)
    if root is None:
        return False
    for arm in _arms(root):
        selects = list(arm.expression.selects)
        if len(selects) != len(types) or any(
            isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star))
            for p in selects
        ):
            continue
        invented = computed = False
        for projection, type_name in zip(selects, types, strict=True):
            if not _numeric(str(type_name)):
                continue
            if _derived(projection, arm):
                computed = True
            elif not _null_only(projection):
                invented = True
        if invented and not computed:
            return True
    return False


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
    if not _scope_derived(scopes[-1]):
        # Reads a table, returns nothing computed from it: SELECT 4242 FROM t,
        # however the literal is wrapped.
        return "hostile_sql:literal_output", frozenset()
    return None, frozenset(real)


__all__ = ["literal_figure", "select_shape"]
