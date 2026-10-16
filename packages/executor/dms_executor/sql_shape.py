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
    """The CTE scope this table reads, or None when it is a real table.

    DuckDB binds a CTE's own name inside its own body to the real table of
    that name, except in the recursive term of a WITH RECURSIVE (the arms
    after the first), which reads the CTE's own rows.
    """
    if table.db:
        return None
    want = _bare(table.name)
    cte = next((c for name, c in scope.cte_sources.items() if _bare(name) == want), None)
    if cte is None:
        return None
    node = table.parent
    while node is not None:
        if isinstance(node, exp.CTE) and _bare(node.alias) == want:
            holder = node.parent
            recursive = isinstance(holder, exp.With) and bool(holder.args.get("recursive"))
            body = node.this
            if not (recursive and isinstance(body, exp.SetOperation)):
                return None
            base = body
            while isinstance(base, exp.SetOperation):
                base = base.this
            inside: Any = table
            while inside is not None and inside is not base:
                inside = inside.parent
            return None if inside is base else cte
        node = node.parent
    return cte


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
#: The lineage walk's budget. Past either, the SQL is not analyzable and is
#: refused (fail closed), never counted as data.
_MAX_DEPTH = 150
_MAX_STEPS = 50_000


class _TooDeep(Exception):
    """The lineage walk ran out of depth or steps."""


class _Walk:
    """One lineage walk: a memo, a step budget, and the real columns if known.

    The memo makes the walk linear in the size of the query: without it a CTE
    chain whose columns each read two columns of the one before is
    exponential. Every node the walk creates is kept alive here, so an id in
    the memo is never reused by another node.
    """

    def __init__(self, real_columns: frozenset[str] | None = None) -> None:
        self.memo: dict[tuple[int, int, frozenset[tuple[int, str]]], bool] = {}
        self.rows_memo: dict[int, bool] = {}
        self.steps = 0
        self.real_columns = real_columns
        self._keep: list[Any] = []

    def tick(self, depth: int) -> None:
        self.steps += 1
        if depth > _MAX_DEPTH or self.steps > _MAX_STEPS:
            raise _TooDeep

    def column(self, name: str) -> exp.Column:
        node = exp.column(name)
        self._keep.append(node)
        return node


Visiting = frozenset[tuple[int, str]]
_NONE: Visiting = frozenset()


def _is_star(projection: Any) -> bool:
    return isinstance(projection, exp.Star) or (
        isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
    )


def _is_recursive(scope: Scope) -> bool:
    """A WITH RECURSIVE CTE: its later arms build on its own rows."""
    cte = scope.expression.parent
    holder = cte.parent if isinstance(cte, exp.CTE) else None
    return isinstance(holder, exp.With) and bool(holder.args.get("recursive"))


def _recursive_base(scope: Scope) -> bool:
    """The base arm of a WITH RECURSIVE body.

    sqlglot resolves a recursive term's reference to its own CTE to this
    scope, so reaching it from another arm means the CTE is reading itself.
    """
    expr = scope.expression
    body = expr
    while isinstance(body.parent, exp.SetOperation):
        body = body.parent
    if body is expr or not isinstance(body.parent, exp.CTE):
        return False
    holder = body.parent.parent
    if not (isinstance(holder, exp.With) and holder.args.get("recursive")):
        return False
    base = body
    while isinstance(base, exp.SetOperation):
        base = base.this
    return base is expr


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


def _selected(scope: Scope) -> list[Any]:
    """Sources in this scope's FROM and JOIN. A CTE that is only in scope is not one.

    A recursive CTE's reference to itself comes back from sqlglot as a table;
    it is the CTE's own rows, not a real table.
    """
    return [_as_source(scope, node, source) for node, source in scope.selected_sources.values()]


