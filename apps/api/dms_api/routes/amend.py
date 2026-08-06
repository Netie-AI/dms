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
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/amend", tags=["amend"])

#: What confirming a proposal actually does today.
#:
#: ``confirm_proposal_version`` moves ``dms.proposal_versions.status`` from
#: ``pending_confirm`` to ``applied`` under an advisory lock, and that is the
#: whole of it — no ``call_action``, no warehouse write, nothing outside that
#: one row changes. "applied" is true of the *proposal version* and false of
#: the data, and a page that says "Confirmed" over an unchanged warehouse is
#: the difference the customer cannot see.
#:
#: A real apply needs an action endpoint on the contract surface (a contract
#: minor plus a regenerated client), an ``amend.apply`` action type and a
#: ``tool_runner`` branch. Until that lands, the response says plainly that
#: nothing was mutated rather than letting "applied" imply it.
MUTATION_DISCLOSURE = {
    "executed": False,
    "detail": (
        "Proposal version recorded as applied in the control plane. No warehouse "
        "data was changed: confirm does not yet invoke call_action, so nothing "
        "outside dms.proposal_versions has been mutated."
    ),
}


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
    enforce(decision)

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
    enforce(decision)

    try:
        with _conn(settings) as conn:
            set_tenant_context(conn, settings.dms_tenant_id, role="steward")
            result = confirm_proposal_version(
                conn,
                tenant_id=uuid.UUID(settings.dms_tenant_id),
                idempotency_token=body.idempotency_token,
            )
            result["proposal_id"] = str(result.get("proposal_id", proposal_id))

            # The receipt is part of the write, not something attempted after it.
            # This used to be a bare except that set ledger_entry_id=None and
            # committed anyway, so a confirm whose append failed still answered
            # 200 with status "applied" and left an accepted proposal version
            # that nothing in the ledger points at — a governed write with no
            # record that it happened, in exactly the outage where that matters.
            #
            # Appending inside the transaction, before the commit, makes the
            # rollback do the work: no receipt, no apply.
            if cortex is None:
                raise HTTPException(status_code=503, detail="ledger_unavailable")
            try:
                from cortex_client.models import LedgerAppendRequest

                appended = cortex.ledger_append(
                    LedgerAppendRequest(
                        event_type="amend.confirm",
                        payload={"proposal_id": proposal_id, "result": str(result)},
                        actor=settings.dms_actor_user_id,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=503, detail="ledger_append_failed") from exc

            conn.execute(
                """
                INSERT INTO dms.ledger_ref (tenant_id, cortex_entry_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (uuid.UUID(settings.dms_tenant_id), appended.entry_id),
            )
            result["ledger_entry_id"] = appended.entry_id
            conn.commit()
            return {
                "id": str(result["id"]),
                "proposal_id": str(result["proposal_id"]),
                "version_num": result["version_num"],
                "status": result["status"],
                "ledger_entry_id": result.get("ledger_entry_id"),
                "mutation": MUTATION_DISCLOSURE,
            }
    except StaleTokenError as exc:
        raise HTTPException(status_code=409, detail="stale_token") from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail="conflict") from exc
