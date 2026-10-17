"""BANK-02 (dms#269): write one audit row per ask.

The actor is server configuration, never the request (DR-0004 Option A): under it
there is no identity provider, so the only identity this can name is the
deployment's. The row says ``deployment`` so an auditor does not read it as a
person. When BANK-01 lands a verified principal, this is the one place that
changes.

The executed SQL, the tables it read and the row count come from what the engine
actually ran (``executed``, read from the ask service right after the ask), not
from the customer envelope: an abstain's envelope carries no SQL and no rows, and
a document answer carries a placeholder comment, none of which is a record of what
ran. The envelope is only the fallback for a path that ran nothing through Cortex.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from dms_core.ask import ExecutedTrace
from dms_core.control_plane.ask_audit import (
    DEPLOYMENT_ACTOR_KIND,
    AskAuditStorePort,
    has_sql_statement,
    record_from_envelope,
    record_from_error,
)
from fastapi import HTTPException

from dms_api.settings import Settings
from dms_api.wiring import sql_tables_read_checked

logger = logging.getLogger(__name__)


def _error_reason(exc: BaseException) -> str:
    """A short, stable reason for an ask that ended in an error."""
    if isinstance(exc, HTTPException):
        detail = exc.detail
        code = detail.get("code") if isinstance(detail, dict) else detail
        return f"http_{exc.status_code}:{code}"
    return f"error:{type(exc).__name__}"


def take_executed(ask: object) -> ExecutedTrace:
    """What the ask service ran for the ask just finished on this thread, if it can say.

    Also how a request starts clean: calling it first drops whatever a path that
    never took its trace left on this thread, so a stale trace is not the next
    request's evidence.
    """
    taker = getattr(ask, "take_executed", None)
    if not callable(taker):
        return ExecutedTrace()
    try:
        got = taker()
    except Exception:  # noqa: BLE001 - the trace is evidence, never a reason to fail an ask
        logger.warning("executed-statement trace unreadable")
        return ExecutedTrace()
    return got if isinstance(got, ExecutedTrace) else ExecutedTrace()


def _evidence(
    envelope: dict[str, Any] | None, statements: Sequence[tuple[str, int]]
) -> tuple[str, tuple[str, ...], int, bool]:
    """``(executed_sql, tables_read, row_count, tables_approximate)`` for the row.

    Statements the engine ran win. Several are kept in order, and the row count
    is the last one's. Only when none ran through Cortex is the envelope's own
    ``sql_used`` read, and a comment-only placeholder there is no SQL at all.
    ``tables_approximate`` is True when any statement was too long to parse (or would
    not parse) and its tables came from a bounded name scan.
    """
    stmts = [(sql, n) for sql, n in statements if has_sql_statement(sql)]
    if stmts:
        sql_text = (
            stmts[0][0]
            if len(stmts) == 1
            else ";\n".join(s.strip().rstrip(";").rstrip() for s, _ in stmts)
        )
        tables: dict[str, str] = {}
        approximate = False
        for sql, _ in stmts:
            names, rough = sql_tables_read_checked(sql)
            approximate = approximate or rough
            for t in names:
                tables.setdefault(t.casefold(), t)
        return (
            sql_text,
            tuple(sorted(tables.values(), key=str.casefold)),
            stmts[-1][1],
            approximate,
        )
    rows = [r for r in ((envelope or {}).get("rows") or []) if isinstance(r, dict)]
    shown_sql = (envelope or {}).get("sql_used")
    if has_sql_statement(shown_sql):
        names, rough = sql_tables_read_checked(shown_sql)
        return str(shown_sql), names, len(rows), rough
    return "", (), len(rows), False


def record_ask(
    store: AskAuditStorePort,
    settings: Settings,
    *,
    question: str,
    space_id: str | None,
    asked_at: datetime,
    envelope: dict[str, Any] | None = None,
    error: BaseException | None = None,
    executed: ExecutedTrace | None = None,
) -> None:
    """Append the ask's row. Never raises, and never loses a failure silently.

    A failed write must not turn a read into a 500, and it must not vanish: the
    store counts it in ``dropped`` and the export reports that count. Whether a
    failed audit write should refuse the answer instead is a policy call (an
    audit trail with holes against an answer service that stops when its audit
    store is down); this takes the available side and says so.
    """
    trace = executed or ExecutedTrace()
    try:
        executed_sql, tables_read, row_count, approximate = _evidence(envelope, trace.statements)
        if envelope is not None:
            rec = record_from_envelope(
                envelope,
                question=question,
                actor=settings.dms_actor_user_id,
                actor_kind=DEPLOYMENT_ACTOR_KIND,
                space_id=space_id,
                executed_sql=executed_sql,
                tables_read=tables_read,
                row_count=row_count,
                ledger=trace.ledger,
                asked_at=asked_at,
                tables_read_approximate=approximate,
            )
        else:
            rec = record_from_error(
                question=question,
                reason=_error_reason(error) if error is not None else "error:unknown",
                actor=settings.dms_actor_user_id,
                actor_kind=DEPLOYMENT_ACTOR_KIND,
                space_id=space_id,
                executed_sql=executed_sql,
                tables_read=tables_read,
                row_count=row_count,
                asked_at=asked_at,
                tables_read_approximate=approximate,
            )
        store.record(rec)
    except Exception as exc:  # noqa: BLE001 - the audit write must not break the ask
        store.dropped += 1
        # The exception type only. A driver error can quote the failing row or the
        # connection string, and neither belongs in a log.
        logger.error(
            "ask audit row not recorded (%s); %d dropped this run",
            type(exc).__name__,
            store.dropped,
        )