def _as_source(scope: Scope, node: Any, source: Any) -> Any:
    """The source a FROM reference binds to, the way DuckDB binds it.

    sqlglot resolves a CTE's own name inside its body to the CTE; DuckDB binds
    the real table there, except in the recursive term. ``_cte_of`` decides.
    """
    if isinstance(node, exp.Table) and _is_name(node):
        cte = _cte_of(scope, node)
        if cte is None:
            return node
        if isinstance(source, exp.Table):
            return cte
    return source


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


def _constant(node: Any) -> bool:
    """No column, star, subquery or COLUMNS() anywhere inside."""
    kinds: tuple[type, ...] = (exp.Column, exp.Star, exp.Subquery, exp.Select, *_COLUMNS_NODE)
    return node.find(*kinds) is None


def _source_derived(w: _Walk, source: Any, depth: int, visiting: Visiting) -> bool:
    """Does a FROM source carry rows of a real table into this scope?"""
    if isinstance(source, exp.Table):
        return _real_table(source)
    if isinstance(source, Scope):
        return _scope_derived(w, source, depth + 1, visiting)
    return False


def _rows_from_data(w: _Walk, scope: Scope, depth: int) -> bool:
    """Every arm's rows come through FROM or JOIN from a real table.

    A WHERE or EXISTS subquery that reads a table does not count, and neither
    does a CTE that is in scope but not selected: the rows are still the
    generator's.
    """
    w.tick(depth)
    key = id(scope)
    if key in w.rows_memo:
        return w.rows_memo[key]
    w.rows_memo[key] = False  # a recursive CTE reaching itself adds no rows of its own
    result = all(
        any(
            _real_table(src) or (isinstance(src, Scope) and _rows_from_data(w, src, depth + 1))
            for src in _selected(arm)
        )
        for arm in _arms(scope)
    )
    w.rows_memo[key] = result
    return result


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


def _column_derived(
    w: _Walk, column: exp.Column, scope: Scope, depth: int, visiting: Visiting
) -> bool:
    w.tick(depth)
    selected = scope.selected_sources
    if column.table:
        picked = [
            _as_source(scope, node, src)
            for name, (node, src) in selected.items()
            if _bare(name) == _bare(column.table)
        ]
    else:
        picked = _selected(scope)
        lateral = _lateral_alias(column, scope)
        # DuckDB binds a real column before an alias of the same name.
        real = w.real_columns is not None and _bare(column.name) in w.real_columns
        if lateral is not None and not (real and any(_real_table(s) for s in picked)):
            return _derived(w, lateral, scope, depth + 1, visiting)
    if isinstance(column.this, exp.Star):
        if not picked and column.table:
            # st.* expands a struct column, not a source.
            return _column_derived(w, w.column(column.table), scope, depth + 1, visiting)
        return any(_source_derived(w, src, depth + 1, visiting) for src in picked)
    for src in picked:
        if isinstance(src, Scope) and src is not scope and _recursive_base(src):
            # The recursive term reading its own CTE: data only when this arm
            # also joins a real table, so rows drive the recursion. A counter
            # or a literal carried through the recursion is not.
            return any(_real_table(s) for s in _selected(scope))
        if isinstance(src, Scope):
            key = (id(src), _bare(column.name))
            if key in visiting:
                # A recursive CTE reading its own column: data only when this
                # arm also joins a real table, so rows drive the recursion.
                return any(_real_table(s) for s in _selected(scope))
            # Only a recursive CTE can reach itself; keeping other hops out of
            # the visiting set keeps the memo effective on long CTE chains.
            inner = visiting | {key} if _is_recursive(src) else visiting
            named = _named_output(src, column.name)
            if named is not None:
                combine = any if _is_recursive(src) else all
                return combine(_derived(w, p, arm, depth + 1, inner) for p, arm in named)
            if len(_arms(src)) == 1 and any(_is_star(p) for p in src.expression.selects):
                # A star passes the column through from that scope's own sources.
                return _column_derived(w, w.column(column.name), src, depth + 1, inner)
        elif isinstance(src, exp.Table) and _is_generator(src):
            if _bare(column.name) in _generator_names(src):
                return False
    if any(_real_table(src) for src in picked):
        return True
    if not picked and scope.parent is not None:
        # A correlated reference to the enclosing query.
        return _column_derived(w, column, scope.parent, depth + 1, visiting)
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


