"""Create doc and Create slides. Flag ``DMS_CREATE_DOCS``, default off.

The provider writes the file. This route plans, then asks the shared Create
adapter, then returns the judge, the versions, and the PDF sha256. It does
not render and it does not call a model itself.

Status stays mounted while the flag is off so the UI can hide the hook.
Plan, generate, edit, and file are mutations: compliance_gate, then
``gatekeeping.enforce`` (fail closed, 403).
"""

from __future__ import annotations

import threading
from typing import Any

from cortex_client import compliance_gate
from create_docs.adapter import AdapterMissing
from create_docs.flag import enabled
from create_docs.harness import apply_style, edit, file_bytes, new_run, produce, public_view
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/create-docs", tags=["create-docs"])
status_router = APIRouter(prefix="/v1/create-docs", tags=["create-docs"])

# ponytail: process-local, 32 runs, gone on restart. Two requests on one run
# can interleave. The shared governance store replaces this when it lands.
_RUNS: dict[str, dict[str, Any]] = {}
_ORDER: list[str] = []
_LOCK = threading.Lock()
_CAP = 32


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = ""
    text: str = ""
    sql: str = ""
    sources: list[str] = Field(default_factory=list)
    as_of: str = ""
    space_id: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)


class FewShotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    text: str = ""


class BrandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    primary: str = ""
    font: str = ""
    notes: str = ""


class StyleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = "pptx"
    skill_id: str = ""
    prompt_version: str = "draft"
    prompt_text: str = ""
    super_guide: str = ""
    template_id: str = ""
    brand: BrandIn = Field(default_factory=BrandIn)
    few_shots: list[FewShotIn] = Field(default_factory=list)
    route: str = "openvault"


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[AnswerIn] = Field(min_length=1, max_length=12)
    style: StyleIn = Field(default_factory=StyleIn)
    max_steps: int = Field(default=8, ge=1, le=8)
    max_usd: float | None = None


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: StyleIn | None = None
    max_steps: int | None = Field(default=None, ge=1, le=8)
    max_usd: float | None = None


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    instruction: str = Field(min_length=1, max_length=400)


def _gate(cortex: CortexDep, action: str, run_id: str | None) -> None:
    decision = compliance_gate(
        action=action,
        resource=run_id,
        metadata={"task_id": action, "run_id": run_id or ""},
        client=cortex,
    )
    enforce(decision)


def _save(run: dict[str, Any]) -> None:
    with _LOCK:
        run_id = str(run["run_id"])
        if run_id not in _RUNS:
            _ORDER.append(run_id)
        _RUNS[run_id] = run
        while len(_ORDER) > _CAP:
            _RUNS.pop(_ORDER.pop(0), None)


def _load(run_id: str) -> dict[str, Any]:
    with _LOCK:
        run = _RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return run


def _answers(body: CreateIn) -> list[dict[str, Any]]:
    return [item.model_dump() for item in body.answers]


@status_router.get("/status")
def create_docs_status() -> dict[str, bool]:
    return {"enabled": enabled()}


@router.post("/runs")
def create_docs_run(body: CreateIn, cortex: CortexDep) -> dict[str, Any]:
    _gate(cortex, "create_docs.plan", None)
    try:
        run = new_run(
            _answers(body),
            body.style.model_dump(),
            max_steps=body.max_steps,
            max_usd=body.max_usd,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save(run)
    return public_view(run)


@router.post("/runs/{run_id}/generate")
def create_docs_generate(run_id: str, body: GenerateIn, cortex: CortexDep) -> dict[str, Any]:
    _gate(cortex, "create_docs.generate", run_id)
    run = _load(run_id)
    try:
        if body.style is not None:
            apply_style(run, body.style.model_dump())
        if body.max_steps is not None:
            run["budget"]["max_steps"] = body.max_steps
        if body.max_usd is not None:
            run["budget"]["max_usd"] = body.max_usd
        view = produce(run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AdapterMissing:
        raise HTTPException(status_code=503, detail="adapter_missing") from None
    _save(run)
    return view


@router.post("/runs/{run_id}/edit")
def create_docs_edit(run_id: str, body: EditIn, cortex: CortexDep) -> dict[str, Any]:
    _gate(cortex, "create_docs.edit", run_id)
    run = _load(run_id)
    try:
        view = edit(run, body.target, body.instruction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AdapterMissing:
        raise HTTPException(status_code=503, detail="adapter_missing") from None
    _save(run)
    return view


@router.post("/runs/{run_id}/file")
def create_docs_file(
    run_id: str,
    cortex: CortexDep,
    which: str = Query(...),
    version: str | None = Query(None),
) -> Response:
    _gate(cortex, "create_docs.file", run_id)
    if which not in {"artifact", "pdf"}:
        raise HTTPException(status_code=400, detail="which")
    run = _load(run_id)
    try:
        data, media, name = file_bytes(run, which=which, version_id=version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
