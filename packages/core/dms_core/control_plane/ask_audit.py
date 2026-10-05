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
to the entry where the ask had one.

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
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import psycopg

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

_REASON_CAP = 500
_REDACTED = "[redacted]"

# --- secret removal ----------------------------------------------------------
#
# Applied when a row is built (secrets are never persisted) and again when it is
# exported. Matching runs on a normalised view of the text (NFKC, lookalike
# letters folded, zero-width characters and /* */ comments dropped) so that
# ``pass/**/word=``, a Cyrillic ``a`` or a fullwidth ``=`` do not hide a pair;
# the replacement is made on the ORIGINAL text, so what is kept is not altered.
#
# Two failure modes bound the patterns. A leak is a secret left in the record.
# An over-redaction is ordinary text or SQL rewritten for good, which corrupts
# the audit record at write time, so patterns need a left word boundary, values
# that look like tokens, and they never rewrite an SQL identifier or number.

_INVISIBLE = frozenset("\u200b\u200c\u200d\u200e\u200f\u2060\u00ad\ufeff")
#: Letters from other scripts that read as Latin ones, for the words below.
_CONFUSABLES = {
    **dict.fromkeys("\u0430\u03b1", "a"),
    **dict.fromkeys("\u0435\u0454", "e"),
    **dict.fromkeys("\u043e\u03bf", "o"),
    **dict.fromkeys("\u0440\u03c1", "p"),
    **dict.fromkeys("\u0441\u03f2", "c"),
    "\u0445": "x",
    "\u0443": "y",
    "\u043a": "k",
    "\u043c": "m",
    "\u0442": "t",
    **dict.fromkeys("\u0456\u0131", "i"),
    "\u0455": "s",
    "\u0458": "j",
    "\u0501": "d",
    "\u051d": "w",
    "\u04bb": "h",
    "\u0261": "g",
    "\u043d": "h",
    "\u03bd": "v",
}

_NAME_CORE = (
    r"(?:pass(?:word|wd|phrase)|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|credentials?)"
)
_PASSWORD_FAMILY = re.compile(r"(?i)pass(?:word|wd|phrase)|pwd")
_NAME = rf"(?<![A-Za-z0-9])[A-Za-z0-9_.-]{{0,40}}?{_NAME_CORE}[A-Za-z0-9_]*"

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
)
_KEY_BODY = re.compile(r"(?<!\S)(?:[A-Za-z0-9+/]{40,}={0,2}[ \t]*\r?\n)+[A-Za-z0-9+/]{4,}={0,2}")
_PROVIDER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:sk-(?:ant-)?|gsk_|ov_|sk_live_|pk_live_|rk_live_)[A-Za-z0-9_-]{16,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)
#: scheme://user:PASSWORD@host - the password runs to the LAST @ of the token, so a
#: password holding "/" or "@" is covered. ``host:8080/x?e=a@b`` is a port, not a pair.
_URL_PASSWORD = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:(?P<v>(?!\d{1,5}(?:[/?#]|$))\S+)@(?=[^\s@]+)"
)
_AUTH_HEADER = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?"
    r"(?:(?:basic|bearer|digest|negotiate|token)\s+(?P<v1>[A-Za-z0-9._~+/=:-]{6,})"
    r"|(?P<v2>(?=[A-Za-z0-9._~+/=:-]*[\d+/=_-])[A-Za-z0-9._~+/=:-]{8,}))"
)
_BEARER = re.compile(
    r"(?i)(?<![A-Za-z0-9])bearer\s+"
    r"(?P<v>[A-Za-z0-9._~+/=-]{16,}|(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{8,})"
)
_ASSIGN = re.compile(
    rf"(?P<name>{_NAME})"
    r"(?:"
    r"(?P<op>[\"']?\s*(?::=|=>|==|!=|<>|[:=])\s*)"
    r"|(?P<wop>\s+(?:like|ilike|rlike|regexp)\s+)"
    r"|(?P<nop>\s+(?:is|was)\s+(?!(?:not|null|true|false|pending|empty|required|missing|set)\b))"
    r")"
    r"(?:(?P<q>[\"'])(?P<qv>(?:\\.|(?!(?P=q)).)*)(?P=q)|(?P<uv>[^\s,;&)}\]\"'`]+))",
    re.I | re.S,
)
_MULTIWORD = re.compile(r"[ \t]+((?:[^\s.?!,;]|[.?!](?!\s|$))+)")
_BLOB = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=_-])")
_NOT_A_VALUE = re.compile(r"(?i)^(?:null|none|nil|true|false|undefined|\*+|\?|%s|\$\{.*\}|<.*>)$")
_SQL_LITERAL = re.compile(r"'(?:[^']|'')*'")
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _view(text: str, *, drop_block_comments: bool) -> tuple[str, list[int], list[int]]:
    """Normalised text for matching, plus the original [start, end) of each character."""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i, n = 0, len(text)
    while i < n:
        if drop_block_comments and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j != -1:
                i = j + 2
                continue
        ch = text[i]
        if ch in _INVISIBLE:
            i += 1
            continue
        for c in unicodedata.normalize("NFKC", ch):
            chars.append(_CONFUSABLES.get(c, c))
            starts.append(i)
            ends.append(i + 1)
        i += 1
    return "".join(chars), starts, ends