def _derived(w: _Walk, node: Any, scope: Scope, depth: int, visiting: Visiting) -> bool:
    """True when this value depends on rows of a real table.

    A literal, or functions and arithmetic of literals, does not, however deep
    it is wrapped in CTEs, derived tables or scalar subqueries. COUNT over
    real rows does. SUM of a constant does (SUM(1) counts rows); SUM of a
    column is that column's lineage. MIN, MAX, AVG and FIRST of a constant do
    not. A window's value is its function's, except ranking functions, which
    number the rows. Not judged here (needs a figure verifier): a constant
    beside or multiplied into a computed value.
    """
    w.tick(depth)
    key = (id(node), id(scope), visiting)
    cached = w.memo.get(key)
    if cached is not None:
        return cached
    result = _derived_uncached(w, node, scope, depth, visiting)
    w.memo[key] = result
    return result


def _derived_uncached(w: _Walk, node: Any, scope: Scope, depth: int, visiting: Visiting) -> bool:
    if isinstance(node, (exp.Subquery, exp.Select, exp.SetOperation)):
        child = _child_scope(scope, node)
        return child is not None and _scope_derived(w, child, depth + 1, visiting)
    if isinstance(node, exp.Count):
        return _rows_from_data(w, scope, depth + 1)
    if isinstance(node, exp.Sum):
        arg = node.this
        if arg is None or _constant(arg):
            return _rows_from_data(w, scope, depth + 1)
        return _derived(w, arg, scope, depth + 1, visiting)
    if isinstance(node, exp.Window):
        fn = node.this
        if isinstance(fn, exp.Ntile) or not list(fn.iter_expressions()):
            return _rows_from_data(w, scope, depth + 1)
        return _derived(w, fn, scope, depth + 1, visiting)
    if _COLUMNS_NODE and isinstance(node, _COLUMNS_NODE):
        return _rows_from_data(w, scope, depth + 1)
    if isinstance(node, exp.Star):
        return any(_source_derived(w, src, depth + 1, visiting) for src in _selected(scope))
    if isinstance(node, exp.Column):
        return _column_derived(w, node, scope, depth + 1, visiting)
    return any(_derived(w, child, scope, depth + 1, visiting) for child in node.iter_expressions())


def _scope_derived(w: _Walk, scope: Scope, depth: int, visiting: Visiting) -> bool:
    """Some output of every UNION arm is computed from a real table's rows."""
    w.tick(depth)
    return all(
        any(_derived(w, p, arm, depth + 1, visiting) for p in arm.expression.selects)
        for arm in _arms(scope)
    )


def _null_only(node: Any) -> bool:
    """NULL, or casts of NULL: no figure, though the engine types it INTEGER."""
    leaves = [n for n in node.walk() if isinstance(n, (exp.Literal, exp.Null))]
    return bool(leaves) and all(isinstance(n, exp.Null) for n in leaves)


def _has_number(node: Any) -> bool:
    return any(isinstance(n, exp.Literal) and n.is_number for n in node.walk())


def _numeric(type_name: str) -> bool:
    text = str(type_name).upper()
    return text in _NUMERIC_TYPES or text.startswith("DECIMAL")


def _root_scope(sql: str) -> Scope | None:
    try:
        tree: Any = parse(sql, read="duckdb")[0]
        if isinstance(tree, exp.Subquery):
            tree = tree.unnest()
        scopes = traverse_scope(tree)
    except Exception:  # noqa: BLE001 - the caller fails closed on None
        return None
    return scopes[-1] if scopes else None


