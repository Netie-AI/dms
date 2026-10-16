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
  FROM t`` is refused however the literal is wrapped. ``literal_figure_reason`` adds
  the engine's column types after EXPLAIN: an arm whose numbers are all
  literals is refused, a literal label beside a computed figure is not.
- Not covered, named so nobody reads more into it (SHAPE_RESIDUAL in
  tests/test_safe_01_one_gate.py): a literal number beside a computed one
  (``SELECT sku, 4242 AS revenue, qty``), a constant multiplied into or
  added to a computed value (``4242 + 0 * SUM(q)``, ``SUM(4242)``), a
  constant chosen by a column (``CASE category WHEN 'x' THEN 5000 ELSE 4242
  END``), a function of a column that ignores its value (``ARG_MAX(4242,
  q)``), and figures typed as text (``'RM 4242'``). Those need a figure
  verifier.

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


def _is_star(projection: Any) -> bool:
    return isinstance(projection, exp.Star) or (
        isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
    )


def _is_recursive(scope: Scope) -> bool:
    """A WITH RECURSIVE CTE: its later arms build on its own rows."""
    cte = scope.expression.parent
    holder = cte.parent if isinstance(cte, exp.CTE) else None
    return isinstance(holder, exp.With) and bool(holder.args.get("recursive"))


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


def _generator_names(table: exp.Table) -> set[str]:
    """Columns a generator in FROM exposes: its column list, else its own name."""
    listed = {_bare(c) for c in table.alias_column_names}
    if listed:
        return listed
    if isinstance(table.this, exp.GenerateSeries):
        # sqlglot reads range() and generate_series() as one node; DuckDB names
        # the column after the function that was written.
        return {"range", "generate_series"}
    return {_bare(getattr(table.this, "name", ""))}


def _source_derived(source: Any, depth: int) -> bool:
    """Does a FROM source carry rows of a real table into this scope?"""
    if isinstance(source, exp.Table):
        return _real_table(source)
    if isinstance(source, Scope):
        return _scope_derived(source, depth + 1)
    return False


def _rows_from_data(scope: Scope, depth: int = 0) -> bool:
    """Every arm's rows come through FROM or JOIN from a real table.

    A WHERE or EXISTS subquery that reads a table does not count: the rows
    are still the generator's.
    """
    if depth > _MAX_DEPTH:
        return True
    for arm in _arms(scope):
        if not any(
            _real_table(src) or (isinstance(src, Scope) and _rows_from_data(src, depth + 1))
            for src in arm.sources.values()
        ):
            return False
    return True


def _lateral_alias(column: exp.Column, scope: Scope) -> Any | None:
    """The expression an unqualified name means as an alias of another output."""
    select = scope.expression
    if not isinstance(select, exp.Select):
        return None
    holder: Any = column
    while holder is not None and holder.parent is not select:
        holder = holder.parent
    if holder is None or not any(holder is p for p in select.expressions):
        return None
    want = _bare(column.name)
    for projection in select.expressions:
        if projection is holder or not isinstance(projection, exp.Alias):
            continue
        if _bare(projection.alias) == want:
            return projection.this
    return None


def _column_derived(column: exp.Column, scope: Scope, depth: int) -> bool:
    if depth > _MAX_DEPTH:
        return True
    sources = scope.sources
    if column.table:
        picked = [v for k, v in sources.items() if _bare(k) == _bare(column.table)]
    else:
        picked = list(sources.values())
        lateral = _lateral_alias(column, scope)
        if lateral is not None:
            return _derived(lateral, scope, depth + 1)
    if isinstance(column.this, exp.Star):
        if not picked and column.table:
            # st.* expands a struct column, not a source.
            return _column_derived(exp.column(column.table), scope, depth + 1)
        return any(_source_derived(src, depth) for src in picked)
    for src in picked:
        if isinstance(src, Scope):
            named = _named_output(src, column.name)
            if named is not None:
                combine = any if _is_recursive(src) else all
                return combine(_derived(p, arm, depth + 1) for p, arm in named)
            if len(_arms(src)) == 1 and any(_is_star(p) for p in src.expression.selects):
                # A star passes the column through from that scope's own sources.
                return _column_derived(exp.column(column.name), src, depth + 1)
        elif isinstance(src, exp.Table) and _is_generator(src):
            if _bare(column.name) in _generator_names(src):
                return False
    if any(_real_table(src) for src in picked):
        return True
    if not picked and scope.parent is not None:
        # A correlated reference to the enclosing query.
        return _column_derived(column, scope.parent, depth + 1)
    # Bound by the engine but not traceable here. A generator column is not
    # data; anything else counts as data rather than refuse it.
    return not any(isinstance(src, exp.Table) and _is_generator(src) for src in picked)


def _child_scope(scope: Scope, node: Any) -> Scope | None:
    inner = node.this if isinstance(node, exp.Subquery) else node
    for child in scope.subquery_scopes:
        if child.expression is inner:
            return child
    return None


