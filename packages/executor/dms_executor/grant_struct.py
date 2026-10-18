"""Structural grant check for a statement about to run.

The question text is not an input. Callers pass the extract dialect they
already use (warehouse SQL is duckdb). sqlglot parses in that dialect.

Allow-list: every relation the parser can see, including inside CTEs and
subqueries, must be a granted qualified name. A one-part name is a CTE
reference only where a WITH enclosing it defines that name (scope, not
spelling); anywhere else it is a relation and the grant decides. A self or
forward reference inside a CTE body is a relation. A file or external reader
anywhere in the tree is refused. Every FROM, JOIN and LATERAL item is a plain
table name, a subquery or a VALUES list; anything else (a table function, a
table macro, a generator, ``LATERAL f(...)``) is not a relation and refuses.
A name whose case fold is not ASCII-only is not settled: sqlglot folds with
Python ``str.lower``, the engine folds ASCII. The default schema is part of
the comparison, so a different schema is not the granted table. More than one
statement refuses, and so does one that is not a query. A parse failure
refuses. No function-name list and no split on ``;``.

sqlglot does not parse every statement the way DuckDB binds it (``(TABLE t)``
is a column to sqlglot and ``SELECT * FROM t`` to DuckDB). For the serving
dialect the statement is therefore also grant-checked on DuckDB's own parse
(``json_serialize_sql``, parse only, nothing runs): every base table there
goes through the same allow-list, and any other table reference refuses.
"""

from __future__ import annotations

import json
import os
import string
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError

from dms_executor.abstain import build_abstain
from dms_executor.demo_warehouse import SERVING_DIALECT, clear_engine_clock, connect_serving
from dms_executor.envelope import assert_envelope_valid
from dms_executor.gen_path_refuse import customer_abstain_text
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql

# SourceConfig.kind plus the warehouse dialect. Not a block list.
# Keys are the kind the caller already has. The serving name is the constant.
_DIALECTS = {
    SERVING_DIALECT: SERVING_DIALECT,
    "postgres": "postgres",
    "postgresql": "postgres",
    "mysql": "mysql",
    "sqlserver": "tsql",
    "tsql": "tsql",
}

_STOP = frozenset(
    {
        "ungranted:file",
        "ungranted:unparsed",
        "multi_statement",
        "statement_not_allowed",
        "sql_relation_not_granted",
        "sql_relation_unresolved",
        "sql_dialect_unknown",
    }
)


def sqlglot_dialect(kind: str | None) -> str | None:
    """sqlglot read-dialect for a kind production already passes. None if unknown."""
    return _dialect_name(kind)


def normalize_relation(name: str, *, dialect: str | None) -> str | None:
    """Canonical relation key for ``dialect``, or None if it cannot be settled.

    One function. sqlglot's own ``normalize_identifier`` applies that
    dialect's fold (the engine's rule). There is no blanket lowercase and
    no match on the bare table tail. A single identifier uses the dialect's
    fixed default schema when the engine has one. #422 imports this; it
    must not keep a second copy.
    """
    found = _engine(dialect)
    if found is None:
        return None
    dialect_name, engine = found
    text = str(name or "").strip()
    if not text:
        return None
    table = _parsed_table(text, dialect_name, engine)
    if table is None:
        return None
    return _table_key(table, dialect_name, engine)


def serve_gap(
    sql: str,
    *,
    grantable: set[str],
    dialect: str | None,
    warehouse: Path | None = None,
    cite_missing: bool = False,
) -> str | None:
    """None when the statement is one granted read.

    Order: the dialect has to be one sqlglot knows, then parse, then one
    statement, then the existing hostile scanner, then the statement has to
    be a query (``statement_not_allowed`` otherwise: sqlglot's parsed type,
    so a COPY, SET or ALTER the scanner let through still does not reach
    EXPLAIN), then the allow-list.
    Names are normalized once, with that dialect's rules. A qualified
    relation is granted only when that exact key is granted. A bare grant
    covers the default schema only. An unknown dialect is
    ``sql_dialect_unknown``. A name that cannot be settled is
    ``sql_relation_unresolved``. A reader the parser types as a table or
    external-read function, in any position, is ``sql_relation_not_granted``.
    A missing name in the default schema is bare ``ungranted``. The
    relation is not part of that code, unless ``cite_missing`` (the
    compiled-SQL checker names the table). A ``bronze.<table>`` grant
    covers that bare name in the default schema. Folding inside the
    allow-list is ASCII-only, so ``Ä`` and ``ä`` stay two tables; a fold
    that would invent an ASCII letter stays unsettled. A statement whose
    only source is a literal, a literal wrapped in a cast, or a union of
    those selects is bare ``ungranted``: the parsed tree has no granted
    base table. A file or external reader is ``sql_relation_not_granted``
    even beside a granted table. A generator is that same code, alone or
    beside a grant. Any other FROM, JOIN or LATERAL item that is not a
    table name, a subquery or a VALUES list is bare ``ungranted`` when
    the statement reads no granted table, and ``sql_relation_not_granted``
    when it does.

    Last, for the serving dialect, DuckDB's own parse of the same text
    (``_duckdb_gap``): one statement, and every base table it reads passes
    the same allow-list. That is where sqlglot and DuckDB disagree.
    """
    if _engine(dialect) is None:
        return "sql_dialect_unknown"
    trees = _trees(sql, dialect)
    if trees is None:
        return "ungranted:unparsed"
    if len(trees) != 1:
        return "multi_statement"
    # A value constructor is not a SELECT. The non-select scanner is not
    # this outcome: a VALUES list that reads no granted base table is bare
    # ungranted. A VALUES that does read one falls through to that scanner.
    if isinstance(trees[0], exp.Values):
        gap = _allow(
            trees[0],
            grantable,
            dialect,
            _catalog_for(trees[0], warehouse, dialect),
            cite_missing=cite_missing,
        )
        if gap:
            return gap
    try:
        reject_hostile_chat_sql(sql)
    except SecurityEvent as exc:
        return f"hostile_sql:{exc.code}"
    if not isinstance(trees[0], (exp.Query, exp.Values)):
        return "statement_not_allowed"
    catalog = _catalog_for(trees[0], warehouse, dialect)
    # For the serving dialect "reads a granted base table" is decided on
    # DuckDB's parse: sqlglot does not see ``(TABLE t)`` as a read at all.
    serving = _dialect_name(dialect) == SERVING_DIALECT
    gap = _allow(
        trees[0],
        grantable,
        dialect,
        catalog,
        need_granted=not serving,
        cite_missing=cite_missing,
    )
    if gap or not serving:
        return gap
    return _duckdb_gap(sql, grantable, dialect, catalog, cite_missing=cite_missing)


