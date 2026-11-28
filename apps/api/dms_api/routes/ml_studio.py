"""Modelling studio. Mounted only when DMS_ML_STUDIO is the digit 1.

POST /v1/studio/ml/runs starts a background fit.
GET /v1/studio/ml/runs/{run_id} returns the saved card.

The provider plans the SQL and writes the paragraph. This route does not
import the executor. Wiring does.
"""

from __future__ import annotations

import os

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import ml_read, ml_start

FLAG = "DMS_ML_STUDIO"

router = APIRouter(prefix="/v1/studio/ml", tags=["studio"])


def enabled() -> bool:
    """True only when the value is the digit 1. Unset, true, and 01 stay off."""
    return os.environ.get(FLAG) == "1"


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)


@router.post("/runs", status_code=202)
def start_run(body: RunIn, cortex: CortexDep, settings: SettingsDep) -> dict[str, str]:
    if not enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    decision = compliance_gate(
        action="studio.ml_run",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.ml_run"},
        client=cortex,
    )
    enforce(decision)
    try:
        doc = ml_start(
            question=body.question,
            actor=str(settings.dms_actor_user_id or ""),
            event_id=getattr(decision, "event_id", None),
        )
    except PermissionError:
        raise HTTPException(status_code=404, detail="Not Found") from None
    return {"run_id": str(doc["run_id"]), "status": str(doc["status"])}


@router.get("/runs/{run_id}")
def read_run(run_id: str, cortex: CortexDep, settings: SettingsDep) -> dict[str, object]:
    if not enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    decision = compliance_gate(
        action="studio.ml_read",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.ml_read"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    doc = ml_read(run_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return doc
