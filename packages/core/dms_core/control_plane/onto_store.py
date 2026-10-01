"""Durable versioned ontology store (ONTO-STORE-01 / dms#279).

Netie's own control-plane store. Extract-only (DR-0005): no outbound network,
no Cortex or LLM calls, no keys. Source identity is kind + host + database +
schema. Never credentials, connection strings, or passwords.

Thin API: load_active, load_by_version, list_versions. reconnect() creates a
new version (bootstrap active, or proposed on fingerprint change) plus one
onto_audit row.

ONTO-CONFIRM-01 (dms#283) adds the measure rows: add_measures (proposals and
manual entries) and decide_measure, the single write path for confirm and reject
(ledger callable first, then ONE UPDATE, audit and ledger pointer in the same
transaction). Migration 0006 freezes the definition with a BEFORE UPDATE trigger;
MEASURE_TRANSITIONS below is its Python mirror.

ONTO-DERIVE-01 (dms#277 CONNECT-ASK-01 change 1) adds the measurement: a
snapshot is the derived ontology body plus what ``Ontology.verify`` found,
appended per derive (never updated), so a re-derive after the data changed is a
new row and the latest one is what the ask path reads. The body carries names,
keys and types only; never rows, never credentials.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
import psycopg.errors

from dms_core.control_plane.session import AppRole

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


def measure_definition_hash(
    name: str, grain: str, aggregate: str, column: str, description: str
) -> str:
    """sha256 over the canonical JSON of one measure definition.

    Our own hash over our own object (not a manifest signature): the single
    home of the formula; the executor imports it.
    """
    payload = {
        "v": 1,
        "name": name,
        "grain": grain,
        "aggregate": aggregate,
        "column": column,
        "description": description,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


# --------------------------------------------------------------------------
# measures (ONTO-CONFIRM-01)
# --------------------------------------------------------------------------

#: The four legal moves; the trigger in migration 0006 enforces the same set.
#: Never: confirmed -> proposed, rejected -> proposed, a same-state UPDATE.
MEASURE_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("proposed", "confirmed"),
        ("proposed", "rejected"),
        ("confirmed", "rejected"),
        ("rejected", "confirmed"),
    }
)
MEASURE_STATES = ("proposed", "confirmed", "rejected")
_MEASURE_AGGREGATES = frozenset({"count", "count_distinct", "sum", "avg", "min", "max"})
_MEASURE_SOURCES = frozenset({"derived", "manual"})
_MEASURE_NAME_RE = re.compile(r"[a-z][a-z0-9_]{1,47}")
_MEASURE_DESC_MAX = 200
_MEASURE_AUDIT_ADD = frozenset({"measure.propose", "measure.create"})


def measure_transition_ok(old: str, new: str) -> bool:
    """True iff ``old -> new`` is one of the four legal measure transitions."""
    return (old, new) in MEASURE_TRANSITIONS


class MeasureNotFound(LookupError):
    """No such measure in this Space (or tenant)."""


class MeasureTransitionError(RuntimeError):
    """A decide that cannot proceed. ``code`` is machine-readable.

    illegal_transition | stale_definition | definition_frozen | concurrent_change.
    ``current`` is the row as the store saw it, when there is one.
    """

    def __init__(self, code: str, message: str, current: OntoMeasure | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.current = current


class MeasureNameTaken(ValueError):
    """The (version, name) pair already exists (rename at confirm collided)."""


@dataclass(frozen=True)
class MeasureDraft:
    """The whole of one measure definition, as proposed or entered by hand."""

    name: str
    grain: str
    aggregate: str
    column: str
    description: str
    source: str = "derived"


@dataclass(frozen=True)
class OntoMeasure:
    id: UUID
    tenant_id: UUID | None
    version_id: UUID
    name: str
    grain: str
    aggregate: str
    column_name: str
    description: str
    state: ElementState
    source: str
    definition_hash: str
    schema_fingerprint: str
    evidence: dict[str, Any]
    decided_by: UUID | None
    decided_at: datetime | None
    decision_reason: str | None
    ledger_entry_id: str | None
    # Join-only: the owning version, so a reader can tell active from drifted.
    version_status: str = ""
    version_fingerprint: str = ""


def _check_draft(draft: MeasureDraft) -> None:
    """Re-check the shape here so a caller that skipped the executor validator
    still cannot store garbage (the DB CHECKs are the last line, not the first)."""
    if not _MEASURE_NAME_RE.fullmatch(draft.name):
        raise ValueError("measure name must match ^[a-z][a-z0-9_]{1,47}$")
    if draft.aggregate not in _MEASURE_AGGREGATES:
        raise ValueError("measure aggregate must be count, count_distinct, sum, avg, min or max")
    if draft.column == "*" and draft.aggregate != "count":
        raise ValueError("column '*' is only valid with aggregate count")
    if not draft.column or not draft.grain:
        raise ValueError("measure grain and column are required")
    if draft.source not in _MEASURE_SOURCES:
        raise ValueError("measure source must be 'derived' or 'manual'")
    if len(draft.description) > _MEASURE_DESC_MAX:
        raise ValueError("measure description is at most 200 characters")


def _draft_hash(draft: MeasureDraft) -> str:
    return measure_definition_hash(
        draft.name, draft.grain, draft.aggregate, draft.column, draft.description
    )


def _clean_json(value: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(dict(value), default=str))
    return out


def _plan_decision(
    row: OntoMeasure,
    *,
    to_state: str,
    expected_hash: str,
    name: str | None,
    description: str | None,
    reason: str | None,
    evidence: Mapping[str, Any] | None,
) -> OntoMeasure | None:
    """The row ``decide_measure`` would write, or None for an idempotent no-op.

    Pure: no I/O, shared by the memory and Postgres stores so they cannot drift.
    Raises MeasureTransitionError / ValueError before anything is written.
    """
    if to_state not in MEASURE_STATES:
        raise ValueError(f"unknown measure state {to_state!r}")
    if row.definition_hash != expected_hash:
        raise MeasureTransitionError(
            "stale_definition", "the measure changed since it was read", row
        )
    new_name = row.name if name is None else name
    new_desc = row.description if description is None else description
    reworded = new_name != row.name or new_desc != row.description
    if row.state == to_state:
        if reworded or evidence is not None:
            raise MeasureTransitionError(
                "definition_frozen", f"a {row.state} measure cannot be reworded", row
            )
        return None
    if not measure_transition_ok(row.state, to_state):
        raise MeasureTransitionError(
            "illegal_transition", f"{row.state} -> {to_state} is not allowed", row
        )
    if to_state == "rejected":
        if reworded or evidence is not None:
            raise MeasureTransitionError(
                "definition_frozen", "rejecting cannot change the definition", row
            )
        if not (reason or "").strip():
            raise ValueError("a rejection needs a reason")
        return replace(row, state="rejected", decision_reason=reason)
    # -> confirmed: a rename and a rewording are allowed, the spec is not.
    if not _MEASURE_NAME_RE.fullmatch(new_name):
        raise ValueError("measure name must match ^[a-z][a-z0-9_]{1,47}$")
    if not new_desc.strip():
        raise ValueError("a confirmed measure needs a description")
    if len(new_desc) > _MEASURE_DESC_MAX:
        raise ValueError("measure description is at most 200 characters")
    return replace(
        row,
        state="confirmed",
        name=new_name,
        description=new_desc,
        definition_hash=measure_definition_hash(
            new_name, row.grain, row.aggregate, row.column_name, new_desc
        ),
        evidence=row.evidence if evidence is None else _clean_json(evidence),
        decision_reason=reason if reason else None,
    )


def _ledger_payload(row: OntoMeasure, new: OntoMeasure, space_id: UUID | None) -> dict[str, Any]:
    """Pointers only: names, hashes, ids. No rows, no values, no reason text."""
    return {
        "space_id": str(space_id) if space_id is not None else None,
        "version_id": str(new.version_id),
        "measure_id": str(new.id),
        "name": new.name,
        "grain": new.grain,
        "aggregate": new.aggregate,
        "column": new.column_name,
        "definition_hash": new.definition_hash,
        "schema_fingerprint": new.schema_fingerprint,
        "snapshot_id": new.evidence.get("snapshot_id"),
        "from_state": row.state,
        "to_state": new.state,
    }


def _decide_audit(
    row: OntoMeasure, new: OntoMeasure, expected_hash: str, entry_id: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    action = "measure.confirm" if new.state == "confirmed" else "measure.reject"
    inputs = {
        "measure_id": str(new.id),
        "name": new.name,
        "definition_hash": new.definition_hash,
        "expected_hash": expected_hash,
        "schema_fingerprint": new.schema_fingerprint,
        "snapshot_id": new.evidence.get("snapshot_id"),
        "from_state": row.state,
    }
    result = {"to_state": new.state, "ledger_entry_id": entry_id}
    return action, inputs, result


def _add_audit(
    action: str, version: OntologyVersion, inserted: Sequence[OntoMeasure], skipped: int,
    snapshot_id: UUID | str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = {
        "version_id": str(version.id),
        "schema_fingerprint": version.schema_fingerprint,
        "snapshot_id": str(snapshot_id) if snapshot_id is not None else None,
    }
    result = {
        "inserted": [m.name for m in inserted],
        "skipped": skipped,
    }
    return inputs, result


@dataclass(frozen=True)
class OntologySnapshot:
    """One measurement of a version: the derived body and what verify() found.

    ``violations`` are ``{check, subject, detail}``. ``verified`` is True only
    when verify() ran and found nothing; a failed subject stays unusable.
    """

    id: UUID
    version_id: UUID
    body: dict[str, Any]
    violations: tuple[dict[str, str], ...]
    verified: bool
    measured_at: datetime
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
        self._snapshots: list[OntologySnapshot] = []
        self._measures: list[OntoMeasure] = []
        # One store serves every request thread of the API process.
        self._lock = threading.RLock()

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
        with self._lock:
            return self._reconnect(
                space_id=space_id,
                identity=identity,
                fingerprint=fingerprint,
                created_by=created_by,
                tenant_id=tenant_id,
            )

    def _reconnect(
        self,
        *,
        space_id: UUID,
        identity: SourceIdentity,
        fingerprint: str,
        created_by: UUID | None,
        tenant_id: UUID | None,
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

    def active_for_space(self, space_id: UUID) -> list[OntologyVersion]:
        """Every source's active version for one Space. Never another Space's."""
        with self._lock:
            found = [
                v for v in self._versions if v.space_id == space_id and v.status == "active"
            ]
        return sorted(found, key=lambda v: v.created_at)

    def record_snapshot(
        self,
        version_id: UUID,
        *,
        body: Mapping[str, Any],
        violations: Sequence[Mapping[str, str]],
        verified: bool,
        actor: UUID | None = None,
    ) -> OntologySnapshot:
        """Append one measurement of ``version_id`` (never an update) + audit."""
        with self._lock:
            version = self.load_by_version(version_id)
            if version is None:
                raise KeyError(f"unknown ontology version {version_id}")
            snap = _new_snapshot(version, body, violations, verified)
            self._snapshots.append(snap)
            inputs, result = _snapshot_audit(snap)
            self._audit.append(
                OntoAudit(
                    id=uuid4(),
                    version_id=version_id,
                    actor_user_id=actor,
                    action_type="verify",
                    inputs=inputs,
                    result=result,
                    created_at=snap.measured_at,
                    tenant_id=version.tenant_id,
                )
            )
            return snap

    def latest_snapshot(self, version_id: UUID) -> OntologySnapshot | None:
        with self._lock:
            found = [s for s in self._snapshots if s.version_id == version_id]
        return found[-1] if found else None

    # ---- measures (ONTO-CONFIRM-01) -------------------------------------

    @property
    def persistent(self) -> bool:
        """False: a confirmation held here is lost on restart."""
        return False

    def _with_version(self, m: OntoMeasure) -> OntoMeasure:
        v = self.load_by_version(m.version_id)
        if v is None:
            return m
        return replace(m, version_status=v.status, version_fingerprint=v.schema_fingerprint)

    def measures_for_space(
        self,
        space_id: UUID,
        *,
        states: Sequence[str] | None = None,
        active_only: bool = False,
    ) -> list[OntoMeasure]:
        with self._lock:
            out: list[OntoMeasure] = []
            for m in self._measures:
                v = self.load_by_version(m.version_id)
                if v is None or v.space_id != space_id:
                    continue
                if active_only and v.status != "active":
                    continue
                if states is not None and m.state not in states:
                    continue
                out.append(self._with_version(m))
            vs = {v.id: v.created_at for v in self._versions}
            return sorted(out, key=lambda m: (vs.get(m.version_id), m.name))

    def get_measure(self, space_id: UUID, measure_id: UUID) -> OntoMeasure | None:
        """Space-scoped: an id that belongs to another Space is simply absent."""
        with self._lock:
            for m in self._measures:
                if m.id == measure_id:
                    v = self.load_by_version(m.version_id)
                    if v is not None and v.space_id == space_id:
                        return self._with_version(m)
        return None

    def add_measures(
        self,
        version_id: UUID,
        drafts: Sequence[MeasureDraft],
        evidences: Sequence[Mapping[str, Any]],
        *,
        actor: UUID | None,
        snapshot_id: UUID | str | None,
        action: str,
    ) -> tuple[list[OntoMeasure], list[MeasureDraft]]:
        """Insert PROPOSED rows; an existing (version, name) is skipped, never touched."""
        if action not in _MEASURE_AUDIT_ADD:
            raise ValueError(f"unknown measure add action {action!r}")
        if len(drafts) != len(evidences):
            raise ValueError("one evidence mapping per draft")
        for d in drafts:
            _check_draft(d)
        with self._lock:
            version = self.load_by_version(version_id)
            if version is None:
                raise KeyError(f"unknown ontology version {version_id}")
            taken = {m.name for m in self._measures if m.version_id == version_id}
            inserted: list[OntoMeasure] = []
            skipped: list[MeasureDraft] = []
            for draft, evidence in zip(drafts, evidences, strict=True):
                if draft.name in taken:
                    skipped.append(draft)
                    continue
                taken.add(draft.name)
                row = OntoMeasure(
                    id=uuid4(),
                    tenant_id=version.tenant_id,
                    version_id=version_id,
                    name=draft.name,
                    grain=draft.grain,
                    aggregate=draft.aggregate,
                    column_name=draft.column,
                    description=draft.description,
                    state="proposed",
                    source=draft.source,
                    definition_hash=_draft_hash(draft),
                    schema_fingerprint=version.schema_fingerprint,
                    evidence=_clean_json(evidence),
                    decided_by=None,
                    decided_at=None,
                    decision_reason=None,
                    ledger_entry_id=None,
                )
                self._measures.append(row)
                inserted.append(self._with_version(row))
            inputs, result = _add_audit(action, version, inserted, len(skipped), snapshot_id)
            self._audit.append(
                OntoAudit(
                    id=uuid4(),
                    version_id=version_id,
                    actor_user_id=actor,
                    action_type=action,
                    inputs=inputs,
                    result=result,
                    created_at=datetime.now(UTC),
                    tenant_id=version.tenant_id,
                )
            )
            return inserted, skipped

    def decide_measure(
        self,
        measure_id: UUID,
        *,
        to_state: str,
        expected_hash: str,
        name: str | None = None,
        description: str | None = None,
        reason: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        actor: UUID,
        ledger: Callable[[dict[str, Any]], str],
    ) -> tuple[OntoMeasure, bool]:
        """THE write path for confirm and reject. The ledger runs first; only
        when it returns an entry id is the new row swapped in (one step), so a
        raising ledger leaves the state exactly as it was."""
        with self._lock:
            idx = next((i for i, m in enumerate(self._measures) if m.id == measure_id), None)
            if idx is None:
                raise MeasureNotFound(str(measure_id))
            row = self._with_version(self._measures[idx])
            planned = _plan_decision(
                row,
                to_state=to_state,
                expected_hash=expected_hash,
                name=name,
                description=description,
                reason=reason,
                evidence=evidence,
            )
            if planned is None:
                return row, False
            version = self.load_by_version(row.version_id)
            if any(
                m.version_id == row.version_id and m.name == planned.name and m.id != row.id
                for m in self._measures
            ):
                raise MeasureNameTaken(planned.name)
            space_id = version.space_id if version is not None else None
            entry_id = ledger(_ledger_payload(row, planned, space_id))
            if not entry_id:
                raise ValueError("the ledger returned no entry id")
            now = datetime.now(UTC)
            new = replace(planned, decided_by=actor, decided_at=now, ledger_entry_id=entry_id)
            action, inputs, result = _decide_audit(row, new, expected_hash, entry_id)
            self._measures[idx] = replace(new, version_status="", version_fingerprint="")
            self._audit.append(
                OntoAudit(
                    id=uuid4(),
                    version_id=new.version_id,
                    actor_user_id=actor,
                    action_type=action,
                    inputs=inputs,
                    result=result,
                    created_at=now,
                    tenant_id=new.tenant_id,
                )
            )
            return self._with_version(new), True

    def record_refusal(
        self,
        measure_id: UUID,
        *,
        failed: Sequence[str],
        actor: UUID | None,
        from_state: str,
    ) -> None:
        with self._lock:
            row = next((m for m in self._measures if m.id == measure_id), None)
            if row is None:
                raise MeasureNotFound(str(measure_id))
            self._audit.append(
                OntoAudit(
                    id=uuid4(),
                    version_id=row.version_id,
                    actor_user_id=actor,
                    action_type="measure.confirm_refused",
                    inputs={
                        "measure_id": str(row.id),
                        "name": row.name,
                        "definition_hash": row.definition_hash,
                        "from_state": from_state,
                    },
                    result={"failed": [str(c) for c in failed]},
                    created_at=datetime.now(UTC),
                    tenant_id=row.tenant_id,
                )
            )


def _clean_violations(
    violations: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "check": str(v.get("check") or ""),
            "subject": str(v.get("subject") or ""),
            "detail": str(v.get("detail") or ""),
        }
        for v in violations
    )


def _new_snapshot(
    version: OntologyVersion,
    body: Mapping[str, Any],
    violations: Sequence[Mapping[str, str]],
    verified: bool,
) -> OntologySnapshot:
    cleaned = _clean_violations(violations)
    # A body that names a failed subject cannot also claim verified.
    return OntologySnapshot(
        id=uuid4(),
        version_id=version.id,
        body=json.loads(json.dumps(dict(body), default=str)),
        violations=cleaned,
        verified=bool(verified) and not cleaned,
        measured_at=datetime.now(UTC),
        tenant_id=version.tenant_id,
    )


def _snapshot_audit(snap: OntologySnapshot) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = {"version_id": str(snap.version_id)}
    result = {
        "snapshot_id": str(snap.id),
        "verified": snap.verified,
        "violations": len(snap.violations),
        "failed_subjects": sorted({v["subject"] for v in snap.violations}),
    }
    return inputs, result


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


_SNAPSHOT_SELECT = "id, tenant_id, version_id, body, violations, verified, measured_at"


def _row_to_snapshot(row: Any) -> OntologySnapshot:
    body = row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}")
    raw = row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]")
    return OntologySnapshot(
        id=UUID(str(row[0])),
        tenant_id=UUID(str(row[1])) if row[1] is not None else None,
        version_id=UUID(str(row[2])),
        body=body,
        violations=_clean_violations(raw),
        verified=bool(row[5]),
        measured_at=row[6],
    )


def active_for_space(
    conn: psycopg.Connection, *, space_id: UUID | str
) -> list[OntologyVersion]:
    rows = conn.execute(
        f"""
        SELECT {_VERSION_SELECT}
          FROM dms.ontology_version
         WHERE space_id = %s AND status = 'active'
         ORDER BY created_at ASC
        """,
        (str(space_id),),
    ).fetchall()
    return [_row_to_version(row) for row in rows]


def record_snapshot(
    conn: psycopg.Connection,
    *,
    version_id: UUID | str,
    body: Mapping[str, Any],
    violations: Sequence[Mapping[str, str]],
    verified: bool,
    actor: UUID | str | None = None,
) -> OntologySnapshot:
    """Insert one measurement row + one ``verify`` audit row. Never an UPDATE."""
    version = load_by_version(conn, version_id)
    if version is None or version.tenant_id is None:
        raise KeyError(f"unknown ontology version {version_id}")
    snap = _new_snapshot(version, body, violations, verified)
    conn.execute(
        """
        INSERT INTO dms.onto_snapshot (
          id, tenant_id, version_id, body, violations, verified, measured_at
        ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        """,
        (
            str(snap.id),
            str(version.tenant_id),
            str(version.id),
            json.dumps(snap.body),
            json.dumps(list(snap.violations)),
            snap.verified,
            snap.measured_at,
        ),
    )
    inputs, result = _snapshot_audit(snap)
    conn.execute(
        """
        INSERT INTO dms.onto_audit (
          tenant_id, version_id, actor_user_id, action_type, inputs, result
        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
        """,
        (
            str(version.tenant_id),
            str(version.id),
            str(actor) if actor is not None else None,
            "verify",
            json.dumps(inputs),
            json.dumps(result),
        ),
    )
    return snap


def latest_snapshot(
    conn: psycopg.Connection, version_id: UUID | str
) -> OntologySnapshot | None:
    row = conn.execute(
        f"""
        SELECT {_SNAPSHOT_SELECT}
          FROM dms.onto_snapshot
         WHERE version_id = %s
         ORDER BY measured_at DESC, id DESC
         LIMIT 1
        """,
        (str(version_id),),
    ).fetchone()
    return _row_to_snapshot(row) if row is not None else None


_MEASURE_SELECT = """
m.id, m.tenant_id, m.version_id, m.name, m.grain, m.aggregate, m.column_name,
m.description, m.state, m.source, m.definition_hash, m.schema_fingerprint,
m.evidence, m.decided_by, m.decided_at, m.decision_reason, m.ledger_entry_id,
v.status, v.schema_fingerprint
"""


def _row_to_measure(row: Any) -> OntoMeasure:
    evidence = row[12] if isinstance(row[12], dict) else json.loads(row[12] or "{}")
    return OntoMeasure(
        id=UUID(str(row[0])),
        tenant_id=UUID(str(row[1])) if row[1] is not None else None,
        version_id=UUID(str(row[2])),
        name=str(row[3]),
        grain=str(row[4]),
        aggregate=str(row[5]),
        column_name=str(row[6]),
        description=str(row[7]),
        state=row[8],
        source=str(row[9]),
        definition_hash=str(row[10]),
        schema_fingerprint=str(row[11]),
        evidence=evidence,
        decided_by=UUID(str(row[13])) if row[13] is not None else None,
        decided_at=row[14],
        decision_reason=row[15],
        ledger_entry_id=row[16],
        version_status=str(row[17]),
        version_fingerprint=str(row[18]),
    )


def _insert_audit(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID | str | None,
    version_id: UUID | str,
    actor: UUID | str | None,
    action: str,
    inputs: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO dms.onto_audit (
          tenant_id, version_id, actor_user_id, action_type, inputs, result
        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
        """,
        (
            str(tenant_id),
            str(version_id),
            str(actor) if actor is not None else None,
            action,
            json.dumps(dict(inputs)),
            json.dumps(dict(result)),
        ),
    )


def measures_for_space(
    conn: psycopg.Connection,
    *,
    space_id: UUID | str,
    states: Sequence[str] | None = None,
    active_only: bool = False,
) -> list[OntoMeasure]:
    """ONE join query: onto_measure x ontology_version for one Space."""
    clauses = ["v.space_id = %s"]
    params: list[Any] = [str(space_id)]
    if active_only:
        clauses.append("v.status = 'active'")
    if states is not None:
        clauses.append("m.state = ANY(%s)")
        params.append(list(states))
    rows = conn.execute(
        f"""
        SELECT {_MEASURE_SELECT}
          FROM dms.onto_measure m
          JOIN dms.ontology_version v ON v.id = m.version_id
         WHERE {" AND ".join(clauses)}
         ORDER BY v.created_at ASC, m.name ASC
        """,
        params,
    ).fetchall()
    return [_row_to_measure(r) for r in rows]


def get_measure(
    conn: psycopg.Connection, *, space_id: UUID | str, measure_id: UUID | str
) -> OntoMeasure | None:
    row = conn.execute(
        f"""
        SELECT {_MEASURE_SELECT}
          FROM dms.onto_measure m
          JOIN dms.ontology_version v ON v.id = m.version_id
         WHERE m.id = %s AND v.space_id = %s
        """,
        (str(measure_id), str(space_id)),
    ).fetchone()
    return _row_to_measure(row) if row is not None else None


def add_measures(
    conn: psycopg.Connection,
    *,
    version_id: UUID | str,
    drafts: Sequence[MeasureDraft],
    evidences: Sequence[Mapping[str, Any]],
    actor: UUID | str | None,
    snapshot_id: UUID | str | None,
    action: str,
) -> tuple[list[OntoMeasure], list[MeasureDraft]]:
    if action not in _MEASURE_AUDIT_ADD:
        raise ValueError(f"unknown measure add action {action!r}")
    if len(drafts) != len(evidences):
        raise ValueError("one evidence mapping per draft")
    for d in drafts:
        _check_draft(d)
    version = load_by_version(conn, version_id)
    if version is None or version.tenant_id is None:
        raise KeyError(f"unknown ontology version {version_id}")
    inserted_ids: list[UUID] = []
    skipped: list[MeasureDraft] = []
    for draft, evidence in zip(drafts, evidences, strict=True):
        row = conn.execute(
            """
            INSERT INTO dms.onto_measure (
              tenant_id, version_id, name, column_name, aggregate, grain, state,
              description, source, definition_hash, schema_fingerprint, evidence
            ) VALUES (%s, %s, %s, %s, %s, %s, 'proposed', %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (version_id, name) DO NOTHING
            RETURNING id
            """,
            (
                str(version.tenant_id),
                str(version.id),
                draft.name,
                draft.column,
                draft.aggregate,
                draft.grain,
                draft.description,
                draft.source,
                _draft_hash(draft),
                version.schema_fingerprint,
                json.dumps(_clean_json(evidence)),
            ),
        ).fetchone()
        if row is None:
            skipped.append(draft)
        else:
            inserted_ids.append(UUID(str(row[0])))
    inserted: list[OntoMeasure] = []
    if inserted_ids:
        rows = conn.execute(
            f"""
            SELECT {_MEASURE_SELECT}
              FROM dms.onto_measure m
              JOIN dms.ontology_version v ON v.id = m.version_id
             WHERE m.id = ANY(%s)
             ORDER BY m.name
            """,
            ([str(i) for i in inserted_ids],),
        ).fetchall()
        inserted = [_row_to_measure(r) for r in rows]
    inputs, result = _add_audit(action, version, inserted, len(skipped), snapshot_id)
    _insert_audit(
        conn,
        tenant_id=version.tenant_id,
        version_id=version.id,
        actor=actor,
        action=action,
        inputs=inputs,
        result=result,
    )
    return inserted, skipped


def decide_measure(
    conn: psycopg.Connection,
    *,
    measure_id: UUID | str,
    to_state: str,
    expected_hash: str,
    name: str | None,
    description: str | None,
    reason: str | None,
    evidence: Mapping[str, Any] | None,
    actor: UUID,
    ledger: Callable[[dict[str, Any]], str],
) -> tuple[OntoMeasure, bool]:
    """Lock the row, plan, ledger, ONE UPDATE, audit, ledger pointer. The caller
    owns the transaction: any exception here must roll the connection back."""
    locked = conn.execute(
        "SELECT id FROM dms.onto_measure WHERE id = %s FOR UPDATE", (str(measure_id),)
    ).fetchone()
    if locked is None:
        raise MeasureNotFound(str(measure_id))
    row_raw = conn.execute(
        f"""
        SELECT {_MEASURE_SELECT}
          FROM dms.onto_measure m
          JOIN dms.ontology_version v ON v.id = m.version_id
         WHERE m.id = %s
        """,
        (str(measure_id),),
    ).fetchone()
    assert row_raw is not None
    row = _row_to_measure(row_raw)
    planned = _plan_decision(
        row,
        to_state=to_state,
        expected_hash=expected_hash,
        name=name,
        description=description,
        reason=reason,
        evidence=evidence,
    )
    if planned is None:
        return row, False
    space = conn.execute(
        "SELECT space_id FROM dms.ontology_version WHERE id = %s", (str(row.version_id),)
    ).fetchone()
    space_id = UUID(str(space[0])) if space is not None else None
    entry_id = ledger(_ledger_payload(row, planned, space_id))
    if not entry_id:
        raise ValueError("the ledger returned no entry id")
    now = datetime.now(UTC)
    try:
        cur = conn.execute(
            """
            UPDATE dms.onto_measure
               SET state = %s, name = %s, description = %s, definition_hash = %s,
                   evidence = %s::jsonb, decided_by = %s, decided_at = %s,
                   decision_reason = %s, ledger_entry_id = %s
             WHERE id = %s AND state = %s AND definition_hash = %s
            """,
            (
                planned.state,
                planned.name,
                planned.description,
                planned.definition_hash,
                json.dumps(planned.evidence),
                str(actor),
                now,
                planned.decision_reason,
                entry_id,
                str(row.id),
                row.state,
                expected_hash,
            ),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise MeasureNameTaken(planned.name) from exc
    if cur.rowcount != 1:
        raise MeasureTransitionError(
            "concurrent_change", "the measure changed while it was being decided", row
        )
    new = replace(planned, decided_by=actor, decided_at=now, ledger_entry_id=entry_id)
    action, inputs, result = _decide_audit(row, new, expected_hash, entry_id)
    _insert_audit(
        conn,
        tenant_id=new.tenant_id,
        version_id=new.version_id,
        actor=actor,
        action=action,
        inputs=inputs,
        result=result,
    )
    conn.execute(
        """
        INSERT INTO dms.ledger_ref (tenant_id, cortex_entry_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (str(new.tenant_id), entry_id),
    )
    return new, True


def record_refusal(
    conn: psycopg.Connection,
    *,
    measure_id: UUID | str,
    failed: Sequence[str],
    actor: UUID | str | None,
    from_state: str,
) -> None:
    row_raw = conn.execute(
        f"""
        SELECT {_MEASURE_SELECT}
          FROM dms.onto_measure m
          JOIN dms.ontology_version v ON v.id = m.version_id
         WHERE m.id = %s
        """,
        (str(measure_id),),
    ).fetchone()
    if row_raw is None:
        raise MeasureNotFound(str(measure_id))
    row = _row_to_measure(row_raw)
    _insert_audit(
        conn,
        tenant_id=row.tenant_id,
        version_id=row.version_id,
        actor=actor,
        action="measure.confirm_refused",
        inputs={
            "measure_id": str(row.id),
            "name": row.name,
            "definition_hash": row.definition_hash,
            "from_state": from_state,
        },
        result={"failed": [str(c) for c in failed]},
    )


class PostgresOntologyStore:
    """The durable store: the in-memory ``OntologyStore`` API over Postgres + RLS.

    One connection and transaction per call, bound to one tenant, so a Space
    in another tenant is invisible here by RLS, not by a WHERE clause alone.
    """

    def __init__(self, conninfo: str, *, tenant_id: UUID | str) -> None:
        self._conninfo = conninfo
        self._tenant_id = UUID(str(tenant_id))

    @property
    def persistent(self) -> bool:
        """True: confirmations survive a restart."""
        return True

    def _conn(self, role: AppRole = "steward") -> psycopg.Connection:
        from dms_core.control_plane.session import set_tenant_context

        conn = psycopg.connect(self._conninfo)
        set_tenant_context(conn, self._tenant_id, role=role)
        return conn

    def reconnect(
        self,
        *,
        space_id: UUID,
        identity: SourceIdentity,
        fingerprint: str,
        created_by: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> OntologyVersion:
        with self._conn() as conn:
            return reconnect(
                conn,
                tenant_id=tenant_id or self._tenant_id,
                space_id=space_id,
                identity=identity,
                fingerprint=fingerprint,
                created_by=created_by,
            )

    def load_by_version(self, version_id: UUID) -> OntologyVersion | None:
        with self._conn() as conn:
            return load_by_version(conn, version_id)

    def list_versions(
        self, space_id: UUID, identity: SourceIdentity
    ) -> list[OntologyVersion]:
        with self._conn() as conn:
            return list_versions(conn, space_id=space_id, identity=identity)

    def active_for_space(self, space_id: UUID) -> list[OntologyVersion]:
        with self._conn() as conn:
            return active_for_space(conn, space_id=space_id)

    def record_snapshot(
        self,
        version_id: UUID,
        *,
        body: Mapping[str, Any],
        violations: Sequence[Mapping[str, str]],
        verified: bool,
        actor: UUID | None = None,
    ) -> OntologySnapshot:
        with self._conn() as conn:
            return record_snapshot(
                conn,
                version_id=version_id,
                body=body,
                violations=violations,
                verified=verified,
                actor=actor,
            )

    def latest_snapshot(self, version_id: UUID) -> OntologySnapshot | None:
        with self._conn() as conn:
            return latest_snapshot(conn, version_id)

    def measures_for_space(
        self,
        space_id: UUID,
        *,
        states: Sequence[str] | None = None,
        active_only: bool = False,
    ) -> list[OntoMeasure]:
        with self._conn(role="viewer") as conn:
            return measures_for_space(
                conn, space_id=space_id, states=states, active_only=active_only
            )

    def get_measure(self, space_id: UUID, measure_id: UUID) -> OntoMeasure | None:
        with self._conn(role="viewer") as conn:
            return get_measure(conn, space_id=space_id, measure_id=measure_id)

    def add_measures(
        self,
        version_id: UUID,
        drafts: Sequence[MeasureDraft],
        evidences: Sequence[Mapping[str, Any]],
        *,
        actor: UUID | None,
        snapshot_id: UUID | str | None,
        action: str,
    ) -> tuple[list[OntoMeasure], list[MeasureDraft]]:
        with self._conn() as conn:
            return add_measures(
                conn,
                version_id=version_id,
                drafts=drafts,
                evidences=evidences,
                actor=actor,
                snapshot_id=snapshot_id,
                action=action,
            )

    def decide_measure(
        self,
        measure_id: UUID,
        *,
        to_state: str,
        expected_hash: str,
        name: str | None = None,
        description: str | None = None,
        reason: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        actor: UUID,
        ledger: Callable[[dict[str, Any]], str],
    ) -> tuple[OntoMeasure, bool]:
        # `with conn` commits on a clean exit and rolls back on any exception,
        # so a raising ledger callable leaves no row change and no audit row.
        with self._conn() as conn:
            return decide_measure(
                conn,
                measure_id=measure_id,
                to_state=to_state,
                expected_hash=expected_hash,
                name=name,
                description=description,
                reason=reason,
                evidence=evidence,
                actor=actor,
                ledger=ledger,
            )

    def record_refusal(
        self,
        measure_id: UUID,
        *,
        failed: Sequence[str],
        actor: UUID | None,
        from_state: str,
    ) -> None:
        with self._conn() as conn:
            record_refusal(
                conn,
                measure_id=measure_id,
                failed=failed,
                actor=actor,
                from_state=from_state,
            )
