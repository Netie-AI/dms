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
- A table position, LATERAL included, may hold a name or a generator (range,
  generate_series, unnest). A file reader, a quoted path, a URL or any other
  table function is refused.
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
  END``), a constant a recursive CTE carries beside a counted column, and
  figures typed as text (``'RM 4242'``). Those need a figure verifier.

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


#: DuckDB folds identifier case for ASCII letters only and never trims.
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _bare(name: str) -> str:
    """An identifier as DuckDB compares it: ASCII letters folded, nothing else.

    Python's lower() also folds non-ASCII letters (the Kelvin sign becomes k,
    É becomes é) and strip() drops whitespace a quoted name keeps; either
    would let a CTE named "alerts " stand for the real table alerts.
    """
    return str(name or "").translate(_ASCII_LOWER)


def _is_name(table: exp.Table) -> bool:
    return isinstance(table.this, exp.Identifier)


def _cte_of(scope: Scope, table: exp.Table) -> Scope | None:
    """The CTE scope this table reads, or None when it is a real table.

    DuckDB binds a CTE's own name inside its own body to the real table of
    that name, except in the recursive term of a WITH RECURSIVE (the arm
    right of the last UNION; everything left of it is the base), which reads
    the CTE's own rows. Those rows start as the base's, so the recursive term
    is traced through the base: a value it computes from base data is data,
    and a literal or counter it adds is not.
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
            if not (recursive and _recurses(body)):
                return None
            if not _within(table, body.expression):
                return None
            # sqlglot hands back a fresh scope for the base that it never
            # walked (no UNION arms when the base is itself a UNION); use the
            # walked one.
            walked: Scope | None = scope
            while walked is not None and walked.expression is not body:
                walked = walked.parent
            if walked is not None and len(walked.union_scopes) == 2:
                return walked.union_scopes[0]
            return cte
        node = node.parent
    return cte


def _recurses(body: Any) -> bool:
    """A recursive CTE body: a plain UNION [ALL] of a base and a recursive term.

    Under EXCEPT, INTERSECT or UNION BY NAME, DuckDB binds the CTE's own name
    to the table of that name in every arm.
    """
    return isinstance(body, exp.Union) and not body.args.get("by_name")


def _within(node: Any, ancestor: Any) -> bool:
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False


def _cte_reference(table: exp.Table) -> bool:
    """True when a table reference names a CTE, decided from the syntax tree.

    The grant check uses this, not sqlglot's scopes: a subquery inside extra
    parentheses or inside unnest() gets no scope, but its tables are still
    read. A WITH is visible to its own query body and, inside a CTE's body,
    only the CTEs defined before it are. A CTE's own name inside its body is
    the real table, except in the recursive term of a WITH RECURSIVE. Any
    reference this does not resolve counts as a real table, which can only
    ask the grant check about more tables.
    """
    if table.db or table.catalog:
        return False
    want = _bare(table.name)
    child: Any = table
    node = table.parent
    while node is not None:
        holder = node.args.get("with") if isinstance(node, exp.Expression) else None
        if isinstance(holder, exp.With) and child is not holder:
            if any(_bare(c.alias) == want for c in holder.expressions):
                return True
        if isinstance(node, exp.CTE) and isinstance(node.parent, exp.With):
            outer = node.parent
            if _bare(node.alias) == want:
                body = node.this
                recursive = bool(outer.args.get("recursive"))
                if recursive and _recurses(body):
                    return _within(table, body.expression)
                return False
            earlier = []
            for c in outer.expressions:
                if c is node:
                    break
                earlier.append(_bare(c.alias))
            if want in earlier:
                return True
            # Later CTEs of this WITH are not visible here; neither is the WITH
            # as a body would see it. Continue above the query that owns it.
            child = outer
            node = outer.parent
            continue
        child = node
        node = node.parent
    return False


def _real_tables(tree: Any) -> set[str]:
    """Every real table the statement reads, from every table node in the tree."""
    return {
        _bare(t.name) for t in tree.find_all(exp.Table) if _is_name(t) and not _cte_reference(t)
    }


def _hoist_with(tree: Any) -> Any:
    """``WITH x AS (...) (SELECT ...)``: move the WITH onto the inner query.

    sqlglot hangs that WITH on the outer parentheses, so unwrapping them
    would drop the CTE bodies from the walk.
    """
    while isinstance(tree, exp.Subquery):
        holder = tree.args.get("with")
        inner = tree.this
        if holder is not None and isinstance(inner, exp.Query):
            if inner.args.get("with") is not None:
                return tree
            tree.set("with", None)
            inner.set("with", holder)
        tree = inner
    return tree


def _is_generator(table: exp.Table) -> bool:
    inner = table.this
    if isinstance(inner, exp.GenerateSeries):
        return True
    if isinstance(inner, exp.Anonymous):
        return _bare(inner.name) in _GENERATORS
    return False


def _lateral_reader(lateral: exp.Lateral) -> bool:
    """A table function reached through LATERAL that is not a generator.

    sqlglot puts ``JOIN LATERAL f(...)`` and ``, LATERAL f(...)`` on a Lateral
    node, not a Table, so the table-position check does not see it.
    """
    inner = lateral.this
    if isinstance(inner, (exp.Subquery, exp.Select, exp.Unnest, exp.GenerateSeries)):
        return False
    return not (isinstance(inner, exp.Anonymous) and _bare(inner.name) in _GENERATORS)


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


def _reads_data(scope: Scope, seen: dict[int, bool]) -> bool:
    """True when this query's output is built from at least one real table.

    ``seen`` memoizes per scope, so a CTE read by several UNION arms is judged
    once and counted every time; a scope reached again while it is still
    being judged (a recursive CTE) adds nothing of its own.
    """
    key = id(scope)
    if key in seen:
        return seen[key]
    seen[key] = False
    if scope.union_scopes:
        # Every branch must read a table: a literal branch adds an invented row.
        result = all(_reads_data(s, seen) for s in scope.union_scopes)
    else:
        result = False
        for table in scope.tables:
            if not _is_name(table):
                continue
            cte = _cte_of(scope, table)
            if cte is None or _reads_data(cte, seen):
                result = True
                break
        if not result:
            children: list[Scope] = [
                *scope.derived_table_scopes,
                *scope.subquery_scopes,
                *scope.udtf_scopes,
            ]
            result = any(_reads_data(c, seen) for c in children)
    seen[key] = result
    return result


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
        # DuckDB 1.5's arbitrary-precision integer (VARINT before).
        "BIGNUM",
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
    recursive = isinstance(holder, exp.With) and bool(holder.args.get("recursive"))
    return recursive and _recurses(scope.expression)


def _recursion_base(scope: Scope) -> bool:
    """The base of a WITH RECURSIVE body, as its recursive term reads it."""
    parent = scope.parent
    return (
        parent is not None
        and _is_recursive(parent)
        and len(parent.union_scopes) == 2
        and parent.union_scopes[0] is scope
    )


def _named_output(scope: Scope, name: str) -> list[Any] | None:
    """The projection(s) a source scope exposes under ``name``, one per UNION arm.

    A column list on the CTE or derived table (``x(v)``, ``s(v)``) renames by
    position. None when the scope exposes no such name (a star, or unknown).
    """
    want = _bare(name)
    arms = _arms(scope)
    renamed = _renamed(scope)
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


def _renamed(scope: Scope) -> list[str]:
    """The column list a CTE, derived table or LATERAL puts on this scope's outputs."""
    parent = scope.expression.parent
    while isinstance(parent, exp.SetOperation):
        # A recursive CTE's base is renamed by the CTE's column list.
        parent = parent.parent
    while (
        isinstance(parent, exp.Subquery)
        and not parent.alias_column_names
        and isinstance(parent.parent, exp.Lateral)
    ):
        # LATERAL (SELECT 4242) l(x): the list sits on the LATERAL.
        parent = parent.parent
    return [_bare(c) for c in getattr(parent, "alias_column_names", None) or []]


