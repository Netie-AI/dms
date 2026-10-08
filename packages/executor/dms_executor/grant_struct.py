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
from sqlglot.errors import SqlglotError

from dms_executor.demo_warehouse import clear_engine_clock
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.gen_path_refuse import customer_abstain_text
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql
from dms_executor.ontology import table_is_granted

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
    }
)


def sqlglot_dialect(kind: str) -> str | None:
    """sqlglot read-dialect for a kind production already passes. None if unknown."""
    return _DIALECTS.get((kind or "").strip().lower())


def serve_gap(sql: str, *, grantable: set[str], dialect: str) -> str | None:
    """None when the statement is one granted read.

    Order: parse, then one statement, then the existing hostile scanner,
    then the allow-list. A file function the scanner already names keeps
    that sentence. A reader the parser types as a table or external-read
    function, in any position, is ``sql_relation_not_granted``. A schema
    other than the default, unless that qualified name is granted, is the
    same sentence. A missing name in the default schema stays
    ``ungranted:<bare>``.
    """
    trees = _trees(sql, dialect)
    if trees is None:
        return "ungranted:unparsed"
    if len(trees) != 1:
        return "multi_statement"
    try:
        reject_hostile_chat_sql(sql)
    except SecurityEvent as exc:
        return f"hostile_sql:{exc.code}"
    unresolved, missing = _allow(trees[0], grantable, dialect)
    if unresolved:
        return "sql_relation_not_granted"
    if missing:
        return "ungranted:" + ",".join(missing)
    return None


def bronze_gap(sql: str, *, grantable: set[str], dialect: str) -> str | None:
    """Sheet-lane gap. The qualified relation is the existing sentence.

    A path or a table function stays kind-only. It does not carry a path.
    """
    trees = _trees(sql, dialect)
    if trees is None:
        return "ungranted:unparsed"
    if len(trees) != 1:
        return "multi_statement"
    allowed = {str(g).strip().strip('"').strip("`").lower() for g in grantable}
    for kind, token in _relations(trees[0]):
        if kind == "unresolved":
            return "sql_relation_not_granted"
        if kind == "dotted":
            if token.lower() not in allowed:
                return "sql_relation_not_granted"
            continue
        if kind == "table" and not table_is_granted(token, set(grantable)):
            return f"ungranted_table:{token}"
    return None


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
    env = build_answer_envelope(
        answer_id="ans_sql_refused",
        text=customer_abstain_text(reason),
        badge="ABSTAIN",
        abstained=True,
        rows=[],
        values=[],
        sql_used=None,
        assumptions=[f"validate:{reason}"],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route=route,
        question=question,
    )
    assert_envelope_valid(env)
    clear_engine_clock()
    return env


def _trees(sql: str, dialect: str) -> list[exp.Expression] | None:
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


_DEFAULT_SCHEMA = {
    "duckdb": "main",
    "postgres": "public",
    "tsql": "dbo",
}


def _default_schema(dialect: str) -> str:
    read = sqlglot_dialect(dialect) or ""
    return _DEFAULT_SCHEMA.get(read, "")


def _grant_keys(grantable: set[str], default: str) -> set[str]:
    """Qualified names the grant allows.

    A bare name is the default schema. ``warehouse_<table>`` is the same
    table, and so is the qualifier ``warehouse.<table>`` (the alias Cortex
    already writes). Any other schema has to be present as that qualified name.
    """
    keys: set[str] = set()
    for raw in grantable:
        token = str(raw).strip().strip('"').strip("`").strip("[]").lower()
        if not token or _slash_path(token):
            continue
        keys.add(token)
        if "." in token:
            continue
        bare = token[len("warehouse_") :] if token.startswith("warehouse_") else token
        keys.add(bare)
        keys.add(f"warehouse.{bare}")
        if default:
            keys.add(f"{default}.{bare}")
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


def _allow(
    tree: exp.Expression, grantable: set[str], dialect: str
) -> tuple[bool, list[str]]:
    if _external_read(tree):
        return True, []
    default = _default_schema(dialect)
    keys = _grant_keys(grantable, default)
    ctes = _cte_names(tree)
    unresolved = False
    missing: set[str] = set()
    for table in tree.find_all(exp.Table):
        this = table.this
        if not isinstance(this, exp.Identifier):
            unresolved = True
            continue
        name = str(table.name or "")
        if not name or _slash_path(name):
            unresolved = True
            continue
        db = str(table.db or "")
        catalog = str(table.catalog or "")
        if not db and not catalog and name.lower() in ctes:
            continue
        low = name.lower()
        # One identifier that contains a dot ("usd.book", 'hidden.csv').
        if not db and not catalog and "." in name:
            dotted = {low}
            if default:
                dotted.add(f"{default}.{low}")
            if not dotted.intersection(keys):
                unresolved = True
            continue
        schema = (db or default).lower()
        key = ".".join(part for part in (catalog.lower(), schema, low) if part)
        if key in keys:
            continue
        if catalog or (default and schema != default):
            unresolved = True
            continue
        missing.add(low)
    return unresolved, sorted(missing)
