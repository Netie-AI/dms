"""BANK-02 (dms#269): the per-ask audit record behind ``GET /v1/audit/export``.

Before this module nothing in DMS recorded an ask. ``dms.ledger_ref`` points at
Cortex ledger entries and carries no question, SQL, tables or outcome, and
Cortex contract 1.2.0 has no way to read an entry back (append and verify only).
So an auditor could be told that a ledger entry exists and never what was asked.

One row is appended per ask, after the ask has produced its envelope or its
error. The table is append-only: no UPDATE and no DELETE grant to any app role
(alembic 0005). That holds for the app roles only: the table owner and a
superuser can still rewrite it, and compose runs the API as the superuser
``POSTGRES_USER=dms`` today (a BANK-04 item, not changed here). This is a
*record*, not a second ledger. It holds no hash chain (CLAUDE.md hard rule 3);
the one chain stays in Cortex, and the row carries a pointer, ``cortex_entry_id``,
to the entry where the ask had one, and the entry's ``ledger_seq`` where DMS
appended it.

What is written is scrubbed of secret-shaped values and passed through the same
PII masker the customer envelope uses (``ask_audit_scrub``): the audit export
shows masked literals by design. A later policy change can store raw values under
tighter access.

Swap scenario (hard rule 6): this is part of the **catalog** port, beside
``SpaceStorePort``. Postgres when ``DATABASE_URL`` binds, an in-process list when
it does not. Not a sixth port.

The actor is whatever the caller passes in. Under DR-0004 Option A that is the
server-side deployment identity and ``actor_kind`` is ``deployment``. Nothing
here reads identity from a request.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import psycopg

from dms_core.control_plane.ask_audit_scrub import (
    cuts_a_run,
    has_sql_statement,
    mask_pii_counted,
    safe_cut,
    scrub,
    scrub_counted,
)
from dms_core.control_plane.session import set_tenant_context

ActorKind = Literal["person", "deployment"]
AskBadge = Literal["validated", "abstain", "error"]

#: Under DR-0004 Option A there is no identity provider, so the only actor the
#: server can truthfully name is the deployment itself.
DEPLOYMENT_ACTOR_KIND: ActorKind = "deployment"

#: What is recorded of a question and of the executed SQL. The ask itself still
#: accepts whatever it accepted before; only the record is bounded, and the cut
#: is marked in the text and in ``truncated`` so it is never silent.
QUESTION_CAP = 10_000
SQL_CAP = 50_000
#: Scrubbing reads this far past the cap before the cut, so a secret that starts
#: before the cut and ends after it is redacted whole and never half-kept.
_CUT_MARGIN = 2_000

_REASON_CAP = 500


def _clean(text: str, cap: int, *, sql: bool) -> tuple[str, int, bool]:
    """Scrub, mask, then cut. Returns the text, how many values were replaced, and
    whether it was cut. The cut is last, so it can never split a secret.

    The masker runs here, once, over the whole window, and never again: the export
    re-runs only the linear scrub. A window that ends inside a run of address
    characters is told so (``open_end``), so half an address is not stored.
    """
    window = text[: cap + _CUT_MARGIN]
    clean, n_secrets = scrub_counted(window, sql=sql)
    clean, n_pii = mask_pii_counted(clean, open_end=cuts_a_run(text, len(window)), sql=sql)
    cut = len(text) > len(window) or len(clean) > cap
    if cut:
        clean = f"{safe_cut(clean, cap)}...[truncated: original was {len(text)} chars]"
    return clean, n_secrets + n_pii, cut


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
    #: How many values were replaced by ``[redacted]`` or a PII mask token in this
    #: row, so an auditor can see that the text was altered. 0 means none.
    redactions: int = 0
    #: The question or SQL was cut at the recorded-length cap (the cut is marked in the text).
    truncated: bool = False
    #: The Cortex ledger seq of ``cortex_entry_id``, when DMS appended that entry and
    #: the append response said. None for a Cortex receipt, which DMS did not append.
    ledger_seq: int | None = None
    #: ``tables_read`` came from a bounded name scan, not a parse (the statement was too
    #: long to parse, or would not parse): it can name a CTE, or a table inside a literal.
    tables_read_approximate: bool = False

    def scrubbed(self) -> AskAuditRecord:
        """Scrub secrets again (write and export). Adds what this pass removed to ``redactions``.

        Secrets only, and linear. The PII masker is not run here: the question and the SQL
        were masked once, at write, and the masker is not linear on every input.
        """
        extra = 0

        def text(value: str, *, sql: bool = False) -> str:
            nonlocal extra
            clean, n = scrub_counted(value, sql=sql)
            extra += n
            return clean

        return replace(
            self,
            actor=text(self.actor),
            space_id=text(self.space_id),
            question=text(self.question),
            executed_sql=text(self.executed_sql, sql=True),
            tables_read=tuple(text(t) for t in self.tables_read),
            abstain_reason=text(self.abstain_reason),
            cortex_entry_id=text(self.cortex_entry_id),
            redactions=self.redactions + extra,
        )


def _one_line(text: str, cap: int = _REASON_CAP) -> str:
    return " ".join(str(text).split())[:cap]


def ledger_pointer(env: dict[str, Any]) -> str:
    """The Cortex ledger entry an ask points at, or ``""``.

    ``build_answer_envelope`` fills ``audit_id`` with ``answer_id`` when a path
    has no receipt, so an abstain without one carries
    ``audit_id == "ans_gen01_abstain"``. That is a label, not an entry, and
    exporting it as a ``cortex_entry_id`` would point an auditor at a ledger entry
    that does not exist. Synthetic answer ids all start ``ans_``. A demo answer
    never touched the ledger either. An abstain that DID get a ledger entry (a
    Cortex receipt id) keeps its pointer.

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


