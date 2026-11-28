"""Skills and documents library. Mounted only when ``DMS_SKILLS`` is the digit 1.

Browse and search the curated catalog without a gate: those files ship in
the repo. Fetch, install, grant, upload, and the Google insight call are
mutations and call ``compliance_gate`` before any write. The provider runs
a granted skill. This route does not.
"""

from __future__ import annotations

from typing import Any, Literal

from cortex_client import compliance_gate
from dms_core.skills.catalog import preview_catalog, search_catalog
from dms_core.skills.context import provider_context
from dms_core.skills.errors import SkillError
from dms_core.skills.fetch import fetch_skill
from dms_core.skills.flag import enabled
from dms_core.skills.store import SkillLibrary
from dms_core.skills.web_insight import run as web_insight_run
from dms_core.skills.web_insight import status as web_insight_status
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/studio/skills", tags=["skills"])

_SURFACES = Literal["ask", "create", "agent"]


class FetchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=500)
    ref: str = Field(default="HEAD", max_length=128)


class InstallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fetch_id: str = ""
    catalog: str = ""


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(min_length=1, max_length=80)
    surfaces: list[_SURFACES] = Field(min_length=1, max_length=3)


class DocumentGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1, max_length=80)
    surfaces: list[_SURFACES] = Field(min_length=1, max_length=3)


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(min_length=1, max_length=80)


class InsightIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    space_id: str = ""


def _library(settings: Any) -> SkillLibrary:
    return SkillLibrary.open(str(settings.dms_tenant_id))


def _actor(settings: Any) -> str:
    return str(settings.dms_actor_user_id or "").strip()


def _steward(settings: Any) -> str:
    role = str(settings.dms_actor_role or "").strip().lower()
    actor = _actor(settings)
    if role not in {"steward", "admin"} or not actor:
        raise HTTPException(status_code=403, detail="steward_grant_required")
    return actor


def _admit(settings: Any, cortex: Any, action: str, resource: str) -> str:
    enforce(
        compliance_gate(
            action=action,
            actor=_actor(settings),
            resource=resource,
            metadata={"task_id": action},
            client=cortex,
        )
    )
    return _steward(settings)


def _http(exc: SkillError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.code)


def _embedder(request: Request) -> Any:
    override = getattr(request.app.state, "skills_embedder", None)
    if override is not None:
        return override if callable(override) else None
    from dms_api.wiring import skills_embedder

    return skills_embedder(getattr(request.app.state, "cortex", None))


@router.get("/status")
def skills_status() -> dict[str, Any]:
    return {"enabled": enabled(), "flag": "DMS_SKILLS"}


@router.get("/catalog")
def skills_catalog(q: str = Query(default="", max_length=200)) -> dict[str, Any]:
    return {"skills": search_catalog(q)}


@router.get("/catalog/{name}")
def skills_catalog_preview(name: str) -> dict[str, Any]:
    try:
        return preview_catalog(name)
    except SkillError as exc:
        raise _http(exc) from exc


@router.get("/installed")
def skills_installed(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    _admit(settings, cortex, "skills.installed", "skills")
    return {"versions": _library(settings).versions()}


@router.get("/audit")
def skills_audit(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    _admit(settings, cortex, "skills.audit", "skills")
    return {"events": _library(settings).audit_log()}


@router.get("/context")
def skills_context(
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
    surface: _SURFACES = "ask",
    space_id: str = "",
    q: str = "",
) -> dict[str, Any]:
    _admit(settings, cortex, "skills.context", "skills")
    return provider_context(
        surface=surface,
        tenant_id=str(settings.dms_tenant_id),
        question=q,
        space_id=space_id or None,
        embedder=_embedder(request),
    )


@router.get("/google")
def skills_google() -> dict[str, Any]:
    return web_insight_status()


@router.get("/documents")
def skills_documents(
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
    space_id: str = "",
    q: str = "",
) -> dict[str, Any]:
    _admit(settings, cortex, "skills.documents", "skills")
    library = _library(settings)
    if q.strip():
        return {
            "citations": library.citations(
                q,
                space_id=space_id or None,
                embedder=_embedder(request),
            )
        }
    return {"documents": library.documents(space_id or None)}


@router.post("/fetch")
def skills_fetch(body: FetchIn, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="skills.fetch",
            actor=_actor(settings),
            resource="skills",
            metadata={"task_id": "skills.fetch"},
            client=cortex,
        )
    )
    actor = _steward(settings)
    library = _library(settings)
    try:
        bundle = fetch_skill(body.url, ref=body.ref)
        return library.stage_fetch(bundle, actor=actor)
    except SkillError as exc:
        library.record("fetch_rejected", actor, code=exc.code)
        raise _http(exc) from exc


@router.post("/install")
def skills_install(body: InstallIn, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="skills.install",
            actor=_actor(settings),
            resource="skills",
            metadata={"task_id": "skills.install"},
            client=cortex,
        )
    )
    actor = _steward(settings)
    library = _library(settings)
    try:
        if body.catalog:
            return library.install_catalog(body.catalog, actor=actor)
        if body.fetch_id:
            return library.install_fetch(body.fetch_id, actor=actor)
        raise SkillError("install_target")
    except SkillError as exc:
        raise _http(exc) from exc


