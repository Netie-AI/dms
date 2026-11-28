"""Create app, doc, or slides (DMS-CREATE-01).

``DMS_CREATE`` defaults off. Status is always mounted and answers
``enabled: false`` while the flag is off. The other routes stay unmounted.

The call is the existing compliance call-through. ``mutation=False`` so a
missing task catalog does not invent a new refusal. A run needs the customer
OpenVault key and a DMS API key. OpenVault sees the caller key and a role
string. DMS does not name a model or a provider, and it does not fall back
to an install key.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.mcp_server.auth import bearer_from_headers, lookup_bearer
from dms_api.wiring import (
    create_accept,
    create_caller_key,
    create_confirm,
    create_confirm_final,
    create_events,
    create_export,
    create_generate,
    create_improve,
    create_plan,
    create_query,
    create_read,
    create_start,
)

FLAG = "DMS_CREATE"

router = APIRouter(prefix="/v1/studio/create", tags=["studio"])
status_router = APIRouter(prefix="/v1/studio/create", tags=["studio"])


def enabled() -> bool:
    """``DMS_CREATE=1`` exactly. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=16)
    goal: str = Field(min_length=1, max_length=400)
    audience: str = Field(min_length=1, max_length=200)


class IntentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str | None = Field(default=None, max_length=400)


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tweak: str | None = Field(default=None, max_length=400)


class ImproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=400)


class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: dict[str, str] = Field(default_factory=dict)


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


def _frame(event: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(event, default=str) + "\n\n").encode()


def _http(exc: BaseException) -> HTTPException:
    name = type(exc).__name__
    if name == "CreateDisabled":
        return HTTPException(status_code=404, detail="Not Found")
    if name == "CreateProblem" and exc.args and isinstance(exc.args[0], str):
        return HTTPException(status_code=400, detail=exc.args[0])
    raise exc


@status_router.get("/status")
def create_status(cortex: CortexDep, settings: SettingsDep) -> dict[str, bool]:
    """Flag off is ``enabled: false``. The other routes stay unmounted."""
    if not enabled():
        return {"enabled": False}
    enforce(
        compliance_gate(
            action="studio.create_status",
            actor=_actor_settings(settings),
            metadata={"task_id": "studio.create_status", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    return {"enabled": True}


def _actor_settings(settings: Any) -> str:
    if settings.dms_actor_user_id is None:
        return ""
    return str(settings.dms_actor_user_id)


@router.post("/sessions")
def open_session(
    body: SessionIn,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_session",
            actor=actor,
            metadata={"task_id": "studio.create_session", "space_id": body.space_id},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_start(
            space_id=body.space_id,
            kind=body.kind,
            goal=body.goal,
            audience=body.audience,
            api_key=_key(request),
            actor=actor,
            tenant_id=str(settings.dms_tenant_id),
        )
    except Exception as exc:  # noqa: BLE001 - typed create error, else re-raised
        raise _http(exc) from None


@router.post("/sessions/{session_id}/intent")
def confirm_session(
    session_id: str,
    body: IntentIn,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_intent",
            actor=actor,
            metadata={"task_id": "studio.create_intent", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_confirm(
            session_id=session_id,
            api_key=_key(request),
            answer=body.answer,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/plan")
def plan_session(
    session_id: str,
    body: PlanIn,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_plan",
            actor=actor,
            metadata={"task_id": "studio.create_plan", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_plan(session_id=session_id, api_key=_key(request), tweak=body.tweak)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/generate")
def generate_session(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_generate",
            actor=actor,
            metadata={"task_id": "studio.create_generate", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_generate(session_id=session_id, api_key=_key(request))
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/run")
def run_session(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> StreamingResponse:
    actor = _actor(request, settings)
    key = _key(request)
    enforce(
        compliance_gate(
            action="studio.create_generate",
            actor=actor,
            metadata={"task_id": "studio.create_generate", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )

    def frames():
        try:
            for event in create_events(session_id=session_id, api_key=key):
                yield _frame(event)
        except Exception as exc:  # noqa: BLE001
            http = _http(exc)
            detail = http.detail if isinstance(http.detail, str) else "create_failed"
            yield _frame({"step": "error", "label": detail, "status": "fail"})

    return StreamingResponse(frames(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/confirm")
def confirm_file(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_confirm",
            actor=actor,
            metadata={"task_id": "studio.create_confirm", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_confirm_final(session_id=session_id, actor=actor or "api_viewer")
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/improve")
def improve_session(
    session_id: str,
    body: ImproveIn,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    enforce(
        compliance_gate(
            action="studio.create_improve",
            actor=actor,
            metadata={"task_id": "studio.create_improve", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_improve(
            session_id=session_id,
            api_key=_key(request),
            instruction=body.instruction,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/accept")
def accept_session(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_accept",
            actor=actor,
            metadata={"task_id": "studio.create_accept", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_accept(session_id=session_id, actor=actor or "api_viewer")
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.get("/artifacts/{session_id}")
def read_session(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_read",
            actor=actor,
            metadata={"task_id": "studio.create_read", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_read(session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/artifacts/{session_id}/query")
def query_session(
    session_id: str,
    body: QueryIn,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_query",
            actor=actor,
            metadata={"task_id": "studio.create_query", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_query(session_id=session_id, filters=body.filters)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/artifacts/{session_id}/refresh")
def refresh_session(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_refresh",
            actor=actor,
            metadata={"task_id": "studio.create_refresh", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_read(session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.get("/artifacts/{session_id}/pptx")
def export_pptx(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> Response:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_export",
            actor=actor,
            metadata={"task_id": "studio.create_export", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        data, media = create_export(session_id=session_id, kind="pptx")
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None
    return Response(content=data, media_type=media)


@router.get("/artifacts/{session_id}/pdf")
def export_pdf(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> Response:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_export",
            actor=actor,
            metadata={"task_id": "studio.create_export", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        data, media = create_export(session_id=session_id, kind="pdf")
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None
    return Response(content=data, media_type=media)