def _build(
    *,
    question: str,
    executed_sql: str,
    actor: str,
    actor_kind: str,
    space_id: str | None,
    tables_read: tuple[str, ...],
    tables_read_approximate: bool,
    badge: str,
    badge_level: str,
    abstain_reason: str,
    row_count: int,
    cortex_entry_id: str,
    ledger_seq: int | None,
    ask_mode: str,
    asked_at: datetime | None,
) -> AskAuditRecord:
    q, n_q, q_cut = _clean(question, QUESTION_CAP, sql=False)
    s, n_s, s_cut = _clean(
        executed_sql if has_sql_statement(executed_sql) else "", SQL_CAP, sql=True
    )
    rec = AskAuditRecord(
        ask_id=str(uuid4()),
        asked_at=asked_at or datetime.now(UTC),
        actor=actor,
        actor_kind=actor_kind,
        space_id=space_id or "",
        question=q,
        executed_sql=s,
        tables_read=tuple(tables_read),
        badge=badge,
        badge_level=badge_level,
        abstain_reason=abstain_reason,
        row_count=max(0, int(row_count)),
        cortex_entry_id=cortex_entry_id,
        ask_mode=ask_mode,
        redactions=n_q + n_s,
        truncated=q_cut or s_cut,
        ledger_seq=ledger_seq,
        tables_read_approximate=tables_read_approximate,
    )
    return rec.scrubbed()