@router.post("/grant")
def skills_grant(body: GrantIn, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="skills.grant",
            actor=_actor(settings),
            resource="skills",
            metadata={"task_id": "skills.grant"},
            client=cortex,
        )
    )
    actor = _steward(settings)
    try:
        return _library(settings).grant(body.version_id, list(body.surfaces), actor=actor)
    except SkillError as exc:
        raise _http(exc) from exc


@router.post("/revoke")
def skills_revoke(
    body: RevokeIn, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="skills.revoke",
            actor=_actor(settings),
            resource="skills",
            metadata={"task_id": "skills.revoke"},
            client=cortex,
        )
    )
    actor = _steward(settings)
    try:
        return _library(settings).revoke(body.version_id, actor=actor)
    except SkillError as exc:
        raise _http(exc) from exc


@router.post("/documents")
def skills_upload(
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
    file: UploadFile = File(...),
    space_id: str = Form(default=""),
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="skills.document_upload",
            actor=_actor(settings),
            resource="skills",
            metadata={"task_id": "skills.document_upload"},
            client=cortex,
        )
    )
    actor = _steward(settings)
    data = file.file.read()
    if not isinstance(data, bytes):
        data = bytes(data)
    try:
        return _library(settings).add_document(
            filename=file.filename or "upload.md",
            data=data,
            space_id=space_id,
            actor=actor,
            embedder=_embedder(request),
        )
    except SkillError as exc:
        raise _http(exc) from exc


@router.post("/documents/grant")
def skills_document_grant(
    body: DocumentGrantIn, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="skills.document_grant",
            actor=_actor(settings),
            resource="skills",
            metadata={"task_id": "skills.document_grant"},
            client=cortex,
        )
    )
    actor = _steward(settings)
    try:
        return _library(settings).grant_document(body.doc_id, list(body.surfaces), actor=actor)
    except SkillError as exc:
        raise _http(exc) from exc


@router.post("/google/insights")
def skills_google_insights(
    body: InsightIn,
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    """Cite a Google AI Overview. The fetcher sees the question only."""
    enforce(
        compliance_gate(
            action="skills.google_insights",
            actor=_actor(settings),
            resource="skills",
            metadata={"task_id": "skills.google_insights"},
            client=cortex,
        )
    )
    actor = _steward(settings)
    slot = web_insight_status()
    library = _library(settings)
    if not slot["enabled"]:
        library.record("google_disabled", actor, code="web_insight_disabled")
        return {"enabled": False, "message": slot["message"], "cited": False, "overview": ""}
    transport = getattr(request.app.state, "skills_web_transport", None)
    try:
        cited = web_insight_run(
            body.question,
            transport=transport if callable(transport) else None,
            tenant=str(settings.dms_tenant_id),
        )
    except SkillError as exc:
        library.record("google_overview", actor, code=exc.code)
        raise _http(exc) from exc
    library.record(
        "google_overview",
        actor,
        code="cited" if cited.get("cited") else "fallback",
        skill="google-ai-insights",
    )
    return {"enabled": True, **cited}
