"""Register a connected source's Space and grant only its exposed tables.

Postgres grants stay parked. The book is process memory. Every mutation
checks the configured service bearer (the same token OpenVault already
uses) and then compliance_gate. The bearer is not a new mint and is not
written to the audit row; the row stores a short hash id.
"""

from __future__ import annotations

from typing import Annotated, Any

from cortex_client import compliance_gate
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep, SettingsDep, SpaceStoreDep, StoreBindingDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import (
    connect_audit_rows,
    connect_registered_source,
    grant_connected_tables,
    revoke_connected_tables,
    service_bearer_id,
)

router = APIRouter(prefix="/v1/connect", tags=["connect"])


class ConnectIn(BaseModel):
    connector_id: str = Field(min_length=1, max_length=128)
    space_name: str = Field(min_length=1, max_length=120)
    #: Subset of the connector listing. Omit to grant every exposed table.
    grant_tables: list[str] | None = Field(default=None, max_length=64)


class TablesIn(BaseModel):
    tables: list[str] = Field(min_length=1, max_length=64)


def _require_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="bearer_denied")
    token_id = service_bearer_id(authorization)
    if token_id is None:
        raise HTTPException(status_code=401, detail="bearer_denied")
    return token_id


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        missing = str(exc.args[0]) if exc.args else ""
        if missing == "connector" or not missing:
            return HTTPException(status_code=404, detail="connector_unknown")
        return HTTPException(status_code=404, detail="source_not_found")
    code = str(exc)
    if code == "space_name_taken":
        return HTTPException(status_code=409, detail=code)
    if code == "connector_unknown":
        return HTTPException(status_code=404, detail=code)
    if code in {"table_not_exposed", "table_not_granted", "bad_table_name", "space_not_found"}:
        status = 404 if code == "space_not_found" else 400
        return HTTPException(status_code=status, detail=code)
    return HTTPException(status_code=400, detail="bad_request")


@router.post("/sources", status_code=201)
def connect_source(
    body: ConnectIn,
    cortex: CortexDep,
    settings: SettingsDep,
    store: SpaceStoreDep,
    binding: StoreBindingDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Read the connector's schema listing and grant this Space only those tables."""
    token_id = _require_bearer(authorization)
    decision = compliance_gate(
        action="connect.register",
        actor=settings.dms_actor_user_id,
        metadata={
            "task_id": "connect.register",
            "connector_id": body.connector_id,
        },
        client=cortex,
    )
    enforce(decision)
    try:
        registered = connect_registered_source(
            store=store,
            connector_id=body.connector_id,
            space_name=body.space_name,
            grant_tables=body.grant_tables,
            actor=settings.dms_actor_user_id,
            token_id=token_id,
        )
    except (KeyError, ValueError) as exc:
        raise _map_error(exc) from None
    return {
        **registered,
        "persisted": binding.persistent,
        "storage": binding.as_dict(),
        "hint": binding.hint,
    }


@router.post("/sources/{source_id}/grants")
def grant_source(
    source_id: str,
    body: TablesIn,
    cortex: CortexDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    token_id = _require_bearer(authorization)
    decision = compliance_gate(
        action="connect.grant",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.grant", "source_id": source_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return grant_connected_tables(
            source_id=source_id,
            tables=body.tables,
            actor=settings.dms_actor_user_id,
            token_id=token_id,
        )
    except (KeyError, ValueError) as exc:
        raise _map_error(exc) from None


@router.post("/sources/{source_id}/revoke")
def revoke_source(
    source_id: str,
    body: TablesIn,
    cortex: CortexDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    token_id = _require_bearer(authorization)
    decision = compliance_gate(
        action="connect.revoke",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.revoke", "source_id": source_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return revoke_connected_tables(
            source_id=source_id,
            tables=body.tables,
            actor=settings.dms_actor_user_id,
            token_id=token_id,
        )
    except (KeyError, ValueError) as exc:
        raise _map_error(exc) from None


@router.get("/audit")
def list_connect_audit(
    cortex: CortexDep,
    settings: SettingsDep,
    binding: StoreBindingDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    token_id = _require_bearer(authorization)
    decision = compliance_gate(
        action="connect.audit.read",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.audit.read", "token_id": token_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return {
        "rows": connect_audit_rows(),
        "persisted": binding.persistent,
        "storage": binding.as_dict(),
    }