#: COLUMNS('regex') and COLUMNS([...]) select table columns by name.
_COLUMNS_NODE: tuple[type, ...] = tuple(
    n for n in (getattr(exp, "Columns", None),) if isinstance(n, type)
)


def _derived(node: Any, scope: Scope, depth: int = 0) -> bool:
    """True when this value depends on rows of a real table.

    A literal, or functions and arithmetic of literals, does not, however deep
    it is wrapped in CTEs, derived tables or scalar subqueries. A COUNT or a
    SUM over real rows does, even SUM(1), because it depends on how many rows
    there are; MIN, MAX, AVG and FIRST of a constant do not. A window's value
    is its function's: its ORDER BY picks rows but states no figure, except
    for ranking functions, which number the rows. Not judged here (needs a
    figure verifier): a constant beside or multiplied into a computed value.
    """
    if depth > _MAX_DEPTH:
        return True
    if isinstance(node, (exp.Subquery, exp.Select, exp.SetOperation)):
        child = _child_scope(scope, node)
        return child is not None and _scope_derived(child, depth + 1)
    if isinstance(node, (exp.Count, exp.Sum)) and _rows_from_data(scope):
        return True
    if isinstance(node, exp.Window):
        fn = node.this
        if isinstance(fn, exp.Ntile) or not list(fn.iter_expressions()):
            return _rows_from_data(scope)
        return _derived(fn, scope, depth)
    if _COLUMNS_NODE and isinstance(node, _COLUMNS_NODE):
        return _rows_from_data(scope)
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


def _judge(arm: Scope, pairs: Sequence[tuple[Any, bool]], star_figures: bool) -> bool:
    """An invented figure and no computed one, over (projection, is numeric) pairs."""
    invented = False
    computed = star_figures
    for projection, numeric in pairs:
        if not numeric:
            continue
        if _derived(projection, arm):
            computed = True
        elif not _null_only(projection):
            invented = True
    return invented and not computed


def _by_name(arm: Scope, numeric_of: dict[str, bool | None]) -> bool:
    """Judge an arm by result column name: UNION BY NAME, or an arm with a star."""
    explicit: dict[str, Any] = {}
    star = False
    for projection in arm.expression.selects:
        if _is_star(projection):
            star = True
            for replaced in projection.args.get("replace") or []:
                explicit[_bare(replaced.alias_or_name)] = replaced
            continue
        explicit[_bare(projection.alias_or_name)] = projection
    pairs = [(p, numeric_of.get(n) is True) for n, p in explicit.items()]
    # Numeric columns a star brings are the source's own figures.
    star_figures = star and any(
        numeric is True and name not in explicit for name, numeric in numeric_of.items()
    )
    return _judge(arm, pairs, star_figures)


def literal_figure_reason(sql: str, types: Sequence[Any], names: Sequence[str] = ()) -> str | None:
    """Refusal when a UNION arm returns a number nothing computed and no number that was.

    ``types`` and ``names`` are the engine's result columns. ``SELECT sku, 0
    AS revenue FROM t`` is refused: its only figure is invented. ``SELECT
    sku, 2024 AS year, SUM(q) FROM t`` passes: a label beside a computed
    figure. Arms are matched to result columns by position, or by name for
    UNION BY NAME and for an arm with a star. SQL too deep to trace fails
    closed. Call only after ``select_shape`` passed.
    """
    root = _root_scope(sql)
    if root is None:
        return None
    numeric = [_numeric(str(t)) for t in types]
    numeric_of: dict[str, bool | None] = {}
    for name, is_numeric in zip((_bare(n) for n in names), numeric, strict=False):
        # A repeated result name is ambiguous: never matched.
        numeric_of[name] = None if name in numeric_of else is_numeric
    expression = root.expression
    by_name = isinstance(expression, exp.SetOperation) and bool(expression.args.get("by_name"))
    try:
        for index, arm in enumerate(_arms(root)):
            selects = list(arm.expression.selects)
            star = any(_is_star(p) for p in selects)
            if by_name or star:
                if not names or (star and index > 0 and not by_name):
                    continue
                refused = _by_name(arm, numeric_of)
            elif len(selects) == len(numeric):
                refused = _judge(arm, list(zip(selects, numeric, strict=True)), False)
            else:
                continue
            if refused:
                return "hostile_sql:literal_output"
    except RecursionError:
        return "hostile_sql:sql_not_analyzable"
    return None


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
    try:
        if not _reads_data(scopes[-1], set()):
            return "hostile_sql:no_data_source", frozenset()
        if not _scope_derived(scopes[-1]):
            # Reads a table, returns nothing computed from it: SELECT 4242 FROM
            # t, however the literal is wrapped.
            return "hostile_sql:literal_output", frozenset()
    except RecursionError:
        # Nested past what the walk can trace (hundreds of UNION arms or of
        # terms in one expression): fail closed rather than crash the ask.
        return "hostile_sql:sql_not_analyzable", frozenset()
    return None, frozenset(real)


__all__ = ["literal_figure_reason", "select_shape"]
