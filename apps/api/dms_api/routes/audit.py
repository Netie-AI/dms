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
def list_ledger_refs(settings: SettingsDep) -> list[dict[str, Any]]:
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
