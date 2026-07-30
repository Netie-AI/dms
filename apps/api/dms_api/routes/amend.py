"""Amend proposals HTTP — versioned confirm over control_plane.proposals."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from cortex_client import compliance_gate
from dms_core.control_plane.proposals import (
    ConflictError,
    StaleTokenError,
    confirm_proposal_version,
    create_proposal_version,
)
from dms_core.control_plane.session import set_tenant_context
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep, SettingsDep

router = APIRouter(prefix="/v1/amend", tags=["amend"])


class ProposeBody(BaseModel):
    space_id: str | None = None
    diff: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class ConfirmBody(BaseModel):
    idempotency_token: str


def _conn(settings: SettingsDep):
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="database_unavailable")
    return psycopg.connect(settings.database_url)


@router.get("/proposals")
def list_proposals(settings: SettingsDep) -> list[dict[str, Any]]:
    if not settings.database_url:
        return []
    with _conn(settings) as conn:
        set_tenant_context(conn, settings.dms_tenant_id, role="steward")
        rows = conn.execute(
            """
            SELECT p.id::text, p.space_id::text, p.created_at,
                   v.version_num, v.status, v.idempotency_token, v.diff
              FROM dms.proposals p
              LEFT JOIN LATERAL (
                SELECT * FROM dms.proposal_versions pv
                 WHERE pv.proposal_id = p.id
                 ORDER BY pv.version_num DESC LIMIT 1
              ) v ON true
             WHERE p.tenant_id::text = %s
             ORDER BY p.created_at DESC
             LIMIT 50
            """,
            (settings.dms_tenant_id,),
        ).fetchall()
        conn.commit()
    return [
        {
            "id": r[0],
            "space_id": r[1],
            "created_at": r[2].isoformat() if r[2] else None,
            "version_num": r[3],
            "status": r[4],
            "idempotency_token": r[5],
            "diff": r[6] or {},
        }
        for r in rows
    ]


@router.post("/proposals")
def propose(
    body: ProposeBody,
    settings: SettingsDep,
    cortex: CortexDep,
    request: Request,
) -> dict[str, Any]:
    decision = compliance_gate(
        action="amend.propose",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "amend.propose", "summary": body.summary},
        client=cortex,
    )
    if not decision.allowed and decision.reason not in {
        "gate_unavailable",
        "gate_task_unknown",
    }:
        raise HTTPException(status_code=403, detail=decision.reason)

    diff = dict(body.diff)
    if body.summary:
        diff["summary"] = body.summary
    with _conn(settings) as conn:
        set_tenant_context(conn, settings.dms_tenant_id, role="steward")
        proposal_id = uuid.uuid4()
        space = uuid.UUID(body.space_id) if body.space_id else None
        conn.execute(
            """
            INSERT INTO dms.proposals (id, tenant_id, space_id, created_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                proposal_id,
                uuid.UUID(settings.dms_tenant_id),
                space,
                uuid.UUID(settings.dms_actor_user_id),
            ),
        )
        ver = create_proposal_version(
            conn,
            proposal_id=proposal_id,
            tenant_id=uuid.UUID(settings.dms_tenant_id),
            diff=diff,
        )
        conn.commit()
    return {
        "proposal_id": str(proposal_id),
        "version_num": ver["version_num"],
        "idempotency_token": ver["idempotency_token"],
        "status": ver["status"],
        "diff": diff,
    }


@router.post("/proposals/{proposal_id}/confirm")
def confirm(
    proposal_id: str,
    body: ConfirmBody,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    decision = compliance_gate(
        action="amend.confirm",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "amend.confirm", "proposal_id": proposal_id},
        client=cortex,
    )
    if not decision.allowed and decision.reason not in {
        "gate_unavailable",
        "gate_task_unknown",
    }:
        raise HTTPException(status_code=403, detail=decision.reason)

    try:
        with _conn(settings) as conn:
            set_tenant_context(conn, settings.dms_tenant_id, role="steward")
            result = confirm_proposal_version(
                conn,
                tenant_id=uuid.UUID(settings.dms_tenant_id),
                idempotency_token=body.idempotency_token,
            )
            result["proposal_id"] = str(result.get("proposal_id", proposal_id))
            # ledger_ref pointer placeholder until Cortex append succeeds
            if cortex is not None:
                try:
                    from cortex_client.models import LedgerAppendRequest

                    appended = cortex.ledger_append(
                        LedgerAppendRequest(
                            event_type="amend.confirm",
                            payload={"proposal_id": proposal_id, "result": str(result)},
                            actor=settings.dms_actor_user_id,
                        )
                    )
                    conn.execute(
                        """
                        INSERT INTO dms.ledger_ref (tenant_id, cortex_entry_id)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (uuid.UUID(settings.dms_tenant_id), appended.entry_id),
                    )
                    result["ledger_entry_id"] = appended.entry_id
                except Exception:  # noqa: BLE001
                    result["ledger_entry_id"] = None
            conn.commit()
            return {
                "id": str(result["id"]),
                "proposal_id": str(result["proposal_id"]),
                "version_num": result["version_num"],
                "status": result["status"],
                "ledger_entry_id": result.get("ledger_entry_id"),
            }
    except StaleTokenError as exc:
        raise HTTPException(status_code=409, detail="stale_token") from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail="conflict") from exc