def _values_names(values: exp.Values) -> list[str]:
    """Columns a VALUES list exposes: its column list, then col0, col1, ..."""
    rows = values.expressions
    first = rows[0] if rows else None
    width = len(first.expressions) if isinstance(first, exp.Tuple) else int(first is not None)
    listed = [_bare(c) for c in values.alias_column_names][:width]
    return listed + [f"col{i}" for i in range(len(listed), width)]


def _star_column(scope: Scope, name: str) -> str | None:
    """The name an output of this SELECT * scope has in its own source, or None.

    A column list on the CTE or derived table renames by position. Over one
    VALUES list or one derived query the positions are known, so a renamed
    output maps to the source's own column and a name the scope does not
    expose is None. Elsewhere the name passes through unchanged.
    """
    want = _bare(name)
    renamed = _renamed(scope)
    sources = _selected(scope)
    if len(sources) != 1 or not isinstance(sources[0], Scope):
        return name
    source = sources[0]
    if isinstance(source.expression, exp.Values):
        own = _values_names(source.expression)
    elif len(_arms(source)) == 1 and not any(_is_star(p) for p in source.expression.selects):
        own = _renamed(source) or [_bare(p.alias_or_name) for p in source.expression.selects]
    else:
        return name
    shown = renamed[: len(own)] + own[len(renamed) :]
    if shown.count(want) != 1:
        return None
    return own[shown.index(want)]