def _looks_like_secret(value: str) -> bool:
    """A value worth redacting when only a word like "is" introduced it."""
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    has_symbol = any(c in "-_/+=!@#$%^&*.~" for c in value)
    return (has_digit and has_alpha and len(value) >= 6) or (has_symbol and len(value) >= 8)


def _blob_like(value: str) -> bool:
    """Base64-shaped and not a word: mixed case, and digits or base64 punctuation."""
    digits = sum(c.isdigit() for c in value)
    return (
        any(c.isupper() for c in value)
        and any(c.islower() for c in value)
        and (digits >= 2 or any(c in "+/=" for c in value))
    )


def _spans(v: str, *, sql: bool) -> list[tuple[int, int]]:
    """View ranges to redact. ``sql`` keeps identifiers, numbers and bare column
    references intact: only quoted literals, tokens and comments are touched."""
    out: list[tuple[int, int]] = []
    comments = [m.span() for m in _SQL_COMMENT.finditer(v)] if sql else []
    literals = [m.span() for m in _SQL_LITERAL.finditer(v)] if sql else []

    def inside(spans: list[tuple[int, int]], pos: int) -> bool:
        return any(a <= pos < b for a, b in spans)

    for m in _PRIVATE_KEY_BLOCK.finditer(v):
        out.append(m.span())
    for m in _KEY_BODY.finditer(v):
        out.append(m.span())
    for m in _PROVIDER_TOKEN.finditer(v):
        out.append(m.span())
    for m in _URL_PASSWORD.finditer(v):
        if m.group("v") != _REDACTED:
            out.append(m.span("v"))
    for m in _AUTH_HEADER.finditer(v):
        out.append(m.span("v1" if m.group("v1") else "v2"))
    for m in _BEARER.finditer(v):
        out.append(m.span("v"))
    for m in _ASSIGN.finditer(v):
        name = m.group("name")
        family = bool(_PASSWORD_FAMILY.search(name))
        if m.group("q"):
            inner = m.group("qv")
            if not inner or inner.startswith(_REDACTED):
                continue
            # A bare word after "is" is prose; a literal compared in SQL needs to
            # look like a secret unless the column is a password.
            if m.group("nop") and not _looks_like_secret(inner):
                continue
            if sql and not family and not _looks_like_secret(inner):
                continue
            out.append(m.span("qv"))
            continue
        val = m.group("uv")
        start = m.start("uv")
        if not val or v.startswith(_REDACTED, start) or _NOT_A_VALUE.match(val):
            continue
        if m.group("wop"):
            continue  # LIKE wants a quoted literal; a bare word is a column
        if sql and not inside(comments, start):
            continue  # token = t2.token, secret = 0: comparisons, not secrets
        if m.group("nop") and not _looks_like_secret(val.rstrip(".?!")):
            continue
        end = m.end("uv")
        while end > start and v[end - 1] in ".?!" and (end == len(v) or v[end].isspace()):
            end -= 1
        # A passphrase is words: take up to seven more after a first word that is
        # plain letters. A value with digits or symbols (DB_PASSWORD=x9, a token) is one.
        if family and not m.group("nop") and val.rstrip(".?!").isalpha():
            words = 0
            while words < 7:
                nxt = _MULTIWORD.match(v, end)
                if not nxt or "=" in nxt.group(1) or ":" in nxt.group(1):
                    break
                end, words = nxt.end(), words + 1
        if end > start:
            out.append((start, end))
    for m in _BLOB.finditer(v):
        if not _blob_like(m.group()):
            continue
        if sql and not (inside(literals, m.start()) or inside(comments, m.start())):
            continue
        out.append(m.span())
    return out