def relation_gap(name: str, *, grantable: set[str], dialect: str | None) -> str | None:
    """None when ``name`` is a granted relation. Same keys as ``serve_gap``.

    The caller already has the relation. This does not build a statement.
    A statement still goes through ``serve_gap``. A missing dialect is
    ``sql_dialect_unknown``.
    """
    if _engine(dialect) is None:
        return "sql_dialect_unknown"
    key = normalize_relation(name, dialect=dialect)
    if not key:
        return "sql_relation_unresolved"
    if key in _grant_keys(set(grantable), dialect):
        return None
    return "sql_relation_not_granted"


def customer_grant_reason(reason: str) -> str:
    """User-visible reason code. A table name is not part of the code.

    ``ungranted:file`` and ``ungranted:unparsed`` stay. A bare
    ``ungranted:<table>`` drops the table. The envelope builder records
    the code. This function does not log it.
    """
    gap = str(reason or "").strip()
    if gap.startswith("ungranted:") and gap not in {"ungranted:file", "ungranted:unparsed"}:
        return "ungranted"
    return gap


def structural_grant_stop(reason: str) -> bool:
    """True when a retry would run a file, a script, or an unparsed statement."""
    return str(reason or "").strip() in _STOP


def sql_refusal_envelope(
    *,
    reason: str,
    space_id: str | None,
    session_id: str | None,
    route: str,
    question: str | None,
    shown: str | None = None,
) -> dict[str, Any]:
    """Named ABSTAIN. The statement did not run. No path in the envelope.

    ``reason`` is the ticket. ``shown`` is the envelope code. Callers that
    must hide a table name pass ``shown``. Other callers show ``reason``.
    """
    visible = reason if shown is None else shown
    env = build_abstain(
        reason=reason,
        question=question or "",
        stage=route or "unspecified",
        answer_id="ans_sql_refused",
        text=customer_abstain_text(visible),
        rows=[],
        values=[],
        sql_used=None,
        assumptions=[f"validate:{visible}"],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route=route,
    )
    assert_envelope_valid(env)
    clear_engine_clock()
    return env


def _trees(sql: str, dialect: str | None) -> list[exp.Expression] | None:
    read = sqlglot_dialect(dialect)
    if read is None:
        return None
    try:
        trees = sqlglot.parse(sql or "", read=read)
    except SqlglotError:
        return None
    if not trees or any(tree is None for tree in trees):
        return None
    return [tree for tree in trees if tree is not None]


def _slash_path(name: str) -> bool:
    # A slash inside one identifier is a path. A dot may be a quoted relation
    # (``"usd.book"``). Do not bare on that dot.
    return "/" in name or "\\" in name


# Fixed default schema an unqualified name resolves to. DuckDB's
# current_schema() on a fresh connection is main. Postgres search_path
# resolves to public. TSQL's built-in default is dbo. Snowflake's current
# schema is the session, so a bare name stays unqualified.
_DEFAULT_SCHEMA = {
    SERVING_DIALECT: "main",
    "postgres": "public",
    "tsql": "dbo",
}


def _dialect_name(kind: str | None) -> str | None:
    key = (kind or "").strip().lower()
    if not key:
        return None
    mapped = _DIALECTS.get(key, key)
    try:
        Dialect.get_or_raise(mapped)
    except ValueError:
        return None
    return mapped