def _arms(scope: Scope) -> list[Scope]:
    """The SELECT scopes whose rows this scope returns, nested UNIONs flattened."""
    if not scope.union_scopes:
        return [scope]
    return [arm for child in scope.union_scopes for arm in _arms(child)]


def _selected_items(scope: Scope) -> list[tuple[str, Any, Any]]:
    """(name, reference, source) for each FROM and JOIN entry of this scope.

    sqlglot refuses a name used twice in one FROM (a self-join without
    aliases); DuckDB accepts some of those, so they are read entry by entry.
    """
    try:
        return [(name, node, src) for name, (node, src) in scope.selected_sources.items()]
    except Exception:  # noqa: BLE001 - sqlglot OptimizeError on a repeated alias
        select = scope.expression
        nodes: list[Any] = []
        if isinstance(select, exp.Select):
            frm = select.args.get("from")
            if frm is not None:
                nodes.append(frm.this)
            nodes.extend(j.this for j in select.args.get("joins") or [])
        derived = {id(d.expression): d for d in scope.derived_table_scopes}
        out: list[tuple[str, Any, Any]] = []
        for node in nodes:
            src: Any = node
            if isinstance(node, exp.Table):
                found = scope.sources.get(node.alias_or_name)
                src = found if isinstance(found, Scope) else node
            elif isinstance(node, exp.Subquery):
                src = derived.get(id(node.this), node)
            out.append((node.alias_or_name, node, src))
        return out


def _selected(scope: Scope) -> list[Any]:
    """Sources in this scope's FROM and JOIN. A CTE that is only in scope is not one.

    A recursive CTE's reference to itself comes back from sqlglot as a table;
    it is the CTE's own rows, not a real table.
    """
    return [_as_source(scope, node, source) for _, node, source in _selected_items(scope)]


def _as_source(scope: Scope, node: Any, source: Any) -> Any:
    """The source a FROM reference binds to, the way DuckDB binds it.

    sqlglot resolves a CTE's own name inside its body to the CTE; DuckDB binds
    the real table there, except in the recursive term. ``_cte_of`` decides.
    """
    if isinstance(node, exp.Table) and _is_name(node):
        cte = _cte_of(scope, node)
        return node if cte is None else cte
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


def _lateral_query(source: Any) -> Any:
    """LATERAL (SELECT ...) read as the query inside it, like a derived table."""
    if not (isinstance(source, Scope) and isinstance(source.expression, exp.Lateral)):
        return source
    inner = source.expression.this
    if not isinstance(inner, exp.Subquery):
        return source
    for child in (*source.subquery_scopes, *source.derived_table_scopes):
        if child.expression is inner.this:
            return child
    return source


def _udtf_call(source: Any) -> Any | None:
    """The unnest or generator call behind a FROM or LATERAL source, if that is what it is."""
    if not isinstance(source, Scope):
        return None
    node = source.expression
    call = node.this if isinstance(node, exp.Lateral) else node
    if isinstance(call, (exp.Unnest, exp.GenerateSeries)):
        return call
    if isinstance(call, exp.Anonymous) and _bare(call.name) in _GENERATORS:
        return call
    return None


def _udtf_names(node: Any, call: Any) -> set[str]:
    """Columns an unnest or generator source exposes: its column list, else its own name."""
    alias = node.args.get("alias")
    listed = {_bare(c.name) for c in alias.columns} if isinstance(alias, exp.TableAlias) else set()
    if listed:
        return listed
    if isinstance(call, exp.Unnest):
        return {"unnest"}
    if isinstance(call, exp.GenerateSeries):
        return {"range", "generate_series"}
    return {_bare(call.name)}


def _source_has(w: _Walk, name: str, picked: Sequence[Any]) -> bool:
    """Whether a FROM source may expose a column of this name.

    A CTE or derived table says so by its outputs. A real table does when the
    catalog's column names are known; before EXPLAIN they are not, and a real
    table is taken to have it (the check after EXPLAIN knows).
    """
    want = _bare(name)
    for src in picked:
        if isinstance(src, Scope) and _named_output(src, want) is not None:
            return True
        if _real_table(src) and (w.real_columns is None or want in w.real_columns):
            return True
    return False


