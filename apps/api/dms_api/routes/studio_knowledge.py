"""Steward knowledge notes (BIRD-EVIDENCE-01).

``DMS_SPACE_KNOWLEDGE`` defaults to off. While it is not the digit 1,
``app.py`` does not mount this router, so every path here is a 404.

A note is text plus an optional SQL fragment. Propose, then approve.
Revise writes the next version. The ask path reads approved rows only.
Nothing here inspects question text.
"""

from __future__ import annotations

import os
from typing import Any

from cortex_client import compliance_gate
from dms_core.control_plane.onto_data import ontology_data_store
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import (
    approve_space_knowledge,
    list_space_knowledge,
    propose_space_knowledge,
    revise_space_knowledge,
)

FLAG = "DMS_SPACE_KNOWLEDGE"

router = APIRouter(prefix="/v1/studio/knowledge", tags=["studio"])


def enabled() -> bool:
    """``DMS_SPACE_KNOWLEDGE=1`` exactly. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


class KnowledgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=4000)
    sql_fragment: str | None = Field(default=None, max_length=2000)


class KnowledgeReviseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000)
    sql_fragment: str | None = Field(default=None, max_length=2000)


def _steward(settings: Any) -> str:
    actor = "" if settings.dms_actor_user_id is None else str(settings.dms_actor_user_id).strip()
    if not ontology_data_store().holds_steward_grant(role=settings.dms_actor_role, actor_id=actor):
        raise HTTPException(status_code=403, detail="steward_grant_required")
    return actor


def _http(exc: BaseException) -> HTTPException:
    if type(exc).__name__ == "KnowledgeDisabled":
        return HTTPException(status_code=404, detail="Not Found")
    if isinstance(exc, ValueError) and exc.args and isinstance(exc.args[0], str):
        return HTTPException(status_code=400, detail=exc.args[0])
    raise exc


@router.post("")
def propose_knowledge(
    body: KnowledgeIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    decision = compliance_gate(
        action="studio.knowledge_propose",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.knowledge_propose", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return propose_space_knowledge(
            space_id=body.space_id,
            body=body.body,
            sql_fragment=body.sql_fragment,
            actor=actor,
        )
    except (ValueError, RuntimeError) as exc:
        raise _http(exc) from exc


@router.post("/{note_id}/approve")
def approve_knowledge(
    note_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    decision = compliance_gate(
        action="studio.knowledge_approve",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.knowledge_approve", "note_id": note_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return approve_space_knowledge(note_id=note_id, actor=actor)
    except (ValueError, RuntimeError) as exc:
        raise _http(exc) from exc


@router.post("/{note_id}/revise")
def revise_knowledge(
    note_id: str,
    body: KnowledgeReviseIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    decision = compliance_gate(
        action="studio.knowledge_revise",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.knowledge_revise", "note_id": note_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return revise_space_knowledge(
            note_id=note_id,
            body=body.body,
            sql_fragment=body.sql_fragment,
            actor=actor,
        )
    except (ValueError, RuntimeError) as exc:
        raise _http(exc) from exc


@router.get("")
def list_knowledge(
    cortex: CortexDep,
    settings: SettingsDep,
    space_id: str = Query(..., min_length=1, max_length=128),
) -> list[dict[str, Any]]:
    _steward(settings)
    decision = compliance_gate(
        action="studio.knowledge_list",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.knowledge_list", "space_id": space_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    try:
        return list_space_knowledge(space_id=space_id)
    except (ValueError, RuntimeError) as exc:
        raise _http(exc) from exc