def _engine(dialect: str | None) -> tuple[str, Dialect] | None:
    name = _dialect_name(dialect)
    if name is None:
        return None
    engine = Dialect.get_or_raise(name)
    if not isinstance(engine, Dialect):
        return None
    return name, engine


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_ASCII_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)


def _ascii_fold(raw: str, folded: str) -> bool:
    """True when the dialect's fold of ``raw`` changed ASCII letters only.

    sqlglot folds with Python ``str.lower`` / ``str.upper``, which also maps
    non-ASCII letters: U+212A KELVIN SIGN lowers to ASCII ``k`` and ``É`` to
    ``é``. DuckDB (and Postgres) fold ASCII only, so ``"banK_accounts"``
    with a Kelvin sign is not the CTE or the table ``bank_accounts`` there.
    When the two folds disagree, this key may name a different object than
    the one the engine binds, so the name is not settled.
    """
    return folded in {raw, raw.translate(_ASCII_LOWER), raw.translate(_ASCII_UPPER)}


def _norm_ident(node: exp.Expression, engine: Dialect) -> str | None:
    if not isinstance(node, exp.Identifier):
        return None
    raw = str(node.name or "")
    copied = node.copy()
    engine.normalize_identifier(copied)
    text = str(copied.name or "")
    if not text or _slash_path(text) or not _ascii_fold(raw, text):
        return None
    return text


def _default_schema(dialect_name: str, engine: Dialect) -> str | None:
    raw = _DEFAULT_SCHEMA.get(dialect_name)
    if not raw:
        return None
    return _norm_ident(exp.to_identifier(raw), engine)


def _relation_key(parts: list[str]) -> str:
    rendered: list[str] = []
    for part in parts:
        if "." in part or '"' in part:
            rendered.append('"' + part.replace('"', '""') + '"')
        else:
            rendered.append(part)
    return ".".join(rendered)


def _table_key(table: exp.Table, dialect_name: str, engine: Dialect) -> str | None:
    nodes = [
        node
        for node in (table.args.get("catalog"), table.args.get("db"), table.this)
        if node is not None
    ]
    if not nodes or any(not isinstance(node, exp.Identifier) for node in nodes):
        return None
    names: list[str] = []
    for node in nodes:
        text = _norm_ident(node, engine)
        if not text:
            return None
        names.append(text)
    if len(names) == 1:
        default = _default_schema(dialect_name, engine)
        if default:
            names.insert(0, default)
    return _relation_key(names)


def _fold_adds_ascii(ascii_name: str, folded: str) -> bool:
    """True when sqlglot's fold invented an ASCII letter the ASCII fold did not.

    ``Ä`` lowercased is still ``ä``, not an ASCII letter, so the original
    character stands. Kelvin U+212A lowercases to ASCII ``k`` and does not.
    """
    if len(folded) != len(ascii_name):
        return True
    for src, dst in zip(ascii_name, folded, strict=True):
        if dst.isascii() and dst.isalpha() and dst != src:
            return True
    return False


def _grant_ident(node: exp.Expression, engine: Dialect) -> str | None:
    """Table name for the allow-list. ``normalize_relation`` stays stricter.

    ``_norm_ident`` refuses every name sqlglot's ``str.lower`` changes past
    an ASCII fold. A letter that stays non-ASCII (``Ä``) is the ASCII-folded
    original here, not that lower form, and stays distinct from ``ä``.
    """
    settled = _norm_ident(node, engine)
    if settled:
        return settled
    if not isinstance(node, exp.Identifier):
        return None
    raw = str(node.name or "")
    if not raw or _slash_path(raw):
        return None
    copied = node.copy()
    engine.normalize_identifier(copied)
    folded = str(copied.name or "")
    ascii_name = raw.translate(_ASCII_LOWER)
    if not ascii_name or _fold_adds_ascii(ascii_name, folded):
        return None
    return ascii_name


def _bronze_default_key(token: str, dialect: str | None) -> str | None:
    """``<default>.<table>`` when ``token`` is exactly ``bronze.<table>``.

    A SQL-source Space grants the bronze relation. The bare name in the
    default schema is that table. A bare grant does not cover ``bronze``.
    A name ``_norm_ident`` will not settle is not expanded.
    """
    found = _engine(dialect)
    if found is None:
        return None
    dialect_name, engine = found
    table = _parsed_table(token, dialect_name, engine)
    if table is None or table.args.get("catalog") is not None:
        return None
    db = table.args.get("db")
    this = table.this
    if not isinstance(db, exp.Identifier) or not isinstance(this, exp.Identifier):
        return None
    if _norm_ident(db, engine) != "bronze":
        return None
    bare = _norm_ident(this, engine)
    default = _default_schema(dialect_name, engine)
    if not bare or not default or default == "bronze":
        return None
    return _relation_key([default, bare])