def _column_derived(
    w: _Walk, column: exp.Column, scope: Scope, depth: int, visiting: Visiting
) -> bool:
    w.tick(depth)
    items = _selected_items(scope)
    if column.table:
        picked = [
            _as_source(scope, node, src)
            for name, node, src in items
            if _bare(name) == _bare(column.table)
        ]
        if not picked:
            # A correlated reference to an enclosing query, else a struct
            # field (s.a, st.*) of a column named like the qualifier.
            parent = scope.parent
            while parent is not None:
                if any(_bare(n) == _bare(column.table) for n, _, _ in _selected_items(parent)):
                    return _column_derived(w, column, parent, depth + 1, visiting)
                parent = parent.parent
            return _column_derived(w, w.column(column.table), scope, depth + 1, visiting)
    else:
        picked = [_as_source(scope, node, src) for _, node, src in items]
        lateral = _lateral_alias(column, scope)
        # DuckDB binds a source column before an alias of the same name.
        if lateral is not None and not _source_has(w, column.name, picked):
            return _derived(w, lateral, scope, depth + 1, visiting)
    if isinstance(column.this, exp.Star):
        return any(_source_derived(w, src, depth + 1, visiting) for src in picked)
    for src in picked:
        src = _lateral_query(src)
        if isinstance(src, Scope) and isinstance(src.expression, exp.Values):
            names = _values_names(src.expression)
            if names.count(_bare(column.name)) != 1:
                # Not a column of this VALUES list: a lookup joined beside a table.
                continue
            # VALUES (4242) states its rows; only a subquery in that column reads data.
            index = names.index(_bare(column.name))
            return _values_column_derived(w, src, index, scope, depth + 1, visiting)
        udtf = _udtf_call(src)
        if udtf is not None:
            if _bare(column.name) not in _udtf_names(src.expression, udtf):
                continue
            if isinstance(udtf, exp.Unnest):
                # unnest([q]) carries q: data when what it unnests is.
                return any(_derived(w, e, scope, depth + 1, visiting) for e in udtf.expressions)
            # A generator's own column (range, generate_series) is not data.
            return False
        if (
            isinstance(src, Scope)
            and _recursion_base(src)
            and any(_real_table(s) for s in _selected(scope))
        ):
            # The recursive term reading its own rows while it joins a real
            # table: the rows drive the recursion, so a depth it counts is data.
            return True
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
                # A recursive CTE's column is data when one arm makes it so: a
                # depth starts as a literal 0 and counts joined real rows.
                combine = any if _is_recursive(src) else all
                return combine(_derived(w, p, arm, depth + 1, inner) for p, arm in named)
            if len(_arms(src)) == 1 and any(_is_star(p) for p in src.expression.selects):
                # A star passes the column through from that scope's own sources,
                # under the name it has there.
                own = _star_column(src, column.name)
                if own is None:
                    continue
                return _column_derived(w, w.column(own), src, depth + 1, inner)
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


def _values_column_derived(
    w: _Walk, values: Scope, index: int, scope: Scope, depth: int, visiting: Visiting
) -> bool:
    """A VALUES column is data when a cell in it reads a real table.

    sqlglot files a cell's subquery under the VALUES scope or a scope that
    selects from it. A subquery found under neither is not traced, and the
    cell counts as stated, not computed.
    """
    for row in values.expression.expressions:
        cells = row.expressions if isinstance(row, exp.Tuple) else [row]
        if index >= len(cells) or cells[index].find(exp.Select) is None:
            continue
        selects = list(cells[index].find_all(exp.Select))
        owner: Scope | None = values
        while owner is not None and not any(
            c.expression is q for c in owner.subquery_scopes for q in selects
        ):
            owner = scope if owner is values else owner.parent
        if owner is not None and _derived(w, cells[index], owner, depth + 1, visiting):
            return True
    return False


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
        # A sum over real rows depends on how many there are, whatever it adds
        # up: SUM(1), and SUM(flag) with a literal 1 flag, count rows.
        arg = node.this
        if isinstance(arg, exp.Distinct):
            # SUM(DISTINCT 4242) is 4242 however many rows there are.
            return any(_derived(w, e, scope, depth + 1, visiting) for e in arg.expressions)
        if _rows_from_data(w, scope, depth + 1):
            return True
        return arg is not None and _derived(w, arg, scope, depth + 1, visiting)
    if isinstance(node, (exp.ArgMax, exp.ArgMin)):
        # ARG_MAX(4242, q), MAX_BY, MIN_BY: q picks a row, the figure is 4242.
        return _derived(w, node.this, scope, depth + 1, visiting)
    if isinstance(node, exp.Comprehension):
        # [x * 4242 FOR x IN list]: x is the list's element, not a table column.
        var = _bare(getattr(node.expression, "name", ""))
        parts = [node.args.get("iterator"), node.args.get("condition")]
        parts += [
            n
            for n in node.this.find_all(exp.Column, exp.Subquery, exp.AggFunc)
            if not (isinstance(n, exp.Column) and not n.table and _bare(n.name) == var)
        ]
        return any(p is not None and _derived(w, p, scope, depth + 1, visiting) for p in parts)
    if isinstance(node, exp.Filter):
        # MAX(4242) FILTER (WHERE q > 0): the filter picks rows, states no figure.
        return _derived(w, node.this, scope, depth + 1, visiting)
    if isinstance(node, exp.Order) and node.this is not None:
        # FIRST(4242 ORDER BY q): the order picks a row, states no figure.
        return _derived(w, node.this, scope, depth + 1, visiting)
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


