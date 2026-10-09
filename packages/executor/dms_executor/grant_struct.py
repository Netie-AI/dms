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

from datetime import UTC, datetime
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError

from dms_executor.abstain import build_abstain
from dms_executor.demo_warehouse import clear_engine_clock
from dms_executor.envelope import assert_envelope_valid
from dms_executor.gen_path_refuse import customer_abstain_text
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql

# SourceConfig.kind plus the warehouse dialect. Not a block list.
_DIALECTS = {
    "duckdb": "duckdb",
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
    try:
        tree = sqlglot.parse_one(f"SELECT * FROM {text}", read=dialect_name)
    except SqlglotError:
        return None
    if tree is None:
        return None
    table = next(tree.find_all(exp.Table), None)
    if table is None:
        return None
    return _table_key(table, dialect_name, engine)


def serve_gap(sql: str, *, grantable: set[str], dialect: str | None) -> str | None:
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
    return _allow(trees[0], grantable, dialect)


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
) -> dict[str, Any]:
    """Named ABSTAIN. The statement did not run. No path in the envelope."""
    env = build_abstain(
        reason=reason,
        question=question or "",
        stage=route or "unspecified",
        answer_id="ans_sql_refused",
        text=customer_abstain_text(reason),
        rows=[],
        values=[],
        sql_used=None,
        assumptions=[f"validate:{reason}"],
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
    "duckdb": "main",
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


def _literal_call(node: exp.Expression) -> bool:
    """Unclassified function of literals only: a path or a query string."""
    args = list(node.expressions or [])
    return bool(args) and all(isinstance(arg, exp.Literal) for arg in args)


def _external_read(tree: exp.Expression) -> bool:
    """A table or external-read function in any position, not only FROM."""
    for node in tree.walk():
        if isinstance(node, (exp.ReadCSV, exp.ReadParquet)):
            return True
        if isinstance(node, exp.Anonymous) and _literal_call(node):
            return True
    return False


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


def _allow(tree: exp.Expression, grantable: set[str], dialect: str | None) -> str | None:
    found = _engine(dialect)
    if found is None:
        return "sql_dialect_unknown"
    dialect_name, engine = found
    if _external_read(tree):
        return "sql_relation_not_granted"
    default = _default_schema(dialect_name, engine)
    keys = _grant_keys(grantable, dialect)
    ctes = _cte_keys(tree, engine)
    not_relation = False
    qualified_miss = False
    unsettled = False
    missing: set[str] = set()
    for table in tree.find_all(exp.Table):
        this = table.this
        if not isinstance(this, exp.Identifier):
            # A function in the relation slot is not a granted name.
            # Placeholder and a deeper dot have no name to settle.
            if isinstance(this, exp.Func):
                not_relation = True
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
        if len(names) == 1 and default:
            names = [default, names[0]]
        key = _relation_key(names)
        if key in keys:
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
