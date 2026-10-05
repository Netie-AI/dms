"""BANK-02 (dms#269): the per-ask audit record behind ``GET /v1/audit/export``.

Before this module nothing in DMS recorded an ask. ``dms.ledger_ref`` points at
Cortex ledger entries and carries no question, SQL, tables or outcome, and
Cortex contract 1.2.0 has no way to read an entry back (append and verify only).
So an auditor could be told that a ledger entry exists and never what was asked.

One row is appended per ask, after the ask has produced its envelope or its
error. The table is append-only: no UPDATE and no DELETE grant to any app role
(alembic 0005). This is a *record*, not a second ledger. It holds no hash chain
(CLAUDE.md hard rule 3); the one chain stays in Cortex, and the row carries a
pointer, ``cortex_entry_id``, to the entry where the ask had one.

Swap scenario (hard rule 6): this is part of the **catalog** port, beside
``SpaceStorePort``. Postgres when ``DATABASE_URL`` binds, an in-process list when
it does not. Not a sixth port.

The actor is whatever the caller passes in. Under DR-0004 Option A that is the
server-side deployment identity and ``actor_kind`` is ``deployment``. Nothing
here reads identity from a request.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import psycopg

from dms_core.control_plane.session import set_tenant_context
from dms_core.freeroute import redact_text

ActorKind = Literal["person", "deployment"]
AskBadge = Literal["validated", "abstain", "error"]

#: Under DR-0004 Option A there is no identity provider, so the only actor the
#: server can truthfully name is the deployment itself.
DEPLOYMENT_ACTOR_KIND: ActorKind = "deployment"

_REASON_CAP = 500
_REDACTED = "[redacted]"

_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_SECRET_PAIR = re.compile(
    r"(?i)\b(password|passwd|pwd|passphrase|secret|client[_-]?secret|api[_-]?key|"
    r"access[_-]?key|secret[_-]?key|private[_-]?key|token|authorization)\b"
    r"(\s*[=:]\s*|\s+is\s+(?!(?:not|null|true|false)\b))"
    r"(\"[^\"]*\"|'[^']*'|[^\s;,&)]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KEY_SHAPES = re.compile(
    r"\bAIza[0-9A-Za-z_-]{20,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"
    r"|\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
)


def scrub(text: str | None) -> str:
    """Strip secret-shaped values from free text. Best effort, applied twice.

    The record is built from fixed fields (question, SQL, table names, outcome),
    never from settings or connection config, so a database password or model key
    has no field to arrive in. The one way a secret still reaches the record is a
    person typing one into a question, or SQL that carries one inline. This
    removes the common shapes: URL userinfo, ``password=...`` pairs, bearer and
    provider tokens, and private-key blocks. It cannot recognise an arbitrary
    string as a secret. It runs when the row is built and again when it is
    exported, so a pattern added later also cleans rows written before it.
    """
    out = text or ""
    out = _PRIVATE_KEY_BLOCK.sub(_REDACTED, out)
    out = redact_text(out)
    out = _URL_CREDENTIALS.sub(rf"\1{_REDACTED}@", out)
    out = _SECRET_PAIR.sub(rf"\1\2{_REDACTED}", out)
    out = _BEARER.sub(f"Bearer {_REDACTED}", out)
    return _KEY_SHAPES.sub(_REDACTED, out)


@dataclass(frozen=True)
class AskAuditRecord:
    """One ask, as an auditor needs it. Immutable, and never edited after insert."""

    ask_id: str
    asked_at: datetime
    actor: str
    actor_kind: str
    space_id: str
    question: str
    executed_sql: str
    tables_read: tuple[str, ...]
    badge: str
    badge_level: str
    abstain_reason: str
    row_count: int
    cortex_entry_id: str
    ask_mode: str

    def scrubbed(self) -> AskAuditRecord:
        return AskAuditRecord(
            ask_id=self.ask_id,
            asked_at=self.asked_at,
            actor=scrub(self.actor),
            actor_kind=self.actor_kind,
            space_id=scrub(self.space_id),
            question=scrub(self.question),
            executed_sql=scrub(self.executed_sql),
            tables_read=tuple(scrub(t) for t in self.tables_read),
            badge=self.badge,
            badge_level=self.badge_level,
            abstain_reason=scrub(self.abstain_reason),
            row_count=self.row_count,
            cortex_entry_id=scrub(self.cortex_entry_id),
            ask_mode=self.ask_mode,
        )


def _one_line(text: str, cap: int = _REASON_CAP) -> str:
    return " ".join(str(text).split())[:cap]


def _ledger_pointer(env: dict[str, Any]) -> str:
    """The Cortex ledger entry an ask points at, or ``""``.

    ``build_answer_envelope`` fills ``audit_id`` with ``answer_id`` when a path
    has no receipt, so an abstain carries ``audit_id == "ans_gen01_abstain"``. That
    is a label, not an entry, and exporting it as a ``cortex_entry_id`` would
    point an auditor at a ledger entry that does not exist. Synthetic answer ids
    all start ``ans_``. A demo answer never touched the ledger either.

    The cost of the rule: a real Cortex receipt that itself starts ``ans_`` and
    equals the answer id reads as no pointer. An empty pointer under-claims; a
    false one is the failure this guards.
    """
    if str(env.get("ask_mode") or "") == "demo":
        return ""
    audit_id = str(env.get("audit_id") or "").strip()
    answer_id = str(env.get("answer_id") or "").strip()
    if not audit_id:
        return ""
    if audit_id == answer_id and audit_id.startswith("ans_"):
        return ""
    return audit_id


def _ask_mode(env: dict[str, Any]) -> str:
    if env.get("demo_fallback_used"):
        return "demo_fallback"
    return str(env.get("ask_mode") or "live")


def _abstain_reason(env: dict[str, Any]) -> str:
    """Why it abstained: the coded notes first, then what the customer was told.

    The envelope has no reason field. Its assumptions carry the code on the paths
    that name one (``GEN-01: ledger_entry_missing``) and the text carries the
    sentence, so both go in, capped, rather than guessing which one is the reason.
    """
    notes = [str(a) for a in (env.get("assumptions") or []) if str(a).strip()]
    text = str(env.get("text") or "").strip()
    return _one_line("; ".join([*notes, text] if text else notes))


def record_from_envelope(
    env: dict[str, Any],
    *,
    question: str,
    actor: str,
    actor_kind: ActorKind,
    space_id: str | None,
    tables_read: tuple[str, ...],
    asked_at: datetime | None = None,
) -> AskAuditRecord:
    """Build the row for an ask that produced an envelope, abstains included."""
    abstained = bool(env.get("abstained")) or str(env.get("badge") or "").upper() == "ABSTAIN"
    return AskAuditRecord(
        ask_id=str(uuid4()),
        asked_at=asked_at or datetime.now(UTC),
        actor=actor,
        actor_kind=actor_kind,
        space_id=space_id or "",
        question=question,
        executed_sql=str(env.get("sql_used") or ""),
        tables_read=tuple(tables_read),
        badge="abstain" if abstained else "validated",
        badge_level=str(env.get("badge") or ""),
        abstain_reason=_abstain_reason(env) if abstained else "",
        row_count=len([r for r in (env.get("rows") or []) if isinstance(r, dict)]),
        cortex_entry_id=_ledger_pointer(env),
        ask_mode=_ask_mode(env),
    ).scrubbed()


def record_from_error(
    *,
    question: str,
    reason: str,
    actor: str,
    actor_kind: ActorKind,
    space_id: str | None,
    asked_at: datetime | None = None,
) -> AskAuditRecord:
    """Build the row for an ask that ended in an error rather than an envelope."""
    return AskAuditRecord(
        ask_id=str(uuid4()),
        asked_at=asked_at or datetime.now(UTC),
        actor=actor,
        actor_kind=actor_kind,
        space_id=space_id or "",
        question=question,
        executed_sql="",
        tables_read=(),
        badge="error",
        badge_level="",
        abstain_reason=_one_line(reason),
        row_count=0,
        cortex_entry_id="",
        ask_mode="live",
    ).scrubbed()


class AskAuditStorePort(Protocol):
    """Append and range-read. There is deliberately no update and no delete."""

    backend: str
    #: Asks whose row could not be written since this process started. A store
    #: that fails to record must say so; an export that looks complete while rows
    #: are missing is the failure an audit record exists to rule out.
    dropped: int

    def record(self, rec: AskAuditRecord) -> None: ...

    def list_between(
        self, *, since: datetime | None, until: datetime | None, limit: int
    ) -> list[AskAuditRecord]: ...


def _in_range(rec: AskAuditRecord, since: datetime | None, until: datetime | None) -> bool:
    if since is not None and rec.asked_at < since:
        return False
    return not (until is not None and rec.asked_at >= until)


class InMemoryAskAuditStore:
    """Process-local. Used when no database binds; does not survive a restart."""

    backend = "memory"

    def __init__(self) -> None:
        self._rows: list[AskAuditRecord] = []
        self._lock = threading.Lock()
        self.dropped = 0

    def record(self, rec: AskAuditRecord) -> None:
        with self._lock:
            self._rows.append(rec)

    def list_between(
        self, *, since: datetime | None, until: datetime | None, limit: int
    ) -> list[AskAuditRecord]:
        with self._lock:
            rows = [r for r in self._rows if _in_range(r, since, until)]
        rows.sort(key=lambda r: (r.asked_at, r.ask_id))
        return rows[:limit]


_COLUMNS = (
    "ask_id, asked_at, actor, actor_kind, space_id, question, executed_sql, "
    "tables_read, badge, badge_level, abstain_reason, row_count, cortex_entry_id, ask_mode"
)


class PostgresAskAuditStore:
    """``dms.ask_audit`` under RLS. Writes as steward; reads as viewer."""

    backend = "postgres"

    def __init__(self, conninfo: str, *, tenant_id: UUID | str) -> None:
        self._conninfo = conninfo
        self._tenant_id = str(tenant_id)
        self.dropped = 0

    def record(self, rec: AskAuditRecord) -> None:
        with psycopg.connect(self._conninfo) as conn:
            set_tenant_context(conn, self._tenant_id, role="steward")
            conn.execute(
                f"""
                INSERT INTO dms.ask_audit (tenant_id, {_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ask_id) DO NOTHING
                """,
                (
                    UUID(self._tenant_id),
                    UUID(rec.ask_id),
                    rec.asked_at,
                    rec.actor,
                    rec.actor_kind,
                    rec.space_id,
                    rec.question,
                    rec.executed_sql,
                    list(rec.tables_read),
                    rec.badge,
                    rec.badge_level,
                    rec.abstain_reason,
                    rec.row_count,
                    rec.cortex_entry_id,
                    rec.ask_mode,
                ),
            )
            conn.commit()

    def list_between(
        self, *, since: datetime | None, until: datetime | None, limit: int
    ) -> list[AskAuditRecord]:
        with psycopg.connect(self._conninfo) as conn:
            set_tenant_context(conn, self._tenant_id, role="viewer")
            rows = conn.execute(
                """
                SELECT ask_id::text, asked_at, actor, actor_kind, space_id, question,
                       executed_sql, tables_read, badge, badge_level, abstain_reason,
                       row_count, cortex_entry_id, ask_mode
                  FROM dms.ask_audit
                 WHERE tenant_id = %s
                   AND (%s::timestamptz IS NULL OR asked_at >= %s::timestamptz)
                   AND (%s::timestamptz IS NULL OR asked_at < %s::timestamptz)
                 ORDER BY asked_at, ask_id
                 LIMIT %s
                """,
                (UUID(self._tenant_id), since, since, until, until, limit),
            ).fetchall()
            conn.commit()
        return [
            AskAuditRecord(
                ask_id=r[0],
                asked_at=r[1],
                actor=r[2],
                actor_kind=r[3],
                space_id=r[4] or "",
                question=r[5],
                executed_sql=r[6],
                tables_read=tuple(r[7] or ()),
                badge=r[8],
                badge_level=r[9],
                abstain_reason=r[10],
                row_count=int(r[11]),
                cortex_entry_id=r[12],
                ask_mode=r[13],
            )
            for r in rows
        ]


__all__ = [
    "DEPLOYMENT_ACTOR_KIND",
    "ActorKind",
    "AskAuditRecord",
    "AskAuditStorePort",
    "AskBadge",
    "InMemoryAskAuditStore",
    "PostgresAskAuditStore",
    "record_from_envelope",
    "record_from_error",
    "scrub",
]
