"""Register a connected source's Space and grant only its exposed tables.

Off unless ``DMS_CONNECT_API`` is 1/true/yes/on. The book is process memory
(lost on restart, not shared across workers). The body carries an OpenVault
credential reference. A raw secret is rejected and is not logged, stored,
audited, or copied into the error.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from cortex_client import compliance_gate
from dms_core.control_plane.connect_secrets import (
    ConnectCredentialError,
    body_violation,
    key_id_for_ref,
)
from fastapi import APIRouter, Header, HTTPException, Request

from dms_api.deps import (
    AskServiceDep,
    CortexDep,
    SettingsDep,
    SpaceStoreDep,
    StoreBindingDep,
)
from dms_api.gatekeeping import enforce
from dms_api.settings import Settings
from dms_api.wiring import (
    connect_audit_rows,
    connect_registered_source,
    grant_connected_tables,
    revoke_connected_tables,
    service_bearer_id,
)

router = APIRouter(prefix="/v1/connect", tags=["connect"])

_MAX_BODY = 16_384
_DISABLED = "connect_api_disabled"
_RAW_SECRET = "connect_raw_secret_rejected"


def _enabled(settings: Settings) -> None:
    if not settings.dms_connect_api:
        raise HTTPException(status_code=404, detail=_DISABLED)


def _require_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="bearer_denied")
    token_id = service_bearer_id(authorization)
    if token_id is None:
        raise HTTPException(status_code=401, detail="bearer_denied")
    return token_id


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ConnectCredentialError):
        return HTTPException(status_code=400, detail="connect_credential_unresolved")
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="source_not_found")
    code = str(exc)
    if code == "space_name_taken":
        return HTTPException(status_code=409, detail=code)
    if code == "connector_unknown":
        return HTTPException(status_code=404, detail=code)
    if code in {
        "table_not_exposed",
        "table_not_granted",
        "bad_table_name",
        "connector_failed",
    }:
        return HTTPException(status_code=400, detail=code)
    if code == "space_not_found":
        return HTTPException(status_code=404, detail=code)
    return HTTPException(status_code=400, detail="bad_request")


async def _object(request: Request, *, schema: str) -> dict[str, Any]:
    """Parse a JSON object against the allow-list. No echo of rejected text."""
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        raise HTTPException(status_code=400, detail="bad_request")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="bad_request") from None
    code = body_violation(payload, schema=schema)
    if code == _RAW_SECRET:
        raise HTTPException(status_code=400, detail=_RAW_SECRET)
    if code:
        raise HTTPException(status_code=400, detail=code)
    return payload  # type: ignore[return-value]


def _tables(payload: dict[str, Any], key: str, *, required: bool) -> list[str] | None:
    if key not in payload:
        if required:
            raise HTTPException(status_code=400, detail="bad_request")
        return None
    raw = payload[key]
    if not isinstance(raw, list) or not raw or len(raw) > 64:
        raise HTTPException(status_code=400, detail="bad_request")
    if not all(isinstance(item, str) for item in raw):
        raise HTTPException(status_code=400, detail="bad_request")
    return raw


@router.post("/sources", status_code=201)
async def connect_source(
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    store: SpaceStoreDep,
    binding: StoreBindingDep,
    ask: AskServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Read the connector listing for an OpenVault reference and grant those tables."""
    _enabled(settings)
    payload = await _object(request, schema="sources")
    token_id = _require_bearer(authorization)
    connector_id = payload.get("connector_id")
    space_name = payload.get("space_name")
    credential_ref = payload.get("credential_ref")
    if not isinstance(connector_id, str) or not connector_id.strip():
        raise HTTPException(status_code=400, detail="bad_request")
    if not isinstance(space_name, str) or not space_name.strip():
        raise HTTPException(status_code=400, detail="bad_request")
    if not isinstance(credential_ref, str) or not credential_ref.strip():
        raise HTTPException(status_code=400, detail="credential_ref_required")
    if key_id_for_ref(credential_ref.strip()) is None:
        raise HTTPException(status_code=400, detail="bad_credential_ref")
    options = payload.get("options")
    if options is None:
        grant_tables = None
    else:
        grant_tables = _tables(options, "grant_tables", required=False)
    decision = compliance_gate(
        action="connect.register",
        actor=settings.dms_actor_user_id,
        metadata={
            "task_id": "connect.register",
            "connector_id": connector_id.strip(),
        },
        client=cortex,
    )
    enforce(decision)
    try:
        registered = connect_registered_source(
            store=store,
            ask=ask,
            connector_id=connector_id.strip(),
            space_name=space_name.strip(),
            grant_tables=grant_tables,
            credential_ref=credential_ref.strip(),
            actor=settings.dms_actor_user_id,
            token_id=token_id,
        )
    except (KeyError, ValueError, ConnectCredentialError) as exc:
        raise _map_error(exc) from None
    return {
        **registered,
        "persisted": binding.persistent,
        "storage": binding.as_dict(),
        "hint": binding.hint,
    }


@router.post("/sources/{source_id}/grants")
async def grant_source(
    source_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _enabled(settings)
    payload = await _object(request, schema="tables")
    token_id = _require_bearer(authorization)
    tables = _tables(payload, "tables", required=True)
    assert tables is not None
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
            tables=tables,
            actor=settings.dms_actor_user_id,
            token_id=token_id,
        )
    except (KeyError, ValueError) as exc:
        raise _map_error(exc) from None


@router.post("/sources/{source_id}/revoke")
async def revoke_source(
    source_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _enabled(settings)
    payload = await _object(request, schema="tables")
    token_id = _require_bearer(authorization)
    tables = _tables(payload, "tables", required=True)
    assert tables is not None
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
            tables=tables,
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
    _enabled(settings)
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