def record_from_envelope(
    env: dict[str, Any],
    *,
    question: str,
    actor: str,
    actor_kind: ActorKind,
    space_id: str | None,
    executed_sql: str,
    tables_read: tuple[str, ...],
    row_count: int,
    ledger: Sequence[tuple[str, int | None]] = (),
    asked_at: datetime | None = None,
    tables_read_approximate: bool = False,
) -> AskAuditRecord:
    """Build the row for an ask that produced an envelope, abstains included.

    ``executed_sql``, ``tables_read`` and ``row_count`` are what ran, supplied by
    the caller from the engine side. The envelope is not their source: it drops
    the SQL and rows of an abstain and substitutes placeholders. ``ledger`` is the
    ``(entry_id, seq)`` of each entry DMS appended for this ask; the row takes the
    seq of the entry it points at.
    """
    abstained = bool(env.get("abstained")) or str(env.get("badge") or "").upper() == "ABSTAIN"
    pointer = ledger_pointer(env)
    seq = next((s for eid, s in ledger if pointer and eid == pointer and s is not None), None)
    return _build(
        question=question,
        executed_sql=executed_sql,
        actor=actor,
        actor_kind=actor_kind,
        space_id=space_id,
        tables_read=tables_read,
        tables_read_approximate=tables_read_approximate,
        badge="abstain" if abstained else "validated",
        badge_level=str(env.get("badge") or ""),
        abstain_reason=_abstain_reason(env) if abstained else "",
        row_count=row_count,
        cortex_entry_id=pointer,
        ledger_seq=seq,
        ask_mode=_ask_mode(env),
        asked_at=asked_at,
    )


def record_from_error(
    *,
    question: str,
    reason: str,
    actor: str,
    actor_kind: ActorKind,
    space_id: str | None,
    executed_sql: str = "",
    tables_read: tuple[str, ...] = (),
    row_count: int = 0,
    asked_at: datetime | None = None,
    tables_read_approximate: bool = False,
) -> AskAuditRecord:
    """Build the row for an ask that ended in an error rather than an envelope."""
    return _build(
        question=question,
        executed_sql=executed_sql,
        actor=actor,
        actor_kind=actor_kind,
        space_id=space_id,
        tables_read=tables_read,
        tables_read_approximate=tables_read_approximate,
        badge="error",
        badge_level="",
        abstain_reason=_one_line(reason),
        row_count=row_count,
        cortex_entry_id="",
        ledger_seq=None,
        ask_mode="live",
        asked_at=asked_at,
    )


class AskAuditStorePort(Protocol):
    """Append and range-read. There is deliberately no update and no delete."""

    backend: str
    #: Asks whose row could not be written, or was evicted, since this process
    #: started. Per process: it resets on restart. A store that fails to record
    #: must say so; an export that looks complete while rows are missing is the
    #: failure an audit record exists to rule out.
    dropped: int

    def record(self, rec: AskAuditRecord) -> None: ...

    def list_between(
        self, *, since: datetime | None, until: datetime | None, limit: int
    ) -> list[AskAuditRecord]: ...


def _in_range(rec: AskAuditRecord, since: datetime | None, until: datetime | None) -> bool:
    if since is not None and rec.asked_at < since:
        return False
    return not (until is not None and rec.asked_at >= until)


def _row_size(rec: AskAuditRecord) -> int:
    return 256 + sum(
        len(x)
        for x in (
            rec.actor,
            rec.space_id,
            rec.question,
            rec.executed_sql,
            rec.abstain_reason,
            rec.cortex_entry_id,
            *rec.tables_read,
        )
    )


#: A bound on the in-process store, in characters. The recorded fields are
#: capped, so this holds a few hundred of the largest rows or very many small
#: ones. Past it the oldest rows are evicted and counted in ``dropped``.
MEMORY_BUDGET = 32 * 1024 * 1024


class InMemoryAskAuditStore:
    """Process-local. Used when no database binds; does not survive a restart."""

    backend = "memory"

    def __init__(self, max_bytes: int = MEMORY_BUDGET) -> None:
        self._rows: deque[AskAuditRecord] = deque()
        self._size = 0
        self._max = max_bytes
        self._lock = threading.Lock()
        self.dropped = 0

    def record(self, rec: AskAuditRecord) -> None:
        with self._lock:
            self._rows.append(rec)
            self._size += _row_size(rec)
            while self._size > self._max and len(self._rows) > 1:
                self._size -= _row_size(self._rows.popleft())
                self.dropped += 1

    def list_between(
        self, *, since: datetime | None, until: datetime | None, limit: int
    ) -> list[AskAuditRecord]:
        with self._lock:
            rows = [r for r in self._rows if _in_range(r, since, until)]
        rows.sort(key=lambda r: (r.asked_at, r.ask_id))
        return rows[:limit]


