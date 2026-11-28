"""In-memory grants for a source connected while Postgres is parked.

``dms.acl_grants`` and ``dms.data_sources`` stay empty (P-DMS-2). This book is
the stand-in the API writes and the ask path reads: process memory, same
durability as ``DemoSpaceStore`` when ``DATABASE_URL`` is unset. A restart
drops every row, and two workers do not share the book. Do not put secrets
or cell values in here.

ponytail: the grant key is the table name. Two sources that expose the same
name share one source id (``source_id_for``). Upgrade path is source id plus
table, once the parked Postgres grant tables are actually read.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

_FLAG = "DMS_SQL_SOURCE_BIND"
_ON = frozenset({"1", "true", "yes", "on"})

_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")


@dataclass(frozen=True)
class _Source:
    source_id: str
    space_id: str
    connector_id: str
    exposed: tuple[str, ...]


@dataclass(frozen=True)
class _Audit:
    action: str
    actor: str
    token_id: str
    space_id: str
    source_id: str
    tables: tuple[str, ...]
    at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "actor": self.actor,
            "token_id": self.token_id,
            "space_id": self.space_id,
            "source_id": self.source_id,
            "tables": list(self.tables),
            "at": self.at,
        }


_lock = Lock()
_sources: dict[str, _Source] = {}
_spaces: set[str] = set()
_grants: dict[str, dict[str, str]] = {}
_audit: list[_Audit] = []


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sql_source_bind_enabled() -> bool:
    """``DMS_SQL_SOURCE_BIND`` default off. On, a studio SQL land is a Space source."""
    return os.environ.get(_FLAG, "0").strip().lower() in _ON


def clean_tables(tables: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Table names only. Rejects blanks and anything that is not an identifier."""
    out: list[str] = []
    for raw in tables:
        name = str(raw).strip()
        if not _TABLE.fullmatch(name):
            raise ValueError("bad_table_name")
        if name not in out:
            out.append(name)
    return tuple(out)


def _write(
    *,
    action: str,
    actor: str,
    token_id: str,
    space_id: str,
    source_id: str,
    tables: tuple[str, ...],
) -> None:
    _audit.append(
        _Audit(
            action=action,
            actor=actor,
            token_id=token_id,
            space_id=space_id,
            source_id=source_id,
            tables=tables,
            at=_now(),
        )
    )


def register_source(
    *,
    source_id: str,
    space_id: str,
    connector_id: str,
    exposed: list[str] | tuple[str, ...],
    actor: str,
    token_id: str,
) -> tuple[str, ...]:
    names = clean_tables(list(exposed))
    with _lock:
        if source_id in _sources:
            raise ValueError("source_exists")
        _sources[source_id] = _Source(
            source_id=source_id,
            space_id=space_id,
            connector_id=connector_id,
            exposed=names,
        )
        _spaces.add(space_id)
        _write(
            action="register",
            actor=actor,
            token_id=token_id,
            space_id=space_id,
            source_id=source_id,
            tables=names,
        )
    return names


def grant_tables(
    *,
    source_id: str,
    tables: list[str] | tuple[str, ...],
    actor: str,
    token_id: str,
) -> tuple[str, ...]:
    """Grant a subset of the tables the connector listed at register time."""
    names = clean_tables(list(tables))
    with _lock:
        src = _sources.get(source_id)
        if src is None:
            raise KeyError(source_id)
        exposed = set(src.exposed)
        if any(t not in exposed for t in names):
            raise ValueError("table_not_exposed")
        held = _grants.setdefault(src.space_id, {})
        for table in names:
            held[table] = source_id
        _write(
            action="grant",
            actor=actor,
            token_id=token_id,
            space_id=src.space_id,
            source_id=source_id,
            tables=names,
        )
    return names


def revoke_tables(
    *,
    source_id: str,
    tables: list[str] | tuple[str, ...],
    actor: str,
    token_id: str,
) -> tuple[str, ...]:
    names = clean_tables(list(tables))
    with _lock:
        src = _sources.get(source_id)
        if src is None:
            raise KeyError(source_id)
        held = _grants.get(src.space_id, {})
        if any(held.get(t) != source_id for t in names):
            raise ValueError("table_not_granted")
        for table in names:
            del held[table]
        _write(
            action="revoke",
            actor=actor,
            token_id=token_id,
            space_id=src.space_id,
            source_id=source_id,
            tables=names,
        )
    return names


def is_connected_space(space_id: str) -> bool:
    with _lock:
        return space_id in _spaces


def granted_tables(space_id: str) -> tuple[str, ...]:
    with _lock:
        return tuple(sorted(_grants.get(space_id, {})))


def proposed_ungranted(space_id: str | None = None) -> set[str]:
    """Tables a registered source exposed that a steward has not granted.

    ``space_id`` set: that Space only. ``None``: exposed somewhere and granted
    by nobody, so a grant in one Space is not hidden by another Space's land.
    Used only when ``DMS_SQL_SOURCE_BIND`` is on. A landed name is not a grant.
    """
    with _lock:
        if space_id is None:
            exposed: set[str] = set()
            granted: set[str] = set()
            for src in _sources.values():
                exposed.update(src.exposed)
                held = _grants.get(src.space_id, {})
                granted.update(
                    name for name, owner in held.items() if owner == src.source_id
                )
            return exposed - granted
        out: set[str] = set()
        for src in _sources.values():
            if src.space_id != space_id:
                continue
            held = _grants.get(src.space_id, {})
            for name in src.exposed:
                if held.get(name) != src.source_id:
                    out.add(name)
        return out


def sources_for_space(space_id: str) -> list[dict[str, object]]:
    """Sources registered for this Space. Same fields the sources list shows."""
    with _lock:
        out: list[dict[str, object]] = []
        for src in _sources.values():
            if src.space_id != space_id:
                continue
            held = _grants.get(src.space_id, {})
            granted = sorted(name for name, owner in held.items() if owner == src.source_id)
            out.append(
                {
                    "id": src.source_id,
                    "kind": "sql",
                    "ref": src.connector_id,
                    "scope": "team",
                    "space_id": src.space_id,
                    "space_name": None,
                    "exposed_tables": list(src.exposed),
                    "granted_tables": granted,
                }
            )
        return out


def connected_table_names() -> set[str]:
    """Every table some connected Space currently grants."""
    with _lock:
        names: set[str] = set()
        for held in _grants.values():
            names.update(held)
        return names


def source_for(source_id: str) -> _Source | None:
    with _lock:
        return _sources.get(source_id)


def audit_rows() -> list[dict[str, object]]:
    with _lock:
        return [row.as_dict() for row in _audit]


def reset() -> None:
    """Test isolation. Production never calls this."""
    with _lock:
        _sources.clear()
        _spaces.clear()
        _grants.clear()
        _audit.clear()
