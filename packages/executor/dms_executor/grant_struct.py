"""Structural grant check for a statement about to run.

The question text is not an input. Callers pass the extract dialect they
already use (warehouse SQL is duckdb). sqlglot parses in that dialect.

Allow-list: every relation the parser can see, including inside CTEs and
subqueries, must be a granted qualified name. A table or external-read
function anywhere in the tree (select list, WHERE, a subquery) is not a
granted relation. The default schema is part of the comparison, so a
different schema is not the granted table. More than one statement
refuses. A parse failure refuses. No function-name list and no split
on ``;``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError

from dms_executor.abstain import build_abstain
from dms_executor.demo_warehouse import SERVING_DIALECT, clear_engine_clock, connect_file
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
) -> str | None:
    """None when the statement is one granted read.

    Order: the dialect has to be one sqlglot knows, then parse, then one
    statement, then the existing hostile scanner, then the allow-list.
    Names are normalized once, with that dialect's rules. A qualified
    relation is granted only when that exact key is granted. A bare grant
    covers the default schema only. An unknown dialect is
    ``sql_dialect_unknown``. A name that cannot be settled is
    ``sql_relation_unresolved``. A reader the parser types as a table or
    external-read function, in any position, is ``sql_relation_not_granted``.
    A missing name in the default schema stays ``ungranted:<name>``.
    """
    if _engine(dialect) is None:
        return "sql_dialect_unknown"
    trees = _trees(sql, dialect)
    if trees is None:
        return "ungranted:unparsed"
    if len(trees) != 1:
        return "multi_statement"
    try:
        reject_hostile_chat_sql(sql)
    except SecurityEvent as exc:
        return f"hostile_sql:{exc.code}"
    catalog = _catalog_for(trees[0], warehouse, dialect)
    return _allow(trees[0], grantable, dialect, catalog)


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


def _cte_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = str(cte.alias or "").strip().lower()
        if alias:
            names.add(alias)
    return names


def _slash_path(name: str) -> bool:
    # A slash inside one identifier is a path. A dot may be a quoted relation
    # (``"usd.book"``). Do not bare on that dot.
    return "/" in name or "\\" in name


def _relations(tree: exp.Expression) -> list[tuple[str, str]]:
    """``table`` tokens, ``dotted`` names, or ``unresolved`` non-relations."""
    found: list[tuple[str, str]] = []
    ctes = _cte_names(tree)
    for table in tree.find_all(exp.Table):
        this = table.this
        if not isinstance(this, exp.Identifier):
            found.append(("unresolved", ""))
            continue
        name = str(table.name or "")
        if not name:
            found.append(("unresolved", ""))
            continue
        if not table.db and name.lower() in ctes:
            continue
        if _slash_path(name):
            found.append(("unresolved", ""))
            continue
        if "." in name:
            found.append(("dotted", name))
            continue
        token = f"{table.db}.{name}" if table.db else name
        found.append(("table", token))
    return found


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


def _norm_ident(node: exp.Expression, engine: Dialect) -> str | None:
    if not isinstance(node, exp.Identifier):
        return None
    copied = node.copy()
    engine.normalize_identifier(copied)
    text = str(copied.name or "")
    if not text or _slash_path(text):
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
        if "." in token and quote not in token and end not in token and "`" not in token:
            one = token.replace(end, end + end)
            quoted = normalize_relation(f"{quote}{one}{end}", dialect=dialect)
            if quoted:
                keys.add(quoted)
    return keys


def _external_read(tree: exp.Expression) -> bool:
    """A parser-typed file reader anywhere in the tree.

    An unclassified scalar (``error``, ``printf``) is not a relation. The
    grant decision is which relations the statement reads. A function the
    parser leaves unclassified and places in the relation slot is handled
    in ``_allow``, because that slot is not a granted name.
    """
    return any(isinstance(node, (exp.ReadCSV, exp.ReadParquet)) for node in tree.walk())


def _cte_keys(tree: exp.Expression, engine: Dialect) -> set[str]:
    names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.args.get("alias")
        ident = getattr(alias, "this", None)
        if not isinstance(ident, exp.Identifier):
            continue
        text = _norm_ident(ident, engine)
        if text:
            names.add(text)
    return names


def _allow(
    tree: exp.Expression,
    grantable: set[str],
    dialect: str | None,
    catalog: _Catalog | None = None,
) -> str | None:
    found = _engine(dialect)
    if found is None:
        return "sql_dialect_unknown"
    dialect_name, engine = found
    if _external_read(tree):
        return "sql_relation_not_granted"
    default = _default_schema(dialect_name, engine)
    keys = _grant_keys(grantable, dialect)
    granted_rows = _grant_rows(grantable, dialect, catalog)
    ctes = _cte_keys(tree, engine)
    not_relation = False
    qualified_miss = False
    unsettled = False
    missing: set[str] = set()
    for table in tree.find_all(exp.Table):
        this = table.this
        if not isinstance(this, exp.Identifier):
            # A function in the relation slot is not a granted name.
            # A typed value generator (generate_series) reads no relation.
            # Placeholder and a deeper dot have no name to settle.
            if isinstance(this, exp.Anonymous):
                not_relation = True
            elif not isinstance(this, exp.Func):
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
            text = _norm_ident(node, engine)
            if not text:
                broken = True
                break
            names.append(text)
        if broken or not names:
            not_relation = True
            continue
        if len(names) == 1 and names[0] in ctes:
            continue
        had_qualifier = bool(table.args.get("db") or table.args.get("catalog"))
        cited = list(names)
        if len(names) == 1 and default:
            names = [default, names[0]]
        key = _relation_key(names)
        if key in keys:
            continue
        if catalog is not None and had_qualifier:
            # Same catalog row as a grant is granted. A different row is not.
            # A qualifier that is not a schema and not this database is
            # unresolved. A known schema with no such table keeps the old miss.
            bound = _bind(cited, catalog)
            if bound is not None:
                if bound in granted_rows:
                    continue
                qualified_miss = True
                continue
            if not _qualifier_known(cited, catalog):
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
            not default or schema != default or bool(table.args.get("catalog"))
        )
        if non_default:
            qualified_miss = True
            continue
        missing.add(names[-1])
    if unsettled:
        return "sql_relation_unresolved"
    if not_relation or qualified_miss:
        return "sql_relation_not_granted"
    if missing:
        return "ungranted:" + ",".join(sorted(missing))
    return None


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
    """Read the file only when the statement cites a qualifier."""
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
        con = connect_file(Path(warehouse))
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
