"""Audit — ledger_ref pointers only (no local hash chain)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from cortex_client import compliance_gate
from dms_core.control_plane.session import set_tenant_context
from fastapi import APIRouter, HTTPException

from dms_api.deps import CortexDep, SettingsDep
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
