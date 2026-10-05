"""Audit — ledger_ref pointers only (no local hash chain), and the ask export."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import psycopg
from cortex_client import CortexClient, compliance_gate
from dms_core.control_plane.ask_audit_export import (
    ExportFormat,
    LedgerVerification,
    to_csv,
    to_jsonl,
)
from dms_core.control_plane.session import set_tenant_context
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from dms_api.deps import AskAuditDep, CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("/ledger")
def list_ledger_refs(settings: SettingsDep, cortex: CortexDep) -> list[dict[str, Any]]:
    # Gated before the connection opens, not after the rows are read. A read
    # that has already happened cannot be un-read by a later refusal (A-0007-03).
    decision = compliance_gate(
        action="audit.ledger.read",
        metadata={"task_id": "audit.ledger.read"},
        client=cortex,
    )
    # mutation=False: these are reads. gatekeeping.py is explicit that an
    # unreachable gate must not refuse a read - "refusing to answer a question
    # is not the same risk as applying an unrecorded change". Without this the
    # three routes would 403 whenever Cortex is down, which is a control
    # refusing legitimate work (R-0005), not a boundary holding.
    enforce(decision, mutation=False)
    if not settings.database_url:
        return []
    with psycopg.connect(settings.database_url) as conn:
        set_tenant_context(conn, settings.dms_tenant_id, role="viewer")
        rows = conn.execute(
            """
            SELECT seq, cortex_entry_id, created_at
              FROM dms.ledger_ref
             WHERE tenant_id = %s
             ORDER BY seq DESC
             LIMIT 100
            """,
            (UUID(settings.dms_tenant_id),),
        ).fetchall()
        conn.commit()
    return [
        {
            "seq": r[0],
            "cortex_entry_id": r[1],
            "created_at": r[2].isoformat() if r[2] else None,
        }
        for r in rows
    ]


@router.post("/ledger/verify")
def verify_ledger(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="audit.verify",
        metadata={"task_id": "audit.verify"},
        client=cortex,
    )
    enforce(decision)
    if cortex is None:
        raise HTTPException(status_code=503, detail="cortex_unavailable")
    result = cortex.verify_ledger()
    return {
        "ok": result.ok,
        "first_break": result.first_break,
        "checked": result.checked,
    }


#: A guard on one request's memory, not a retention policy. An export over more
#: rows than this is refused with 413 and asks for a narrower range. It is never
#: truncated: a silently short audit export is worse than none.
MAX_EXPORT_ROWS = 50_000

#: DR-0004 Option A, said in the response so the file cannot be read as naming a person.
ACTOR_BASIS = "deployment identity (DR-0004 option A); not a person"


def _parse_bound(raw: str | None, *, name: str, upper: bool) -> datetime | None:
    """``[from, to)`` in UTC. A bare date means that whole day: ``to`` is its end."""
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_range", "message": f"{name} is not an ISO 8601 date or time"},
        ) from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if upper and len(text) == 10:
        value += timedelta(days=1)
    return value.astimezone(UTC)


def _verify_chain(cortex: CortexClient | None) -> LedgerVerification:
    """Run ``ledger/verify`` and keep only what the export may say about it.

    Anything short of ``ok`` is unverified, including a ledger that could not be
    reached. The failure text is dropped: a transport error can quote a URL.
    """
    now = datetime.now(UTC)
    if cortex is None:
        return LedgerVerification("unavailable", None, None, now)
    try:
        result = cortex.verify_ledger()
    except Exception:  # noqa: BLE001 - an unreachable ledger is "unverified", not a 500
        return LedgerVerification("unavailable", None, None, now)
    if result.ok:
        return LedgerVerification("ok", None, result.checked, now)
    return LedgerVerification("break", result.first_break, result.checked, now)


@router.get("/export")
def export_asks(
    settings: SettingsDep,
    cortex: CortexDep,
    audit: AskAuditDep,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    fmt: Annotated[ExportFormat, Query(alias="format")] = "csv",
) -> Response:
    """BANK-02 (dms#269): one row per ask - who, what, which SQL and tables, what came back.

    ``from`` is inclusive and ``to`` exclusive; a bare date means the whole day.
    The ledger-verification result rides on every row, and ``export_verified`` is
    false on a break or when the ledger cannot be reached. The actor column is the
    deployment identity, and ``actor_kind`` says so (DR-0004 Option A).
    """
    # Gated before any row is read; a read that has happened cannot be un-read.
    # mutation=False: this is a read, so an unreachable gate does not refuse it,
    # the same posture as GET /v1/audit/ledger.
    decision = compliance_gate(
        action="audit.export",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "audit.export", "format": fmt},
        client=cortex,
    )
    enforce(decision, mutation=False)

    since = _parse_bound(from_, name="from", upper=False)
    until = _parse_bound(to, name="to", upper=True)
    if since is not None and until is not None and since >= until:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_range", "message": "from must be before to"},
        )

    rows = audit.list_between(since=since, until=until, limit=MAX_EXPORT_ROWS + 1)
    if len(rows) > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "export_too_large",
                "message": f"more than {MAX_EXPORT_ROWS} asks in range; narrow from/to",
            },
        )

    verification = _verify_chain(cortex)
    if fmt == "csv":
        body, media = to_csv(rows, verification), "text/csv; charset=utf-8"
    else:
        body, media = to_jsonl(rows, verification), "application/x-ndjson; charset=utf-8"
    return Response(
        content=body.encode("utf-8"),
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="dms-ask-audit.{fmt}"',
            "Cache-Control": "no-store",
            "X-Audit-Export-Verified": "true" if verification.verified else "false",
            "X-Audit-Ledger-Verify": verification.status,
            "X-Audit-Actor-Basis": ACTOR_BASIS,
            "X-Audit-Store": audit.backend,
            "X-Audit-Unrecorded-Asks": str(audit.dropped),
        },
    )