def _judge(w: _Walk, arm: Scope, pairs: Sequence[tuple[Any, bool]]) -> tuple[bool, bool]:
    """(invented, computed) over (projection, is numeric) pairs."""
    invented = computed = False
    for projection, numeric in pairs:
        if not numeric:
            continue
        if _derived(w, projection, arm, 0, _NONE):
            computed = True
        elif not _null_only(projection):
            invented = True
    return invented, computed


def _by_name(w: _Walk, arm: Scope, numeric_of: dict[str, bool]) -> bool:
    """Judge an arm by result column name: UNION BY NAME, or an arm with a star.

    A column the star brings is traced to its source projection, so a star
    over a CTE that states a literal is judged like the literal itself. An
    explicit output the engine named differently (an unaliased expression) is
    numeric when it states a number.
    """
    explicit: dict[str, Any] = {}
    star = False
    for projection in arm.expression.selects:
        if _is_star(projection):
            star = True
            replaced = projection.args.get("replace") if isinstance(projection, exp.Star) else None
            for item in replaced or []:
                explicit[_bare(item.alias_or_name)] = item
            continue
        explicit[_bare(projection.alias_or_name)] = projection
    pairs = [
        (p, numeric_of[name] if name in numeric_of else _has_number(p))
        for name, p in explicit.items()
    ]
    invented, computed = _judge(w, arm, pairs)
    if star:
        for name, numeric in numeric_of.items():
            if not numeric or name in explicit:
                continue
            if _column_derived(w, w.column(name), arm, 0, _NONE):
                computed = True
            else:
                invented = True
    return invented and not computed


def literal_figure_reason(
    sql: str,
    types: Sequence[Any],
    names: Sequence[str] = (),
    real_columns: frozenset[str] | None = None,
) -> str | None:
    """Refusal when a UNION arm returns a number nothing computed and no number that was.

    ``types`` and ``names`` are the engine's result columns; ``real_columns``
    are the lowercase column names of the real tables the query reads, used
    where DuckDB binds a real column before an alias. ``SELECT sku, 0 AS
    revenue FROM t`` is refused: its only figure is invented. ``SELECT sku,
    2024 AS year, SUM(q) FROM t`` passes: a label beside a computed figure.
    Arms are matched to result columns by position, or by name for UNION BY
    NAME and for an arm with a star. Anything the walk cannot trace within its
    budget fails closed. Call only after ``select_shape`` passed.
    """
    root = _root_scope(sql)
    if root is None:
        return "hostile_sql:sql_not_analyzable"
    numeric = [_numeric(str(t)) for t in types]
    numeric_of: dict[str, bool] = {}
    for name, is_numeric in zip((_bare(n) for n in names), numeric, strict=False):
        # A repeated result name is numeric if any column under it is.
        numeric_of[name] = numeric_of.get(name, False) or is_numeric
    expression = root.expression
    by_name = isinstance(expression, exp.SetOperation) and bool(expression.args.get("by_name"))
    w = _Walk(real_columns)
    try:
        for index, arm in enumerate(_arms(root)):
            selects = list(arm.expression.selects)
            star = any(_is_star(p) for p in selects)
            if by_name or star:
                if not names or (star and index > 0 and not by_name):
                    continue
                refused = _by_name(w, arm, numeric_of)
            elif len(selects) == len(numeric):
                invented, computed = _judge(w, arm, list(zip(selects, numeric, strict=True)))
                refused = invented and not computed
            else:
                continue
            if refused:
                return "hostile_sql:literal_output"
    except (RecursionError, _TooDeep):
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
        if not _scope_derived(_Walk(), scopes[-1], 0, _NONE):
            # Reads a table, returns nothing computed from it: SELECT 4242 FROM
            # t, however the literal is wrapped.
            return "hostile_sql:literal_output", frozenset()
    except (RecursionError, _TooDeep):
        # Nested past what the walk can trace (hundreds of UNION arms or of
        # terms in one expression): fail closed rather than crash the ask.
        return "hostile_sql:sql_not_analyzable", frozenset()
    return None, frozenset(real)


__all__ = ["literal_figure_reason", "select_shape"]
