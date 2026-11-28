"""Yes and Fix for learned memory. ``DMS_LEARNED_MEMORY`` is off unless it is 1.

Off, this router is not mounted. On, Yes stores SQL only for the asker's own
governed answer. Fix re-runs the edited SQL. Neither route serves a stored row.
"""

from __future__ import annotations

import os
from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import confirm_learned, fix_learned

FLAG = "DMS_LEARNED_MEMORY"

router = APIRouter(prefix="/v1/chat/learned", tags=["learned-memory"])


def enabled() -> bool:
    """True only when ``DMS_LEARNED_MEMORY`` is the digit 1."""
    return os.environ.get(FLAG, "").strip() == "1"


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    space_id: str | None = None


class FixIn(ConfirmIn):
    sql: str = Field(min_length=1)
    interpretation: str = ""


def _actor(settings: Any) -> str:
    actor = "" if settings.dms_actor_user_id is None else str(settings.dms_actor_user_id).strip()
    if not actor:
        raise HTTPException(status_code=403, detail="asker_required")
    return actor


def _gate(cortex: Any, actor: str, action: str) -> None:
    decision = compliance_gate(
        action=action,
        actor=actor,
        metadata={"task_id": action},
        client=cortex,
    )
    enforce(decision)


@router.post("/confirm")
def confirm_answer(
    body: ConfirmIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Yes. Binds to the exact question, the intent, and this actor."""
    actor = _actor(settings)
    _gate(cortex, actor, "chat.learned_confirm")
    return confirm_learned(
        answer_id=body.answer_id,
        question=body.question,
        intent=body.intent,
        confirmer=actor,
    )


@router.post("/fix")
def fix_answer(
    body: FixIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Fix. The interpretation text is not parsed into SQL."""
    actor = _actor(settings)
    _gate(cortex, actor, "chat.learned_fix")
    return fix_learned(
        answer_id=body.answer_id,
        question=body.question,
        intent=body.intent,
        confirmer=actor,
        sql=body.sql,
        space_id=body.space_id,
        interpretation=body.interpretation,
    )