def _grant_keys(grantable: set[str], dialect: str | None) -> set[str]:
    """Exact keys. A dotted token is also the one-identifier table name."""
    keys: set[str] = set()
    found = _engine(dialect)
    if found is None:
        return keys
    _dialect_name, engine = found
    quote = str(engine.IDENTIFIER_START or '"')
    end = str(engine.IDENTIFIER_END or quote)
    for raw in grantable:
        token = str(raw).strip()
        if not token or _slash_path(token):
            continue
        key = normalize_relation(token, dialect=dialect)
        if key:
            keys.add(key)
            bare = _bronze_default_key(token, dialect)
            if bare:
                keys.add(bare)
        if "." in token and quote not in token and end not in token and "`" not in token:
            one = token.replace(end, end + end)
            quoted = normalize_relation(f"{quote}{one}{end}", dialect=dialect)
            if quoted:
                keys.add(quoted)
    return keys


def _file_reader_types() -> tuple[type, ...]:
    """sqlglot classes that are file readers. Not a list of function names."""
    found: list[type] = []
    for name, cls in vars(exp).items():
        if isinstance(cls, type) and issubclass(cls, exp.Func) and name.startswith("Read"):
            found.append(cls)
    return tuple(found)


def _anon_name(node: exp.Anonymous) -> str:
    this = node.this
    if isinstance(this, exp.Identifier):
        return str(this.name or "")
    return str(this or "")


def _catalog_row_reads_a_file(params: Any, ptypes: Any) -> bool:
    """True when this ``duckdb_functions()`` row names a path parameter.

    The parameter is ``filename``. A single ``col0 VARCHAR`` argument is
    not enough: ``query`` and ``query_table`` have that shape and read a
    table, not a file. A path-shaped argument on the call is decided in
    ``_allow``.
    """
    del ptypes
    names = [str(param).lower() for param in (params or ())]
    return "filename" in names


_CATALOG_READERS: frozenset[str] | None = None


def _catalog_readers() -> frozenset[str]:
    """Table functions ``duckdb_functions()`` shows with a path parameter."""
    global _CATALOG_READERS
    if _CATALOG_READERS is not None:
        return _CATALOG_READERS
    import duckdb

    readers: set[str] = set()
    try:
        con = duckdb.connect(":memory:")
    except Exception:  # noqa: BLE001 - no catalog; a relation slot still refuses
        _CATALOG_READERS = frozenset()
        return _CATALOG_READERS
    try:
        rows = con.execute(
            "SELECT function_name, function_type, parameters, parameter_types "
            "FROM duckdb_functions()"
        ).fetchall()
    except Exception:  # noqa: BLE001
        rows = []
    finally:
        con.close()
    for name, ftype, params, ptypes in rows:
        key = str(name or "").lower()
        if key and ftype in {"table", "table_macro"} and _catalog_row_reads_a_file(
            params, ptypes
        ):
            readers.add(key)
    _CATALOG_READERS = frozenset(readers)
    return _CATALOG_READERS


def _file_reader(tree: exp.Expression) -> bool:
    """A file or external reader anywhere in the tree.

    A typed reader node is enough. An unclassified call is a reader when
    ``duckdb_functions()`` gives it a path parameter.
    """
    typed = _file_reader_types()
    readers = _catalog_readers()
    for node in tree.walk():
        if typed and isinstance(node, typed):
            return True
        if isinstance(node, exp.Anonymous) and _anon_name(node).strip().lower() in readers:
            return True
    return False


def _cte_key(cte: exp.Expression, engine: Dialect) -> str | None:
    alias = cte.args.get("alias")
    ident = getattr(alias, "this", None)
    if not isinstance(ident, exp.Identifier):
        return None
    return _norm_ident(ident, engine)


def _cte_in_scope(table: exp.Table, name: str, engine: Dialect) -> bool:
    """True only when a WITH that encloses ``table`` defines ``name`` for it.

    Scope, not spelling. Walk up from the table. A WITH on an enclosing
    query covers that query's body. Inside one CTE's own body only the
    CTEs before it count: DuckDB reads the base table for a forward
    reference and for a plain self reference, so that name is checked as a
    relation. A real recursive CTE (an anchor UNION ALL a step) does bind
    its own name in DuckDB; that read is still checked as a relation, which
    refuses a statement DuckDB would answer from the CTE (fails closed). A
    CTE defined in a sibling subquery, another union branch or another
    scalar subquery does not enclose the table, so the name is a relation
    and the grant decides.
    """
    child: exp.Expression = table
    node = table.parent
    while node is not None:
        if isinstance(node, exp.With):
            ctes = list(node.expressions)
            index = next((i for i, cte in enumerate(ctes) if cte is child), 0)
            if any(_cte_key(cte, engine) == name for cte in ctes[:index]):
                return True
        else:
            with_ = node.args.get("with")
            if isinstance(with_, exp.With) and with_ is not child:
                if any(_cte_key(cte, engine) == name for cte in with_.expressions):
                    return True
        child = node
        node = node.parent
    return False


# What a FROM or JOIN item may be. A Lateral is checked on its own: only
# ``LATERAL (subquery)``.
_RELATION_ITEMS = (exp.Table, exp.Subquery, exp.Values, exp.Lateral)


def _literal_path(node: exp.Literal) -> bool:
    """A string argument that is a path. A bare table name has no dot or slash."""
    if not node.is_string:
        return False
    text = str(node.this or "")
    return "/" in text or "\\" in text or "." in text


