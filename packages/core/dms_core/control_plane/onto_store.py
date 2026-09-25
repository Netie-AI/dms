"""Durable versioned ontology store (ONTO-STORE-01 / dms#279).

Netie's own control-plane store. Extract-only (DR-0005): no outbound network,
no Cortex or LLM calls, no keys. Source identity is kind + host + database +
schema. Never credentials, connection strings, or passwords.

Thin API: load_active, load_by_version, list_versions. The only write is
reconnect() creating a new version (bootstrap active, or proposed on fingerprint
change) plus one onto_audit row. Confirm/reject belongs to the next ticket.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg

VersionStatus = Literal["proposed", "active", "superseded"]
ElementState = Literal["proposed", "confirmed", "rejected"]
DeclaredCardinality = Literal["one-to-one", "one-to-many"]
MeasuredCardinality = Literal["many_to_one", "many_to_many", "unverified"]

IDENTITY_FIELDS = ("kind", "host", "database", "schema")
FORBIDDEN_IDENTITY_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "token",
        "api_key",
        "apikey",
        "connection_string",
        "conn_str",
        "connstring",
        "dsn",
        "user",
        "username",
        "private_key",
    }
)
_CRED_IN_VALUE = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key)\s*="
    r"|(?://[^/\s]*:[^/\s]*@)"
)

_VERSION_SELECT = """
id, tenant_id, space_id, source_kind, source_host, source_database, source_schema,
schema_fingerprint, status, created_by, created_at
"""


@dataclass(frozen=True)
class SchemaColumn:
    name: str
    data_type: str


@dataclass(frozen=True)
class SchemaTable:
    name: str
    columns: tuple[SchemaColumn, ...]
    primary_key: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchemaForeignKey:
    name: str
    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]


@dataclass(frozen=True)
class SourceIdentity:
    """Kind + host + database + schema. No credentials, no connection string."""

    kind: str
    host: str
    database: str
    schema: str

    def __post_init__(self) -> None:
        for field in IDENTITY_FIELDS:
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"source identity {field} is required")
            if _CRED_IN_VALUE.search(value):
                raise ValueError("source identity must not contain credentials")
        object.__setattr__(self, "kind", self.kind.strip())
        object.__setattr__(self, "host", self.host.strip())
        object.__setattr__(self, "database", self.database.strip())
        object.__setattr__(self, "schema", self.schema.strip())

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SourceIdentity:
        extra = set(data) - set(IDENTITY_FIELDS)
        if extra:
            raise ValueError(
                "source identity rejects extra keys (never credentials): "
                + ", ".join(sorted(str(k) for k in extra))
            )
        lowered = {str(k).casefold() for k in data}
        if lowered & FORBIDDEN_IDENTITY_KEYS:
            raise ValueError("source identity must not contain credentials")
        return cls(
            kind=str(data["kind"]),
            host=str(data["host"]),
            database=str(data["database"]),
            schema=str(data["schema"]),
        )


@dataclass(frozen=True)
class OntologyVersion:
    id: UUID
    space_id: UUID
    identity: SourceIdentity
    schema_fingerprint: str
    status: VersionStatus
    created_by: UUID | None
    created_at: datetime
    tenant_id: UUID | None = None


@dataclass(frozen=True)
class OntoAudit:
    id: UUID
    version_id: UUID | None
    actor_user_id: UUID | None
    action_type: str
    inputs: dict[str, Any]
    result: dict[str, Any]
    created_at: datetime
    tenant_id: UUID | None = None


@dataclass(frozen=True)
class ReconnectDecision:
    action: Literal["reuse", "create"]
    status: VersionStatus | None
    existing: OntologyVersion | None


def _norm_type(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _norm_name(value: str) -> str:
    return value.strip()


def schema_fingerprint(
    tables: Sequence[SchemaTable],
    foreign_keys: Sequence[SchemaForeignKey] = (),
) -> str:
    """Stable hash of tables, columns, types, PKs, FKs. Column order does not matter."""
    payload = {
        "tables": [
            {
                "name": _norm_name(table.name),
                "columns": [
                    {
                        "name": _norm_name(col.name),
                        "type": _norm_type(col.data_type),
                    }
                    for col in sorted(table.columns, key=lambda c: c.name.casefold())
                ],
                "primary_key": [_norm_name(col) for col in table.primary_key],
            }
            for table in sorted(tables, key=lambda t: t.name.casefold())
        ],
        "foreign_keys": [
            {
                "name": _norm_name(fk.name),
                "from_table": _norm_name(fk.from_table),
                "from_columns": [_norm_name(col) for col in fk.from_columns],
                "to_table": _norm_name(fk.to_table),
                "to_columns": [_norm_name(col) for col in fk.to_columns],
            }
            for fk in sorted(
                foreign_keys,
                key=lambda item: (
                    item.from_table.casefold(),
                    item.to_table.casefold(),
                    item.name.casefold(),
                    tuple(col.casefold() for col in item.from_columns),
                ),
            )
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def decide_reconnect(
    versions: Sequence[OntologyVersion], fingerprint: str
) -> ReconnectDecision:
    """Same fingerprint -> reuse. First snapshot -> active. Changed -> proposed."""
    for version in versions:
        if version.schema_fingerprint == fingerprint:
            return ReconnectDecision("reuse", None, version)
    if not versions:
        return ReconnectDecision("create", "active", None)
    return ReconnectDecision("create", "proposed", None)


def _audit_payload(
    *,
    space_id: UUID,
    identity: SourceIdentity,
    fingerprint: str,
    version_id: UUID,
    status: VersionStatus,
) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = {
        "space_id": str(space_id),
        "kind": identity.kind,
        "host": identity.host,
        "database": identity.database,
        "schema": identity.schema,
        "schema_fingerprint": fingerprint,
    }
    result = {"version_id": str(version_id), "status": status}
    return inputs, result


class OntologyStore:
    """In-memory store for CI. Same reconnect rule as the Postgres functions below."""

    def __init__(self, *, tenant_id: UUID | None = None) -> None:
        self._tenant_id = tenant_id
        self._versions: list[OntologyVersion] = []
        self._audit: list[OntoAudit] = []

    def load_active(
        self, space_id: UUID, identity: SourceIdentity
    ) -> OntologyVersion | None:
        found = [
            v
            for v in self._versions
            if v.space_id == space_id and v.identity == identity and v.status == "active"
        ]
        return found[0] if found else None

    def load_by_version(self, version_id: UUID) -> OntologyVersion | None:
        for version in self._versions:
            if version.id == version_id:
                return version
        return None

    def list_versions(
        self, space_id: UUID, identity: SourceIdentity
    ) -> list[OntologyVersion]:
        found = [
            v for v in self._versions if v.space_id == space_id and v.identity == identity
        ]
        return sorted(found, key=lambda v: v.created_at)

    def reconnect(
        self,
        *,
        space_id: UUID,
        identity: SourceIdentity,
        fingerprint: str,
        created_by: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> OntologyVersion:
        versions = self.list_versions(space_id, identity)
        decision = decide_reconnect(versions, fingerprint)
        if decision.action == "reuse":
            assert decision.existing is not None
            return decision.existing
        assert decision.status is not None
        tid = tenant_id or self._tenant_id
        version = OntologyVersion(
            id=uuid4(),
            space_id=space_id,
            identity=identity,
            schema_fingerprint=fingerprint,
            status=decision.status,
            created_by=created_by,
            created_at=datetime.now(UTC),
            tenant_id=tid,
        )
        self._versions.append(version)
        inputs, result = _audit_payload(
            space_id=space_id,
            identity=identity,
            fingerprint=fingerprint,
            version_id=version.id,
            status=version.status,
        )
        self._audit.append(
            OntoAudit(
                id=uuid4(),
                version_id=version.id,
                actor_user_id=created_by,
                action_type="reconnect",
                inputs=inputs,
                result=result,
                created_at=version.created_at,
                tenant_id=tid,
            )
        )
        return version


def _row_to_version(row: Any) -> OntologyVersion:
    return OntologyVersion(
        id=UUID(str(row[0])),
        tenant_id=UUID(str(row[1])) if row[1] is not None else None,
        space_id=UUID(str(row[2])),
        identity=SourceIdentity(
            kind=str(row[3]),
            host=str(row[4]),
            database=str(row[5]),
            schema=str(row[6]),
        ),
        schema_fingerprint=str(row[7]),
        status=row[8],
        created_by=UUID(str(row[9])) if row[9] is not None else None,
        created_at=row[10],
    )


def load_active(
    conn: psycopg.Connection,
    *,
    space_id: UUID | str,
    identity: SourceIdentity,
) -> OntologyVersion | None:
    row = conn.execute(
        f"""
        SELECT {_VERSION_SELECT}
          FROM dms.ontology_version
         WHERE space_id = %s
           AND source_kind = %s
           AND source_host = %s
           AND source_database = %s
           AND source_schema = %s
           AND status = 'active'
         LIMIT 1
        """,
        (str(space_id), identity.kind, identity.host, identity.database, identity.schema),
    ).fetchone()
    return _row_to_version(row) if row is not None else None


def load_by_version(
    conn: psycopg.Connection, version_id: UUID | str
) -> OntologyVersion | None:
    row = conn.execute(
        f"""
        SELECT {_VERSION_SELECT}
          FROM dms.ontology_version
         WHERE id = %s
        """,
        (str(version_id),),
    ).fetchone()
    return _row_to_version(row) if row is not None else None


def list_versions(
    conn: psycopg.Connection,
    *,
    space_id: UUID | str,
    identity: SourceIdentity,
) -> list[OntologyVersion]:
    rows = conn.execute(
        f"""
        SELECT {_VERSION_SELECT}
          FROM dms.ontology_version
         WHERE space_id = %s
           AND source_kind = %s
           AND source_host = %s
           AND source_database = %s
           AND source_schema = %s
         ORDER BY created_at ASC
        """,
        (str(space_id), identity.kind, identity.host, identity.database, identity.schema),
    ).fetchall()
    return [_row_to_version(row) for row in rows]


def reconnect(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID | str,
    space_id: UUID | str,
    identity: SourceIdentity,
    fingerprint: str,
    created_by: UUID | str | None = None,
) -> OntologyVersion:
    """Reuse the version for this identity+fingerprint, or insert a new one + audit.

    Never UPDATE an existing ontology_version row in place.
    """
    space = UUID(str(space_id))
    versions = list_versions(conn, space_id=space, identity=identity)
    decision = decide_reconnect(versions, fingerprint)
    if decision.action == "reuse":
        assert decision.existing is not None
        return decision.existing
    assert decision.status is not None
    version_id = uuid4()
    actor = str(created_by) if created_by is not None else None
    conn.execute(
        """
        INSERT INTO dms.ontology_version (
          id, tenant_id, space_id,
          source_kind, source_host, source_database, source_schema,
          schema_fingerprint, status, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(version_id),
            str(tenant_id),
            str(space),
            identity.kind,
            identity.host,
            identity.database,
            identity.schema,
            fingerprint,
            decision.status,
            actor,
        ),
    )
    inputs, result = _audit_payload(
        space_id=space,
        identity=identity,
        fingerprint=fingerprint,
        version_id=version_id,
        status=decision.status,
    )
    conn.execute(
        """
        INSERT INTO dms.onto_audit (
          tenant_id, version_id, actor_user_id, action_type, inputs, result
        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
        """,
        (
            str(tenant_id),
            str(version_id),
            actor,
            "reconnect",
            json.dumps(inputs),
            json.dumps(result),
        ),
    )
    loaded = load_by_version(conn, version_id)
    assert loaded is not None
    return loaded
