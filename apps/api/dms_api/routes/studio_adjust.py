"""Steward Adjust SQL (ADJUST-SQL-VQ-01).

``DMS_STUDIO_ADJUST`` defaults to off. While it is not the digit 1, ``app.py``
does not mount this router, so every path here is a 404 and the OpenAPI
document is main's.

Run and save both call the same validate, grant, and read-only checks as
model SQL (``validate_compiled_sql`` then ``run_readonly``), then the answer
payload mask. Save stores provenance ``steward_adjusted`` with the actor and
a schema hash. A non-steward is 403. Nothing here inspects question text.
"""

from __future__ import annotations

import os
from typing import Any

from cortex_client import compliance_gate
from dms_core.control_plane.onto_data import ontology_data_store
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import preview_adjusted_sql, save_steward_adjusted

FLAG = "DMS_STUDIO_ADJUST"

router = APIRouter(prefix="/v1/studio/adjust", tags=["studio"])


def enabled() -> bool:
    """``DMS_STUDIO_ADJUST=1`` exactly. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


class AdjustRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    sql: str = Field(min_length=1, max_length=8000)


class AdjustSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=8000)


def _steward(settings: Any) -> str:
    actor = "" if settings.dms_actor_user_id is None else str(settings.dms_actor_user_id).strip()
    if not ontology_data_store().holds_steward_grant(role=settings.dms_actor_role, actor_id=actor):
        raise HTTPException(status_code=403, detail="steward_grant_required")
    return actor


def _http(exc: BaseException) -> HTTPException:
    """Checker codes only. ``AdjustDisabled`` is a missing route, not a 400."""
    if type(exc).__name__ == "AdjustDisabled":
        return HTTPException(status_code=404, detail="Not Found")
    if isinstance(exc, ValueError) and exc.args and isinstance(exc.args[0], str):
        return HTTPException(status_code=400, detail=exc.args[0])
    raise exc


@router.get("/status")
def adjust_status(cortex: CortexDep, settings: SettingsDep) -> dict[str, bool]:
    _steward(settings)
    decision = compliance_gate(
        action="studio.adjust_status",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.adjust_status"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return {"enabled": True}


@router.post("/run")
def adjust_run(
    body: AdjustRunIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Preview rows. Refusals use the model SQL checker code."""
    actor = _steward(settings)
    decision = compliance_gate(
        action="studio.adjust_run",
        actor=actor,
        metadata={"task_id": "studio.adjust_run", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    try:
        return preview_adjusted_sql(space_id=body.space_id, sql=body.sql)
    except Exception as exc:  # noqa: BLE001 - checker code or AdjustDisabled, else re-raised
        raise _http(exc) from None


@router.post("/save")
def adjust_save(
    body: AdjustSaveIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Store the SQL the gates just ran, with actor and schema hash."""
    actor = _steward(settings)
    decision = compliance_gate(
        action="studio.adjust_save",
        actor=actor,
        metadata={"task_id": "studio.adjust_save", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return save_steward_adjusted(
            space_id=body.space_id,
            question=body.question,
            sql=body.sql,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 - checker code or AdjustDisabled, else re-raised
        raise _http(exc) from None