def _arg_names_a_relation(node: exp.Expression) -> bool:
    """True when this argument names a relation or a path, not a plain literal."""
    if isinstance(node, exp.Literal):
        return _literal_path(node)
    if isinstance(node, exp.Paren) and node.this is not None:
        return _arg_names_a_relation(node.this)
    if isinstance(node, (exp.Column, exp.Identifier, exp.Table)):
        return True
    if isinstance(node, (exp.Array, exp.Tuple)):
        return any(_arg_names_a_relation(item) for item in node.expressions)
    named = node.find_all(exp.Column, exp.Identifier, exp.Table)
    if next(named, None) is not None:
        return True
    for item in node.find_all(exp.Literal):
        if _literal_path(item):
            return True
    return False


def _call_is_closed(node: exp.Expression) -> bool:
    """True when a table call is ``sql_relation_not_granted`` even with no grant.

    An unclassified call whose arguments are plain literals (or absent)
    does not prove a base table. A column argument or a path-shaped string
    does.
    """
    if not isinstance(node, exp.Anonymous):
        return True
    return any(_arg_names_a_relation(arg) for arg in node.expressions)


def _is_generator(node: exp.Expression | None) -> bool:
    """A typed generator in a relation slot. Not a list of function names.

    sqlglot types ``generate_series`` and ``range`` as ``GenerateSeries``
    (often the ``this`` of a Table) and ``unnest`` in FROM as ``Unnest``.
    """
    if isinstance(node, (exp.GenerateSeries, exp.Unnest)):
        return True
    return isinstance(node, exp.Table) and isinstance(node.this, (exp.GenerateSeries, exp.Unnest))


def _cite_label(names: list[str], *, catalog: bool) -> str:
    """What ``cite_missing`` puts after ``ungranted:``.

    Three parts, or a catalog qualifier, keep the whole key (a cross-catalog
    read must still show the other database). Two parts cite the table only.
    """
    if catalog or len(names) >= 3:
        return ".".join(names)
    return names[-1]


def _allow(
    tree: exp.Expression,
    grantable: set[str],
    dialect: str | None,
    catalog: _Catalog | None = None,
    *,
    need_granted: bool = True,
    cite_missing: bool = False,
) -> str | None:
    """None when every relation in ``tree`` is granted.

    ``need_granted`` also asks for at least one granted base table. The
    serving dialect leaves that to DuckDB's parse (``_duckdb_gap``).
    ``cite_missing`` names a default-schema or qualified miss. Callers that
    must not echo a table leave it false, and the code stays bare.
    """
    found = _engine(dialect)
    if found is None:
        return "sql_dialect_unknown"
    dialect_name, engine = found
    if _file_reader(tree):
        return "sql_relation_not_granted"
    default = _default_schema(dialect_name, engine)
    keys = _grant_keys(grantable, dialect)
    granted_rows = _grant_rows(grantable, dialect, catalog)
    not_relation = False
    qualified_miss = False
    unsettled = False
    bad_ident = False
    generator = False
    closed = False
    unclassified = False
    saw_granted = False
    missing: set[str] = set()
    cited: set[str] = set()
    # Every relation slot, not only the ones sqlglot types as a Table.
    # ``LATERAL f(...)`` is a Lateral over a function (or a Dot), with no
    # Table node at all, and ``unnest(...)`` in FROM is an Unnest.
    for clause in tree.find_all(exp.From, exp.Join, exp.Lateral):
        item = clause.this
        if _is_generator(item):
            generator = True
            continue
        anon = item if isinstance(item, exp.Anonymous) else None
        if anon is None and isinstance(item, exp.Table) and isinstance(item.this, exp.Anonymous):
            anon = item.this
        if anon is not None:
            if _call_is_closed(anon):
                closed = True
            else:
                unclassified = True
            continue
        if isinstance(clause, exp.Lateral):
            if not isinstance(item, exp.Subquery):
                closed = True
        elif not isinstance(item, _RELATION_ITEMS):
            closed = True
    for table in tree.find_all(exp.Table):
        this = table.this
        if not isinstance(this, exp.Identifier):
            # A typed generator is never a relation. Any other function in
            # the table slot is unclassified: a table macro can read a named
            # table, a file, the settings or the secrets.
            if _is_generator(this) or _is_generator(table):
                generator = True
            elif isinstance(this, exp.Anonymous):
                if _call_is_closed(this):
                    closed = True
                else:
                    unclassified = True
            elif isinstance(this, exp.Func):
                closed = True
            else:
                unsettled = True
            continue
        nodes = [
            node
            for node in (table.args.get("catalog"), table.args.get("db"), this)
            if node is not None
        ]
        if any(not isinstance(node, exp.Identifier) for node in nodes):
            unsettled = True
            continue
        names: list[str] = []
        broken = False
        for node in nodes:
            text = _grant_ident(node, engine)
            if not text:
                broken = True
                break
            names.append(text)
        if broken or not names:
            bad_ident = True
            continue
        if len(names) == 1 and _cte_in_scope(table, names[0], engine):
            continue
        had_catalog = table.args.get("catalog") is not None
        had_qualifier = bool(table.args.get("db") or had_catalog)
        cited_parts = list(names)
        if len(names) == 1 and default:
            names = [default, names[0]]
        key = _relation_key(names)
        if key in keys:
            saw_granted = True
            continue
        if catalog is not None and had_qualifier:
            # Same catalog row as a grant is granted. A different row is not.
            # A qualifier that is not a schema and not this database is
            # unresolved. A known schema with no such table keeps the old miss.
            bound = _bind(cited_parts, catalog)
            if bound is not None:
                if bound in granted_rows:
                    saw_granted = True
                    continue
                qualified_miss = True
                if cite_missing:
                    cited.add(_cite_label(cited_parts, catalog=had_catalog))
                continue
            if not _qualifier_known(cited_parts, catalog):
                unsettled = True
                continue
        # One identifier that contains '.' is a path or a stored name
        # (``"usd.book"``). An exact grant key already continued. A miss is
        # not a default-schema table: ``ungranted:`` would echo the name.
        if len(nodes) == 1 and "." in names[-1]:
            not_relation = True
            continue
        schema = names[0] if len(names) > 1 else ""
        non_default = had_qualifier and (
            not default or schema != default or had_catalog
        )
        if non_default:
            qualified_miss = True
            if cite_missing:
                cited.add(_cite_label(cited_parts, catalog=had_catalog))
            continue
        missing.add(names[-1])
    named = set(cited)
    if cite_missing:
        named |= missing
    # A name we could not settle, a generator, or a reader beats a citation.
    # An unclassified function beside a grant is the same refusal. Alone,
    # with no granted base table, it is bare ungranted (the name is an
    # argument, not a relation the parser proved).
    if bad_ident or generator or closed:
        return "sql_relation_not_granted"
    if unclassified and saw_granted:
        return "sql_relation_not_granted"
    if unclassified and not saw_granted:
        return "ungranted"
    if unsettled and not named:
        return "sql_relation_unresolved"
    if named:
        return "ungranted:" + ",".join(sorted(named))
    if not_relation or qualified_miss:
        return "sql_relation_not_granted"
    if missing or (need_granted and not saw_granted):
        return "ungranted"
    return None


