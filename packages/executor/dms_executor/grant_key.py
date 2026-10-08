"""Grant key is (source, table).

Keyed grants change only which source-qualified relations and row filters the
AI pipeline and the Cortex manifest see. This module does not plan a question,
write SQL, or execute. The AI writes the SQL. ``serve_gap`` is the checker.
For a keyed grant set it calls ``relation_allowed`` per relation.

Name comparison uses ``normalize_relation`` from ``dms_executor.grant_struct``.
There is no second normaliser here. A bare relation serves when that table is
granted from exactly one source. The same bare name from two sources is
``grant_key_ambiguous``. A qualified relation outside the set is
``grant_key_missing``. CTE names are not relations.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from dms_executor.acl import SourceGrant
from dms_executor.demo_warehouse import SERVING_DIALECT

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# UUID source ids contain hyphens. A dot is the qualifier, not part of either half.
_SOURCE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$")


class GrantKeyError(LookupError):
    """A grant lookup that is not a (source, table) key."""

    def __init__(self, code: str = "grant_key_unqualified") -> None:
        self.code = code
        super().__init__(code)


class GrantKey:
    """One readable object. ``source`` and ``table`` are both required."""

    __slots__ = ("source", "table")

    def __init__(self, source: str, table: str) -> None:
        src = str(source or "").strip()
        rel = str(table or "").strip()
        if not _SOURCE.fullmatch(src) or not _IDENT.fullmatch(rel):
            raise GrantKeyError("grant_key_unqualified")
        self.source = src
        self.table = rel

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, GrantKey)
            and self.source == other.source
            and self.table == other.table
        )

    def __hash__(self) -> int:
        return hash((self.source, self.table))

    def __repr__(self) -> str:
        return f"GrantKey({self.source!r}, {self.table!r})"


def grants_are_keyed(grants: Sequence[SourceGrant]) -> bool:
    """True when any grant names its source. Legacy seed grants do not."""
    return any(getattr(g, "source_name", None) for g in grants)


def resolve_grant(
    grants: Sequence[SourceGrant],
    table: str,
    source: str | None = None,
) -> GrantKey:
    """Resolve one key. A missing source argument is ``grant_key_unqualified``.

    A source that does not hold ``table`` is ``grant_key_missing``.
    """
    raw_table = str(table or "").strip()
    raw_source = "" if source is None else str(source).strip()
    if not raw_source or "." in raw_table or not raw_table:
        raise GrantKeyError("grant_key_unqualified")
    try:
        key = GrantKey(raw_source, raw_table)
    except GrantKeyError:
        raise
    if key not in _keys(grants):
        raise GrantKeyError("grant_key_missing")
    return key


def split_grant_name(name: str) -> tuple[str, str] | None:
    """``source.table`` when both halves are identifiers. Else None.

    A UUID source is an identifier here (hyphens allowed). A bare table name
    is not split.
    """
    schema, dot, table = str(name or "").partition(".")
    if not dot or "." in table:
        return None
    if not _SOURCE.fullmatch(schema) or not _IDENT.fullmatch(table):
        return None
    return schema, table


def keys_from_grantable(grantable: set[str]) -> frozenset[GrantKey] | None:
    """Keyed grant set, or None when any name is still a legacy bare table.

    None keeps the existing bare-name checker. A mixed set is legacy so a
    dotted table name that is not a source key is not re-read as one.
    """
    if not grantable:
        return None
    found: list[GrantKey] = []
    for name in grantable:
        split = split_grant_name(str(name))
        if split is None:
            return None
        try:
            found.append(GrantKey(split[0], split[1]))
        except GrantKeyError:
            return None
    return frozenset(found)


def serve_gap_grantable(keys: frozenset[GrantKey]) -> set[str]:
    """Qualified relations ``serve_gap(grantable=...)`` is given.

    Bare names are not members. A bare citation is decided by
    ``relation_allowed``, which ``serve_gap`` calls instead of stripping the
    schema off the grant.
    """
    return {f"{key.source}.{key.table}" for key in keys}


def keyed_gap(
    qualified_key: str,
    *,
    bare_table: str | None,
    keys: frozenset[GrantKey],
    dialect: str,
) -> str | None:
    """None when the relation is granted. Else ``grant_key_missing`` or
    ``grant_key_ambiguous``.

    ``qualified_key`` is the key ``normalize_relation`` already produced.
    ``bare_table`` is set only for a citation that had no schema. One source
    for that table is granted. Two sources are ambiguous. A qualified key
    matches the whole source.table or it is missing.
    """
    if bare_table is None:
        if any(_grant_relation_key(key, dialect) == qualified_key for key in keys):
            return None
        return "grant_key_missing"
    found = [key for key in keys if _grant_table_tail(key, dialect) == bare_table]
    if len(found) == 1:
        return None
    if len(found) > 1:
        return "grant_key_ambiguous"
    return "grant_key_missing"


def relation_allowed(
    schema: str | None,
    table: str,
    keys: frozenset[GrantKey],
    *,
    dialect: str | None = None,
) -> str:
    """``ok``, ``grant_key_missing``, or ``grant_key_ambiguous``.

    The grant-set decision ``serve_gap`` calls for one parsed relation. CTE
    names are not passed in. A bare table with one granting source is ``ok``.
    Two granting sources are ``grant_key_ambiguous``. A qualifier that is not
    a granted source is ``grant_key_missing``. Comparison is
    ``normalize_relation``.
    """
    fold = dialect or SERVING_DIALECT
    if schema:
        folded = _relation_key(str(schema), str(table), fold)
        if folded is None:
            return "grant_key_unqualified"
        return keyed_gap(folded, bare_table=None, keys=keys, dialect=fold) or "ok"
    tail = _bare_tail(str(table or ""), fold)
    if tail is None:
        return "grant_key_unqualified"
    return keyed_gap("", bare_table=tail, keys=keys, dialect=fold) or "ok"


def check_grant_sql(sql: str, keys: frozenset[GrantKey]) -> str:
    """``ok`` or the ``serve_gap`` refusal. Does not execute."""
    from dms_executor.grant_struct import serve_gap

    gap = serve_gap(sql, grantable=serve_gap_grantable(keys), dialect=SERVING_DIALECT)
    return gap or "ok"


def qualify_granted_sql(
    sql: str, grantable: set[str], *, dialect: str | None = None
) -> str:
    """Qualify a single-source bare name so the manifest key binds.

    A legacy set is unchanged. A refusal is left for ``serve_gap``; this
    function does not decide one.
    """
    keys = keys_from_grantable(grantable)
    if keys is None:
        return sql
    fold = dialect or SERVING_DIALECT
    rewritten, code = _decide(sql, keys, fold)
    if code != "ok":
        return sql
    return rewritten


def migrate_seed(
    seed: dict[str, tuple[str, tuple[str, ...]]],
) -> dict[str, frozenset[GrantKey]]:
    """Map the bare-name seed onto (source, table) keys.

    Each existing table already had one source id, ``source_id_for(table)``.
    That id becomes the source half. The table half is the same name the
    Space could already read, so the readable set does not change. New
    sources must not reuse this id: it is derived from the table name alone
    and would collide.
    """
    from dms_executor.demo_grants import source_id_for

    out: dict[str, frozenset[GrantKey]] = {}
    for space_id, (_name, tables) in seed.items():
        out[space_id] = frozenset(
            GrantKey(str(source_id_for(table)), table) for table in tables
        )
    return out


def readable_tables(keys: frozenset[GrantKey]) -> frozenset[str]:
    """Table names a migrated key set can read. Source is not part of the set."""
    return frozenset(key.table for key in keys)


def _keys(grants: Sequence[SourceGrant]) -> frozenset[GrantKey]:
    found: list[GrantKey] = []
    for grant in grants:
        source_name = getattr(grant, "source_name", None)
        table = grant.table_name
        if not source_name or not table or "." in str(table):
            continue
        try:
            found.append(GrantKey(str(source_name), str(table)))
        except GrantKeyError:
            continue
    return frozenset(found)


def _render(schema: str | None, table: str) -> str:
    """Quote a hyphenated source so ``normalize_relation`` can parse it."""
    from sqlglot import exp

    rel = exp.to_identifier(table, quoted=_IDENT.fullmatch(table) is None)
    if not schema:
        return str(rel.sql())
    src = exp.to_identifier(schema, quoted=_IDENT.fullmatch(schema) is None)
    return f"{src.sql()}.{rel.sql()}"


def _relation_key(schema: str | None, table: str, dialect: str) -> str | None:
    from dms_executor.grant_struct import normalize_relation

    return normalize_relation(_render(schema, table), dialect=dialect)


def _bare_tail(token: str, dialect: str) -> str | None:
    folded = _relation_key(None, str(token or "").strip(), dialect)
    if not folded:
        return None
    return folded.rsplit(".", 1)[-1]


def _grant_relation_key(key: GrantKey, dialect: str) -> str | None:
    return _relation_key(key.source, key.table, dialect)


def _grant_table_tail(key: GrantKey, dialect: str) -> str | None:
    folded = _grant_relation_key(key, dialect)
    if not folded:
        return None
    return folded.rsplit(".", 1)[-1]


def _ident(name: str) -> Any:
    from sqlglot import exp

    return exp.to_identifier(name, quoted=_IDENT.fullmatch(name) is None)


def _holders(
    schema: str | None,
    table: str,
    keys: frozenset[GrantKey],
    dialect: str,
) -> list[GrantKey] | str:
    if schema:
        want = _relation_key(schema, table, dialect)
        if want is None:
            return "grant_key_unqualified"
        return [key for key in keys if _grant_relation_key(key, dialect) == want]
    tail = _bare_tail(table, dialect)
    if tail is None:
        return "grant_key_unqualified"
    return [key for key in keys if _grant_table_tail(key, dialect) == tail]


def _decide(sql: str, keys: frozenset[GrantKey], dialect: str) -> tuple[str, str]:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import SqlglotError

    from dms_executor.grant_struct import sqlglot_dialect

    read = sqlglot_dialect(dialect)
    if read is None:
        return sql, "grant_key_unqualified"
    try:
        trees = sqlglot.parse(sql or "", read=read)
    except SqlglotError:
        return sql, "grant_key_unqualified"
    if len(trees) != 1 or trees[0] is None:
        return sql, "grant_key_unqualified"
    tree = trees[0]
    ctes = {
        tail
        for cte in tree.find_all(exp.CTE)
        if (tail := _bare_tail(str(cte.alias or ""), dialect))
    }
    changed = False
    saw = False
    for table in list(tree.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            return sql, "grant_key_unqualified"
        name = str(table.name or "")
        if not name:
            return sql, "grant_key_unqualified"
        if not table.db and _bare_tail(name, dialect) in ctes:
            continue
        saw = True
        schema = str(table.db) if table.db else None
        found = _holders(schema, name, keys, dialect)
        if isinstance(found, str):
            return sql, found
        if schema is None:
            if len(found) != 1:
                code = "grant_key_ambiguous" if len(found) > 1 else "grant_key_missing"
                return sql, code
            match = found[0]
            table.set("db", _ident(match.source))
            table.set("this", _ident(match.table))
            changed = True
        elif not found:
            return sql, "grant_key_missing"
        elif schema != found[0].source or name != found[0].table:
            table.set("db", _ident(found[0].source))
            table.set("this", _ident(found[0].table))
            changed = True
    if not saw:
        return sql, "grant_key_unqualified"
    if not changed:
        return sql, "ok"
    return tree.sql(dialect=read), "ok"
