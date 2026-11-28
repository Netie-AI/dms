"""Runnable Create apps (DMS-CREATE-APP-01).

``DMS_CREATE_APP`` defaults off. Status is always mounted and answers
``enabled: false`` while the flag is off. The other routes stay unmounted.
Salesforce apply stays off unless ``DMS_CREATE_APP_SALESFORCE`` is the digit 1.
"""

from __future__ import annotations

import os
from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.mcp_server.auth import bearer_from_headers, lookup_bearer
from dms_api.routes.studio_create import ContextIn
from dms_api.wiring import (
    create_app_chain,
    create_app_chain_answer,
    create_app_confirm,
    create_app_contract,
    create_app_dry_run,
    create_app_generate,
    create_app_preview,
    create_app_publish,
    create_app_query,
    create_app_read,
    create_caller_key,
)

FLAG = "DMS_CREATE_APP"

router = APIRouter(prefix="/v1/studio/apps", tags=["studio"])
status_router = APIRouter(prefix="/v1/studio/apps", tags=["studio"])


def enabled() -> bool:
    """``DMS_CREATE_APP=1`` exactly. Unset is off."""
    return os.environ.get(FLAG, "").strip() == "1"


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=400)
    grants: list[str] | None = None
    catalog: list[dict[str, Any]] | None = None
    context: ContextIn | None = None


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] | None = None
    approval_ref: str | None = Field(default=None, max_length=80)


class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=4000)


class ChainIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=16)
    goal: str = Field(min_length=1, max_length=400)
    grants: list[str] | None = None
    audience: str = Field(default="operator", max_length=200)


class AnswerChainIn(ChainIn):
    source_ref: str = Field(min_length=1, max_length=128)
    space_id: str = Field(min_length=1, max_length=128)
    grants: list[str] = Field(default_factory=list)


def _actor(request: Request, settings: Any) -> str:
    del settings
    token = bearer_from_headers(request.headers)
    if not token:
        raise HTTPException(status_code=401, detail="dms_api_key_required")
    caller, why = lookup_bearer(token)
    if caller is None:
        raise HTTPException(status_code=401, detail=why or "dms_api_key_required")
    return caller.actor


def _key(request: Request) -> str:
    raw = create_caller_key(request.headers.get("x-openvault-key"))
    if not raw.startswith("ov_") or " " in raw or len(raw) <= 3:
        raise HTTPException(status_code=401, detail="ov_api_key_required")
    return raw


def _http(exc: BaseException) -> HTTPException:
    name = type(exc).__name__
    if name == "CreateAppDisabled":
        return HTTPException(status_code=404, detail="Not Found")
    if name == "CreateAppProblem" and exc.args and isinstance(exc.args[0], str):
        code = exc.args[0]
        if code == "context_forbidden":
            return HTTPException(status_code=403, detail=code)
        return HTTPException(status_code=400, detail=code)
    raise exc


@status_router.get("/status")
def app_status(cortex: CortexDep, settings: SettingsDep) -> dict[str, bool]:
    """Flag off is ``enabled: false``. The other routes stay unmounted."""
    if not enabled():
        return {"enabled": False, "salesforce": False}
    enforce(
        compliance_gate(
            action="studio.create_app_status",
            actor="status",
            metadata={"task_id": "studio.create_app_status", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    del settings
    salesforce = os.environ.get("DMS_CREATE_APP_SALESFORCE", "").strip() == "1"
    return {"enabled": True, "salesforce": salesforce}


@router.get("/contract")
def app_contract(request: Request, cortex: CortexDep, settings: SettingsDep) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app_contract",
            actor=actor,
            metadata={"task_id": "studio.create_app_contract", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    return create_app_contract()


@router.post("")
def generate(
    body: GenerateIn, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app",
            actor=actor,
            metadata={"task_id": "studio.create_app", "space_id": body.space_id},
            client=cortex,
        ),
        mutation=True,
    )
    try:
        return create_app_generate(
            space_id=body.space_id,
            goal=body.goal,
            actor=actor,
            api_key=_key(request),
            grants=body.grants,
            catalog=body.catalog,
            context=body.context.model_dump() if body.context is not None else None,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP
        raise _http(exc) from None


@router.get("/{app_id}")
def read_app(
    app_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app_read",
            actor=actor,
            metadata={"task_id": "studio.create_app_read", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_app_read(app_id=app_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.get("/{app_id}/preview", response_class=HTMLResponse)
def preview(
    app_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> HTMLResponse:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app_preview",
            actor=actor,
            metadata={"task_id": "studio.create_app_preview", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return HTMLResponse(create_app_preview(app_id=app_id))
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/{app_id}/publish")
def publish(
    app_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app_publish",
            actor=actor,
            metadata={"task_id": "studio.create_app_publish", "space_id": ""},
            client=cortex,
        ),
        mutation=True,
    )
    try:
        return create_app_publish(app_id=app_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/{app_id}/query")
def query_sql(
    app_id: str, body: QueryIn, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    """The generated client calls this. The app token is the credential."""
    presented = request.headers.get("x-dms-app-token", "")
    del settings
    enforce(
        compliance_gate(
            action="studio.create_app_query",
            actor="app",
            metadata={"task_id": "studio.create_app_query", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_app_query(app_id=app_id, sql=body.sql, presented=presented)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/{app_id}/actions/dry-run")
def dry_run(
    app_id: str, body: ActionIn, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app_action",
            actor=actor,
            metadata={"task_id": "studio.create_app_action", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_app_dry_run(
            app_id=app_id, action_id=body.action_id, actor=actor, payload=body.payload
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/{app_id}/actions/confirm")
def confirm(
    app_id: str, body: ActionIn, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app_action",
            actor=actor,
            metadata={"task_id": "studio.create_app_action", "space_id": ""},
            client=cortex,
        ),
        mutation=True,
    )
    try:
        return create_app_confirm(
            app_id=app_id,
            action_id=body.action_id,
            actor=actor,
            approval_ref=body.approval_ref,
            payload=body.payload,
            tenant_id=str(settings.dms_tenant_id),
            space_id=None,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/{app_id}/chain")
def chain(
    app_id: str, body: ChainIn, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app_chain",
            actor=actor,
            metadata={"task_id": "studio.create_app_chain", "space_id": ""},
            client=cortex,
        ),
        mutation=True,
    )
    try:
        return create_app_chain(
            app_id=app_id,
            kind=body.kind,
            goal=body.goal,
            actor=actor,
            api_key=_key(request),
            audience=body.audience,
            tenant_id=str(settings.dms_tenant_id),
            requested_grants=body.grants,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/from-answer")
def chain_from_answer(
    body: AnswerChainIn, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_app_chain",
            actor=actor,
            metadata={"task_id": "studio.create_app_chain", "space_id": body.space_id},
            client=cortex,
        ),
        mutation=True,
    )
    try:
        return create_app_chain_answer(
            source_ref=body.source_ref,
            kind=body.kind,
            goal=body.goal,
            actor=actor,
            api_key=_key(request),
            audience=body.audience,
            tenant_id=str(settings.dms_tenant_id),
            space_id=body.space_id,
            grants=body.grants,
            requested_grants=body.grants,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None