# DuckDB's own parse, as ``json_serialize_sql`` gives it. A query node carries
# "modifiers", a table reference carries "sample", an expression "class".
# Same walk as GRANT-UNICODE-01 (#438, ``_duckdb_read_names``), returning the
# qualified name so the allow-list above decides, not a bare-name match.
_DUCKDB_QUERY_NODES = frozenset(
    {"SELECT_NODE", "SET_OPERATION_NODE", "RECURSIVE_CTE_NODE", "CTE_NODE"}
)
_DUCKDB_TABLE_REFS = frozenset(
    {"BASE_TABLE", "JOIN", "SUBQUERY", "EXPRESSION_LIST", "EMPTY", "EMPTY_FROM", "PIVOT"}
)

_PARSER: tuple[int, Any] | None = None
_PARSER_LOCK = threading.Lock()


def _duckdb_fold(name: str) -> str:
    """Fold a name the way DuckDB matches it: ASCII letters only."""
    return name.translate(_ASCII_LOWER)


def _duckdb_cursor() -> Any:
    """A cursor on one in-memory DuckDB used only to parse. No file, no access.

    One database per process (a forked worker opens its own); each call
    takes its own cursor, so threads do not share one.
    """
    global _PARSER
    import duckdb

    with _PARSER_LOCK:
        if _PARSER is None or _PARSER[0] != os.getpid():
            con = duckdb.connect(
                ":memory:",
                config={
                    "enable_external_access": False,
                    "autoload_known_extensions": False,
                    "autoinstall_known_extensions": False,
                },
            )
            _PARSER = (os.getpid(), con)
        return _PARSER[1].cursor()


def _duckdb_statements(sql: str) -> list[Any] | str:
    """DuckDB's parse of ``sql`` (one statement), or the refusal.

    ``json_serialize_sql`` parses and runs nothing. A syntax error DuckDB
    reports is ``explain:ParserException``, the code EXPLAIN gives for it,
    so the model ladder still feeds the parser error back. A statement
    DuckDB will not serialize (not a SELECT) is ``statement_not_allowed``.
    Two statements where sqlglot saw one is ``multi_statement``.
    """
    import duckdb

    try:
        cur = _duckdb_cursor()
    except duckdb.Error:
        return "sql_relation_unresolved"
    try:
        row = cur.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
        tree = json.loads(row[0]) if row else None
    except (duckdb.Error, TypeError, ValueError, RecursionError):
        return "sql_relation_unresolved"
    finally:
        cur.close()
    if not isinstance(tree, dict):
        return "sql_relation_unresolved"
    if tree.get("error") is not False:
        kind = str(tree.get("error_type") or "")
        if kind == "parser":
            return "explain:ParserException"
        if kind == "not implemented":
            return "statement_not_allowed"
        return "sql_relation_unresolved"
    statements = tree.get("statements")
    if not isinstance(statements, list) or not statements:
        return "sql_relation_unresolved"
    if len(statements) != 1:
        return "multi_statement"
    return statements


