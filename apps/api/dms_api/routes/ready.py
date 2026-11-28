"""READY-04 routes. Mounted only when a DMS_READY_* flag is the digit 1.

Flag off, ``create_app`` does not include this router, so the paths are 404
and the OpenAPI document is unchanged.
"""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from dms_core.control_plane.onto_data import ontology_data_store
from dms_core.control_plane.ready_flow import (
    ReadyError,
    any_route_enabled,
    call_questions,
    confirm_grants,
    confirm_joins,
    connect_profiled,
    current_profile,
    demo_directory,
    demo_enabled,
    grant_enabled,
    guide_body,
    guide_enabled,
    joins_enabled,
    mark_company,
    mark_demo,
    parse_questions,
    provision_postgres,
    questions_request,
    record_answer,
    store_questions,
    tenant_enabled,
    write_demo_sqlite,
)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep, SpaceStoreDep, StoreBindingDep
from dms_api.gatekeeping import enforce
from dms_api.settings import Settings

router = APIRouter(prefix="/v1/ready", tags=["ready"])


def mount_enabled() -> bool:
    return any_route_enabled()


class TenantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: list[str] = Field(min_length=1, max_length=64)


class JoinConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(default_factory=list, max_length=64)


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1, max_length=63)


def _http(exc: ReadyError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.code)


def _actor(settings: Settings) -> str:
    role = (settings.dms_actor_role or "").strip().lower()
    actor = "" if settings.dms_actor_user_id is None else str(settings.dms_actor_user_id).strip()
    if role == "admin" and actor:
        return actor
    if role == "steward" and actor and ontology_data_store().holds_steward_grant(
        role="steward", actor_id=actor
    ):
        return actor
    raise HTTPException(status_code=403, detail="steward_grant_required")


def _company(
    *,
    name: str,
    settings: Settings,
    store: Any,
    persistent: bool,
) -> dict[str, Any]:
    if not tenant_enabled():
        raise ReadyError("ready_tenant_disabled", 404)
    if persistent and settings.database_url:
        return provision_postgres(settings.database_url, settings.dms_tenant_id, name)
    try:
        record = store.create(name.strip())
    except ValueError as exc:
        code = str(exc)
        if code != "space_name_taken":
            code = "bad_request"
        raise ReadyError(code, 409) from None
    mark_company()
    return {
        "tenant_id": settings.dms_tenant_id,
        "space_id": record.id,
        "name": record.name,
        "persisted": False,
        "created_user": False,
    }


@router.post("/tenants", status_code=201)
def create_tenant(
    body: TenantIn,
    cortex: CortexDep,
    settings: SettingsDep,
    store: SpaceStoreDep,
    binding: StoreBindingDep,
) -> dict[str, Any]:
    """Create the company this process is bound to. Does not return 500."""
    if not tenant_enabled():
        raise HTTPException(status_code=404, detail="ready_tenant_disabled")
    decision = compliance_gate(
        action="ready.tenant.create",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "ready.tenant.create"},
        client=cortex,
    )
    enforce(decision)
    try:
        return _company(
            name=body.name,
            settings=settings,
            store=store,
            persistent=binding.persistent,
        )
    except ReadyError as exc:
        raise _http(exc) from None


@router.post("/demo", status_code=201)
def start_demo(
    cortex: CortexDep,
    settings: SettingsDep,
    store: SpaceStoreDep,
    binding: StoreBindingDep,
) -> dict[str, Any]:
    """Guided demo on a synthetic database. Grants nothing until confirm."""
    if not demo_enabled():
        raise HTTPException(status_code=404, detail="ready_demo_disabled")
    actor = _actor(settings)
    if not (tenant_enabled() and grant_enabled() and joins_enabled() and guide_enabled()):
        raise HTTPException(status_code=404, detail="ready_demo_disabled")
    decision = compliance_gate(
        action="ready.demo.start",
        actor=actor,
        metadata={"task_id": "ready.demo.start"},
        client=cortex,
    )
    enforce(decision)
    try:
        company = _company(
            name="Ready demo",
            settings=settings,
            store=store,
            persistent=binding.persistent,
        )
        path = write_demo_sqlite(demo_directory())
        source = connect_profiled(
            space_id=str(company["space_id"]),
            path=path,
            actor=actor,
            token_id="ready-demo",
        )
        mark_demo()
    except ReadyError as exc:
        raise _http(exc) from None
    return {"company": company, "source": source, "guide": guide_body()}


@router.post("/sources/{source_id}/grants")
def grant_source(
    source_id: str,
    body: GrantIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    if not grant_enabled():
        raise HTTPException(status_code=404, detail="ready_grant_disabled")
    actor = _actor(settings)
    decision = compliance_gate(
        action="ready.grant.confirm",
        actor=actor,
        metadata={"task_id": "ready.grant.confirm", "source_id": source_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return confirm_grants(source_id, body.tables, actor=actor)
    except ReadyError as exc:
        raise _http(exc) from None


@router.post("/joins/confirm")
def confirm_ready_joins(
    body: JoinConfirmIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    if not joins_enabled():
        raise HTTPException(status_code=404, detail="ready_joins_disabled")
    actor = _actor(settings)
    decision = compliance_gate(
        action="ready.joins.confirm",
        actor=actor,
        metadata={"task_id": "ready.joins.confirm"},
        client=cortex,
    )
    enforce(decision)
    try:
        return confirm_joins(body.ids)
    except ReadyError as exc:
        raise _http(exc) from None


@router.get("/guide")
def get_guide(cortex: CortexDep, settings: SettingsDep) -> dict[str, Any]:
    if not guide_enabled():
        raise HTTPException(status_code=404, detail="ready_guide_disabled")
    decision = compliance_gate(
        action="ready.guide.read",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "ready.guide.read"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    try:
        return guide_body()
    except ReadyError as exc:
        raise _http(exc) from None


@router.post("/questions")
def ready_questions(cortex: CortexDep, settings: SettingsDep) -> dict[str, Any]:
    """Ask OpenVault role ``questions`` for five questions. No model id."""
    if not guide_enabled():
        raise HTTPException(status_code=404, detail="ready_guide_disabled")
    actor = _actor(settings)
    decision = compliance_gate(
        action="ready.questions",
        actor=actor,
        metadata={"task_id": "ready.questions"},
        client=cortex,
    )
    enforce(decision)
    profile = current_profile()
    if not profile:
        raise HTTPException(status_code=409, detail="source_not_connected")
    try:
        payload = call_questions(questions_request(profile))
        parsed = parse_questions(payload)
    except ReadyError as exc:
        store_questions([], exc.code)
        return {"questions": [], "questions_status": exc.code}
    if parsed is None:
        store_questions([], "model_output_invalid")
        return {"questions": [], "questions_status": "model_output_invalid"}
    store_questions(parsed, "ok")
    return {"questions": parsed, "questions_status": "ok"}


@router.post("/answer")
def ready_answer(
    body: AnswerIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    if not guide_enabled():
        raise HTTPException(status_code=404, detail="ready_guide_disabled")
    actor = _actor(settings)
    decision = compliance_gate(
        action="ready.answer",
        actor=actor,
        metadata={"task_id": "ready.answer"},
        client=cortex,
    )
    enforce(decision)
    try:
        return record_answer(body.table)
    except ReadyError as exc:
        raise _http(exc) from None
