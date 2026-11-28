"""Create doc and Create slides. Flag ``DMS_CREATE_DOCS``, default off.

Generation, the tool registry, the prompt library, the brand fields, and the
judge are ``dms_core.create_generator``. This route only plans the granted
answers and calls that module. Status stays mounted while the flag is off.
Plan, generate, and edit are mutations: compliance_gate, then enforce.
"""

from __future__ import annotations

import threading
from typing import Any

from cortex_client import compliance_gate
from create_docs.flag import enabled
from create_docs.harness import apply_style, edit, new_run, produce, public_view
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import create_caller_key

router = APIRouter(prefix="/v1/create-docs", tags=["create-docs"])
status_router = APIRouter(prefix="/v1/create-docs", tags=["create-docs"])

# ponytail: process-local, 32 runs, gone on restart. Their create store is the
# durable session. This dict only holds a doc or slides run started from answers.
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

    colours: str = ""
    fonts: str = ""
    logo: str = ""
    tone: str = ""


class StyleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "slides"
    prompt_version: str = "1"
    prompt_text: str = ""
    guide: str = ""
    template: str = "plain"
    brand: BrandIn = Field(default_factory=BrandIn)
    few_shots: list[FewShotIn] = Field(default_factory=list)
    tools: dict[str, bool] | None = None
    workbook: bool = False


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[AnswerIn] = Field(min_length=1, max_length=12)
    style: StyleIn = Field(default_factory=StyleIn)


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: StyleIn | None = None


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


def _key(request: Request) -> str:
    raw = create_caller_key(request.headers.get("x-openvault-key"))
    if not raw.startswith("ov_") or " " in raw or len(raw) <= 3:
        raise HTTPException(status_code=401, detail="ov_api_key_required")
    return raw


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


def _style(body: StyleIn) -> dict[str, Any]:
    return body.model_dump()


@status_router.get("/status")
def create_docs_status() -> dict[str, bool]:
    return {"enabled": enabled()}


@router.post("/runs")
def create_docs_run(body: CreateIn, cortex: CortexDep) -> dict[str, Any]:
    _gate(cortex, "create_docs.plan", None)
    try:
        run = new_run([item.model_dump() for item in body.answers], _style(body.style))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save(run)
    return public_view(run)


@router.post("/runs/{run_id}/generate")
def create_docs_generate(
    run_id: str, body: GenerateIn, request: Request, cortex: CortexDep
) -> dict[str, Any]:
    _gate(cortex, "create_docs.generate", run_id)
    key = _key(request)
    run = _load(run_id)
    try:
        if body.style is not None:
            apply_style(run, _style(body.style))
        view = produce(run, api_key=key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save(run)
    return view


@router.post("/runs/{run_id}/edit")
def create_docs_edit(
    run_id: str, body: EditIn, request: Request, cortex: CortexDep
) -> dict[str, Any]:
    _gate(cortex, "create_docs.edit", run_id)
    key = _key(request)
    run = _load(run_id)
    try:
        view = edit(run, body.target, body.instruction, api_key=key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save(run)
    return view