def duckdb_read_relations(statements: Any) -> list[tuple[str, ...]] | None:
    """Every base table DuckDB's parse reads, as its written name parts.

    Wherever a BASE_TABLE sits (FROM, a CTE body, any expression) it counts.
    An unqualified name is skipped only when an enclosing WITH certainly
    binds it: a CTE body sees the CTEs before it, the query body sees all of
    them, names compare by DuckDB's ASCII fold. A self or forward reference
    and the recursive term of a WITH RECURSIVE count as a read (fails closed).

    None means the parse holds something that is not a plain table: a table
    function or macro (``histogram_values``, ``query_table``, ``range``,
    ``unnest`` in FROM), a SHOW / DESCRIBE / SUMMARIZE reference, or a node
    type this walk does not know.
    """
    reads: list[tuple[str, ...]] = []

    def walk(node: Any, visible: frozenset[str]) -> bool:
        if isinstance(node, list):
            return all(walk(item, visible) for item in node)
        if not isinstance(node, dict):
            return True
        kind = node.get("type")
        if isinstance(kind, str) and "class" not in node:
            if "modifiers" in node and kind not in _DUCKDB_QUERY_NODES:
                return False
            if ("sample" in node or "table_name" in node) and "modifiers" not in node:
                if kind not in _DUCKDB_TABLE_REFS:
                    return False
            if kind == "BASE_TABLE":
                table = str(node.get("table_name") or "")
                schema = str(node.get("schema_name") or "")
                catalog = str(node.get("catalog_name") or "")
                if not table or (catalog and not schema):
                    return False
                if schema or _duckdb_fold(table) not in visible:
                    reads.append(tuple(part for part in (catalog, schema, table) if part))
        ctes = node.get("cte_map")
        if ctes is not None:
            entries = ctes.get("map") if isinstance(ctes, dict) else None
            if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
                return False
            keys = [_duckdb_fold(str(e.get("key") or "")) for e in entries]
            for at, entry in enumerate(entries):
                scope = (visible | set(keys[:at])) - set(keys[at:])
                if not walk(entry, frozenset(scope)):
                    return False
            visible = visible | frozenset(keys)
        return all(walk(value, visible) for key, value in node.items() if key != "cte_map")

    try:
        ok = walk(statements, frozenset())
    except RecursionError:
        return None
    return reads if ok else None


def _duckdb_gap(
    sql: str,
    grantable: set[str],
    dialect: str | None,
    catalog: _Catalog | None,
    *,
    cite_missing: bool = False,
) -> str | None:
    """None when DuckDB's own parse reads a granted table and nothing else.

    The serving dialect only: that is the engine that runs the text. Each
    base table DuckDB reads is quoted exactly as written and goes through
    ``_allow`` with the same grants and the catalog the sqlglot pass already
    read (no second lease). A qualified name only DuckDB sees has no catalog
    then, so it binds only by its exact grant key (fails closed). A
    statement that reads no base table at all is bare ``ungranted``.
    """
    parsed = _duckdb_statements(sql)
    if isinstance(parsed, str):
        return parsed
    reads = duckdb_read_relations(parsed)
    if reads is None:
        return "sql_relation_not_granted"
    if not reads:
        return "ungranted"
    cited = ", ".join(".".join(_quote_part(part) for part in parts) for parts in reads)
    trees = _trees(f"SELECT 1 FROM {cited}", dialect)
    if trees is None or len(trees) != 1:
        return "sql_relation_unresolved"
    return _allow(
        trees[0], grantable, dialect, catalog, cite_missing=cite_missing
    )


def _quote_part(part: str) -> str:
    return '"' + part.replace('"', '""') + '"'


def _parsed_table(text: str, dialect_name: str, engine: Dialect) -> exp.Table | None:
    """One table from ``text``. A digit-leading part is quoted so it can parse."""
    tree = _parse_from(text, dialect_name)
    if tree is None:
        quoted = _quote_digit_leading(text, engine)
        if not quoted:
            return None
        tree = _parse_from(quoted, dialect_name)
    if tree is None:
        return None
    table = next(tree.find_all(exp.Table), None)
    if not isinstance(table, exp.Table):
        return None
    return table