def scrub_counted(text: str | None, *, sql: bool = False) -> tuple[str, int]:
    """Remove secret-shaped values. Returns the clean text and how many were replaced.

    Pattern based and best effort: it cannot recognise an arbitrary string as a
    secret, only the shapes above. ``sql=True`` is for executed SQL.
    """
    original = text or ""
    if not original:
        return "", 0
    regions: list[tuple[int, int]] = []
    for drop in (False, True):
        view, starts, ends = _view(original, drop_block_comments=drop)
        for s, e in _spans(view, sql=sql):
            if e > s:
                regions.append((starts[s], ends[e - 1]))
    if not regions:
        return original, 0
    regions.sort()
    merged: list[list[int]] = []
    for s, e in regions:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, last = [], 0
    for s, e in merged:
        out.append(original[last:s])
        out.append(_REDACTED)
        last = e
    out.append(original[last:])
    return "".join(out), len(merged)


def scrub(text: str | None, *, sql: bool = False) -> str:
    return scrub_counted(text, sql=sql)[0]


def has_sql_statement(sql: str | None) -> bool:
    """True when ``sql`` holds a statement, not nothing or a comment-only placeholder."""
    return bool(_SQL_COMMENT.sub("", sql or "").strip())


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    return f"{text[:cap]}...[truncated {len(text) - cap} chars]", True


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
    #: How many values were replaced by ``[redacted]`` in this row, so an auditor
    #: can see that the text was altered. 0 means nothing was removed.
    redactions: int = 0
    #: The question or SQL was cut at the recorded-length cap (the cut is marked in the text).
    truncated: bool = False

    def scrubbed(self) -> AskAuditRecord:
        """Scrub again (export time). Adds to ``redactions`` what this pass removed."""
        extra = 0

        def text(value: str) -> str:
            nonlocal extra
            clean, n = scrub_counted(value)
            extra += n
            return clean

        executed, n_sql = scrub_counted(self.executed_sql, sql=True)
        extra += n_sql
        return replace(
            self,
            actor=text(self.actor),
            space_id=text(self.space_id),
            question=text(self.question),
            executed_sql=executed,
            tables_read=tuple(text(t) for t in self.tables_read),
            abstain_reason=text(self.abstain_reason),
            cortex_entry_id=text(self.cortex_entry_id),
            redactions=self.redactions + extra,
        )


def _one_line(text: str, cap: int = _REASON_CAP) -> str:
    return " ".join(str(text).split())[:cap]


def _ledger_pointer(env: dict[str, Any]) -> str:
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
    badge: str,
    badge_level: str,
    abstain_reason: str,
    row_count: int,
    cortex_entry_id: str,
    ask_mode: str,
    asked_at: datetime | None,
) -> AskAuditRecord:
    # Cut first, scrub second: the scrub only ever sees a bounded text.
    q, q_cut = _truncate(question, QUESTION_CAP)
    s, s_cut = _truncate(executed_sql if has_sql_statement(executed_sql) else "", SQL_CAP)
    q, n_q = scrub_counted(q)
    s, n_s = scrub_counted(s, sql=True)
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
    asked_at: datetime | None = None,
) -> AskAuditRecord:
    """Build the row for an ask that produced an envelope, abstains included.

    ``executed_sql``, ``tables_read`` and ``row_count`` are what ran, supplied by
    the caller from the engine side. The envelope is not their source: it drops
    the SQL and rows of an abstain and substitutes placeholders.
    """
    abstained = bool(env.get("abstained")) or str(env.get("badge") or "").upper() == "ABSTAIN"
    return _build(
        question=question,
        executed_sql=executed_sql,
        actor=actor,
        actor_kind=actor_kind,
        space_id=space_id,
        tables_read=tables_read,
        badge="abstain" if abstained else "validated",
        badge_level=str(env.get("badge") or ""),
        abstain_reason=_abstain_reason(env) if abstained else "",
        row_count=row_count,
        cortex_entry_id=_ledger_pointer(env),
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
) -> AskAuditRecord:
    """Build the row for an ask that ended in an error rather than an envelope."""
    return _build(
        question=question,
        executed_sql=executed_sql,
        actor=actor,
        actor_kind=actor_kind,
        space_id=space_id,
        tables_read=tables_read,
        badge="error",
        badge_level="",
        abstain_reason=_one_line(reason),
        row_count=row_count,
        cortex_entry_id="",
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
    "ask_id, asked_at, actor, actor_kind, space_id, question, executed_sql, "
    "tables_read, badge, badge_level, abstain_reason, row_count, cortex_entry_id, "
    "ask_mode, redactions, truncated"
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
        try:
            with self._connect() as conn:
                set_tenant_context(conn, self._tenant_id, role="steward")
                conn.execute(
                    f"""
                    INSERT INTO dms.ask_audit (tenant_id, {_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        rec.redactions,
                        rec.truncated,
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
                       row_count, cortex_entry_id, ask_mode, redactions, truncated
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
    "record_from_envelope",
    "record_from_error",
    "scrub",
    "scrub_counted",
]
