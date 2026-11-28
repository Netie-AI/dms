"""Create harness (DMS-CREATE-01).

``DMS_CREATE`` defaults off. Status is always mounted and answers
``enabled: false`` while the flag is off. The other routes stay unmounted.

A run needs the customer OpenVault key and a DMS API key. OpenVault sees
the caller key and a role string. DMS does not name a model.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.mcp_server.auth import bearer_from_headers, lookup_bearer
from dms_api.wiring import (
    create_caller_key,
    create_confirm_final,
    create_events,
    create_generate,
    create_guide_read,
    create_guide_save,
    create_improve,
    create_read,
    create_restore,
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
    style: str = Field(default="", max_length=800)
    template: str = Field(default="plain", max_length=32)
    brand_colours: str = Field(default="", max_length=80)
    brand_fonts: str = Field(default="", max_length=80)
    brand_logo: str = Field(default="", max_length=200)
    brand_tone: str = Field(default="", max_length=80)
    inputs: list[str] = Field(default_factory=list)
    examples: list[dict[str, str]] | None = None
    tools: dict[str, bool] | None = None
    workbook: bool = False
    prompt_version: str = Field(default="1", max_length=8)


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[str] | None = None


class ImproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=400)


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: int = Field(ge=1)


class GuideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand: dict[str, str] | None = None
    guides: dict[str, str] | None = None
    tools: dict[str, bool] | None = None
    examples: list[dict[str, str]] | None = None
    templates: dict[str, str] | None = None


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
            actor="status",
            metadata={"task_id": "studio.create_status", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    del settings
    return {"enabled": True}


@router.get("/guide")
def get_guide(request: Request, cortex: CortexDep, settings: SettingsDep) -> dict[str, Any]:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_guide",
            actor=actor,
            metadata={"task_id": "studio.create_guide", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_guide_read(tenant_id=str(settings.dms_tenant_id))
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.put("/guide")
def put_guide(
    body: GuideIn, request: Request, cortex: CortexDep, settings: SettingsDep
) -> dict[str, Any]:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_guide",
            actor=actor,
            metadata={"task_id": "studio.create_guide", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_guide_save(tenant_id=str(settings.dms_tenant_id), guide=body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions")
def open_session(
    body: SessionIn, request: Request, cortex: CortexDep, settings: SettingsDep
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
            style=body.style,
            template=body.template,
            brand_colours=body.brand_colours,
            brand_fonts=body.brand_fonts,
            brand_logo=body.brand_logo,
            brand_tone=body.brand_tone,
            inputs=body.inputs,
            examples=body.examples,
            tools=body.tools,
            workbook=body.workbook,
            prompt_version=body.prompt_version,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/generate")
def generate_session(
    session_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    body: GenerateIn | None = None,
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
        return create_generate(
            session_id=session_id,
            api_key=_key(request),
            inputs=None if body is None else body.inputs,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/run")
def run_session(
    session_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
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


@router.post("/sessions/{session_id}/edit")
def edit_session(
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
            section_id=body.section_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/restore")
def restore_session(
    session_id: str,
    body: RestoreIn,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(request, settings)
    _key(request)
    enforce(
        compliance_gate(
            action="studio.create_restore",
            actor=actor,
            metadata={"task_id": "studio.create_restore", "space_id": ""},
            client=cortex,
        ),
        mutation=False,
    )
    try:
        return create_restore(session_id=session_id, version_id=body.version_id, actor=actor)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc) from None


@router.post("/sessions/{session_id}/confirm")
def confirm_file(
    session_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
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


@router.get("/sessions/{session_id}")
def read_session(
    session_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
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