def _unparen(tree: Any) -> Any:
    """((SELECT ...)) as (SELECT ...): sqlglot scopes one level of parentheses."""
    for node in list(tree.find_all(exp.Subquery)):
        inner = node.this
        while isinstance(inner, exp.Subquery) and [k for k, v in inner.args.items() if v] == [
            "this"
        ]:
            inner = inner.this
        if inner is not node.this:
            node.set("this", inner)
    return tree


def _root_scope(sql: str) -> Scope | None:
    try:
        tree: Any = _unparen(_hoist_with(parse(sql, read="duckdb")[0]))
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
    for a refusal decided before the tables are known (more than one
    statement, not a SELECT, a reader, unparseable). A query that reads no
    data or returns only literals keeps its tables, so the grant check still
    answers first: an ungranted read is reported as one.
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
    tree: Any = _hoist_with(trees[0])
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return "hostile_sql:statement_not_allowed", frozenset()
    if tree.find(*_FORBIDDEN) is not None:
        return "hostile_sql:statement_not_allowed", frozenset()
    if any(
        not ident.quoted and str(ident.this).upper() == "TABLE"
        for ident in tree.find_all(exp.Identifier)
    ):
        # DuckDB's TABLE <name> shorthand reads the table; sqlglot parses it as
        # a column named TABLE, so no table node would carry the name.
        return "hostile_sql:statement_not_allowed", frozenset()
    if any(not isinstance(cte.this, exp.Query) for cte in tree.find_all(exp.CTE)):
        return "hostile_sql:sql_not_analyzable", frozenset()
    for table in tree.find_all(exp.Table):
        if _is_name(table) or _is_generator(table):
            continue
        # A file reader, a quoted path, a URL or any other table function.
        return "hostile_sql:path_not_allowed", frozenset()
    if any(_lateral_reader(lateral) for lateral in tree.find_all(exp.Lateral)):
        # The same, reached through LATERAL (query_table, sniff_csv, catalog
        # functions such as duckdb_settings()).
        return "hostile_sql:path_not_allowed", frozenset()
    try:
        scopes = traverse_scope(tree)
    except Exception:  # noqa: BLE001 - fail closed
        return "hostile_sql:sql_not_analyzable", frozenset()
    if not scopes:
        return "hostile_sql:sql_not_analyzable", frozenset()
    # Tables sqlglot gives no scope (a subquery in extra parentheses or in
    # unnest()) are still read, so the syntax tree is the source; the scopes'
    # view is added in case the two disagree, which only asks about more.
    real = _real_tables(tree)
    for scope in scopes:
        for table in scope.tables:
            if _is_name(table) and _cte_of(scope, table) is None:
                real.add(_bare(table.name))
    try:
        if not _reads_data(scopes[-1], {}):
            return "hostile_sql:no_data_source", frozenset(real)
        if not _scope_derived(_Walk(), scopes[-1], 0, _NONE):
            # Reads a table, returns nothing computed from it: SELECT 4242 FROM
            # t, however the literal is wrapped.
            return "hostile_sql:literal_output", frozenset(real)
    except (RecursionError, _TooDeep):
        # Nested past what the walk can trace (hundreds of UNION arms or of
        # terms in one expression): fail closed rather than crash the ask.
        return "hostile_sql:sql_not_analyzable", frozenset(real)
    return None, frozenset(real)


__all__ = ["literal_figure_reason", "select_shape"]
