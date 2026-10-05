"""BANK-02 (dms#269): write one audit row per ask.

The actor is server configuration, never the request (DR-0004 Option A): under it
there is no identity provider, so the only identity this can name is the
deployment's. The row says ``deployment`` so an auditor does not read it as a
person. When BANK-01 lands a verified principal, this is the one place that
changes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from dms_core.control_plane.ask_audit import (
    DEPLOYMENT_ACTOR_KIND,
    AskAuditStorePort,
    record_from_envelope,
    record_from_error,
)
from fastapi import HTTPException

from dms_api.settings import Settings
from dms_api.wiring import sql_tables_read

logger = logging.getLogger(__name__)


def _error_reason(exc: BaseException) -> str:
    """A short, stable reason for an ask that ended in an error."""
    if isinstance(exc, HTTPException):
        detail = exc.detail
        code = detail.get("code") if isinstance(detail, dict) else detail
        return f"http_{exc.status_code}:{code}"
    return f"error:{type(exc).__name__}"


def record_ask(
    store: AskAuditStorePort,
    settings: Settings,
    *,
    question: str,
    space_id: str | None,
    asked_at: datetime,
    envelope: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    """Append the ask's row. Never raises, and never loses a failure silently.

    A failed write must not turn a read into a 500, and it must not vanish: the
    store counts it in ``dropped`` and the export reports that count. Whether a
    failed audit write should refuse the answer instead is a policy call (an
    audit trail with holes against an answer service that stops when its audit
    store is down); this takes the available side and says so.
    """
    try:
        if envelope is not None:
            rec = record_from_envelope(
                envelope,
                question=question,
                actor=settings.dms_actor_user_id,
                actor_kind=DEPLOYMENT_ACTOR_KIND,
                space_id=space_id,
                tables_read=sql_tables_read(envelope.get("sql_used")),
                asked_at=asked_at,
            )
        else:
            rec = record_from_error(
                question=question,
                reason=_error_reason(error) if error is not None else "error:unknown",
                actor=settings.dms_actor_user_id,
                actor_kind=DEPLOYMENT_ACTOR_KIND,
                space_id=space_id,
                asked_at=asked_at,
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