#: A hung or unreachable audit database must not hang an ask. Seconds.
CONNECT_TIMEOUT_S = 3
STATEMENT_TIMEOUT_MS = 3000
#: After a connection failure, skip the database this long: each later ask counts
#: as unrecorded at once instead of waiting out the timeout again.
COOLDOWN_S = 10.0

_COLUMNS = (
    "ask_id",
    "asked_at",
    "actor",
    "actor_kind",
    "space_id",
    "question",
    "executed_sql",
    "tables_read",
    "badge",
    "badge_level",
    "abstain_reason",
    "row_count",
    "cortex_entry_id",
    "ask_mode",
    "redactions",
    "truncated",
    "ledger_seq",
    "tables_read_approximate",
)


class AuditStoreUnavailable(RuntimeError):
    """The audit database is in its cool-down after a connection failure."""


class PostgresAskAuditStore:
    """``dms.ask_audit`` under RLS. Writes as steward; reads as viewer."""

    backend = "postgres"

    def __init__(
        self,
        conninfo: str,
        *,
        tenant_id: UUID | str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._conninfo = conninfo
        self._tenant_id = str(tenant_id)
        self._clock = clock
        self._down_until = 0.0
        self.dropped = 0

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._conninfo,
            connect_timeout=CONNECT_TIMEOUT_S,
            options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        )

    def record(self, rec: AskAuditRecord) -> None:
        if self._clock() < self._down_until:
            raise AuditStoreUnavailable("audit database in cool-down")
        columns = ", ".join(("tenant_id", *_COLUMNS))
        marks = ", ".join(["%s"] * (len(_COLUMNS) + 1))
        try:
            with self._connect() as conn:
                set_tenant_context(conn, self._tenant_id, role="steward")
                conn.execute(
                    f"INSERT INTO dms.ask_audit ({columns}) VALUES ({marks}) "
                    "ON CONFLICT (ask_id) DO NOTHING",
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
                        rec.redactions,
                        rec.truncated,
                        rec.ledger_seq,
                        rec.tables_read_approximate,
                    ),
                )
                conn.commit()
        except psycopg.OperationalError:
            self._down_until = self._clock() + COOLDOWN_S
            raise

    def list_between(
        self, *, since: datetime | None, until: datetime | None, limit: int
    ) -> list[AskAuditRecord]:
        with self._connect() as conn:
            set_tenant_context(conn, self._tenant_id, role="viewer")
            rows = conn.execute(
                """
                SELECT ask_id::text, asked_at, actor, actor_kind, space_id, question,
                       executed_sql, tables_read, badge, badge_level, abstain_reason,
                       row_count, cortex_entry_id, ask_mode, redactions, truncated,
                       ledger_seq, tables_read_approximate
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
                redactions=int(r[14]),
                truncated=bool(r[15]),
                ledger_seq=None if r[16] is None else int(r[16]),
                tables_read_approximate=bool(r[17]),
            )
            for r in rows
        ]


__all__ = [
    "DEPLOYMENT_ACTOR_KIND",
    "MEMORY_BUDGET",
    "QUESTION_CAP",
    "SQL_CAP",
    "ActorKind",
    "AskAuditRecord",
    "AskAuditStorePort",
    "AskBadge",
    "AuditStoreUnavailable",
    "InMemoryAskAuditStore",
    "PostgresAskAuditStore",
    "has_sql_statement",
    "ledger_pointer",
    "record_from_envelope",
    "record_from_error",
    "scrub",
    "scrub_counted",
]