def _parse_from(text: str, dialect_name: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(f"SELECT * FROM {text}", read=dialect_name)
    except SqlglotError:
        return None


def _quote_digit_leading(text: str, engine: Dialect) -> str | None:
    """Quote bare parts whose first character is a digit. Already-quoted parts stay."""
    parts = _split_qualified(text)
    if not parts:
        return None
    quote = str(engine.IDENTIFIER_START or '"')
    end = str(engine.IDENTIFIER_END or quote)
    changed = False
    rendered: list[str] = []
    for part in parts:
        if not part:
            return None
        if part[0] in {'"', "'", "`", "["}:
            rendered.append(part)
            continue
        if part[0].isdigit():
            changed = True
            inner = part.replace(end, end + end)
            rendered.append(f"{quote}{inner}{end}")
            continue
        rendered.append(part)
    if not changed:
        return None
    return ".".join(rendered)


def _split_qualified(text: str) -> list[str] | None:
    """Split on dots that are outside identifier quotes."""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    pairs = {'"': '"', "'": "'", "`": "`", "[": "]"}
    i = 0
    while i < len(text):
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in pairs:
            quote = pairs[ch]
            buf.append(ch)
        elif ch == ".":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if quote is not None:
        return None
    parts.append("".join(buf).strip())
    return parts


@dataclass(frozen=True)
class _Catalog:
    """Relations the executor file actually has. Names are already normalised."""

    database: str
    default_schema: str
    schemas: frozenset[str]
    rows: frozenset[tuple[str, str]]


def _catalog_for(
    tree: exp.Expression, warehouse: Path | None, dialect: str | None
) -> _Catalog | None:
    """Read the file only when the statement cites a qualifier.

    Chat-path read: the serving cursor (read-only lease), never the ingest
    writer, so a grant check does not queue behind or block serving reads.
    """
    if warehouse is None or not Path(warehouse).is_file():
        return None
    if not any(
        table.args.get("db") or table.args.get("catalog")
        for table in tree.find_all(exp.Table)
    ):
        return None
    found = _engine(dialect)
    if found is None:
        return None
    _name, engine = found
    try:
        con = connect_serving(Path(warehouse))
    except Exception:  # noqa: BLE001 - no catalog, the name check stays on keys
        return None
    try:
        db_row = con.execute("SELECT current_database()").fetchone()
        schema_row = con.execute("SELECT current_schema()").fetchone()
        # information_schema and pg_catalog live in the system database.
        # A qualifier that names one of them is a real schema, not unresolved.
        schema_listed = con.execute(
            "SELECT schema_name FROM duckdb_schemas() "
            "WHERE database_name NOT IN ('temp')"
        ).fetchall()
        listed = con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables() "
            "WHERE database_name = current_database() "
            "AND database_name NOT IN ('system', 'temp')"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    finally:
        con.close()
    if not db_row or not schema_row:
        return None
    database = _norm_ident(exp.to_identifier(str(db_row[0])), engine)
    default_schema = _norm_ident(exp.to_identifier(str(schema_row[0])), engine)
    if not database or not default_schema:
        return None
    rows: set[tuple[str, str]] = set()
    schemas: set[str] = set()
    for (schema_name,) in schema_listed:
        schema = _norm_ident(exp.to_identifier(str(schema_name)), engine)
        if schema:
            schemas.add(schema)
    for schema_name, table_name in listed:
        schema = _norm_ident(exp.to_identifier(str(schema_name)), engine)
        table = _norm_ident(exp.to_identifier(str(table_name)), engine)
        if schema and table:
            schemas.add(schema)
            rows.add((schema, table))
    return _Catalog(
        database=database,
        default_schema=default_schema,
        schemas=frozenset(schemas),
        rows=frozenset(rows),
    )


def _qualifier_known(names: list[str], catalog: _Catalog) -> bool:
    """True when the qualifier is a schema or this database. The table may be missing."""
    if len(names) == 2:
        qualifier = names[0]
        return qualifier in catalog.schemas or qualifier == catalog.database
    if len(names) == 3:
        database, schema, _table = names
        return database == catalog.database and schema in catalog.schemas
    return False


def _bind(names: list[str], catalog: _Catalog) -> tuple[str, str] | None:
    """``(schema, table)`` the catalog binds, or None when the qualifier does not."""
    if len(names) == 2:
        qualifier, table = names
        if qualifier in catalog.schemas and (qualifier, table) in catalog.rows:
            return (qualifier, table)
        if qualifier == catalog.database and (catalog.default_schema, table) in catalog.rows:
            return (catalog.default_schema, table)
        return None
    if len(names) == 3:
        database, schema, table = names
        if (
            database == catalog.database
            and schema in catalog.schemas
            and (schema, table) in catalog.rows
        ):
            return (schema, table)
        return None
    return None


def _grant_rows(
    grantable: set[str], dialect: str | None, catalog: _Catalog | None
) -> set[tuple[str, str]]:
    """Catalog rows a grant token binds to. Empty when there is no catalog."""
    if catalog is None:
        return set()
    found = _engine(dialect)
    if found is None:
        return set()
    dialect_name, engine = found
    rows: set[tuple[str, str]] = set()
    for raw in grantable:
        token = str(raw).strip()
        if not token:
            continue
        table = _parsed_table(token, dialect_name, engine)
        if table is None:
            continue
        nodes = [
            node
            for node in (table.args.get("catalog"), table.args.get("db"), table.this)
            if isinstance(node, exp.Identifier)
        ]
        names: list[str] = []
        for node in nodes:
            text = _norm_ident(node, engine)
            if not text:
                names = []
                break
            names.append(text)
        if len(names) == 1:
            names = [catalog.default_schema, names[0]]
        bound = _bind(names, catalog)
        if bound is not None:
            rows.add(bound)
    return rows
