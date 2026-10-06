"""CHAT-01 plan preview. Ontology HTTP only. SQL stays on POST /v1/chat/ask.

No model pick. This route does not generate and does not post an insights ask.
"""

from __future__ import annotations

import logging
from typing import Any

from cortex_client import compliance_gate
from cortex_client.insights import InsightsError, honest_envelope, redact_secrets
from dms_core.studio_chat_turn import (
    confirm_allowed,
    project_studio_turn,
    question_to_execute,
)
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep
from dms_api.gatekeeping import enforce

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/studio", tags=["studio"])


class PlanBody(BaseModel):
    question: str = Field(min_length=1)


class ConfirmBody(BaseModel):
    question: str = Field(min_length=1)
    clarify_reply: str = ""
    preview: dict[str, Any]


def _gate(cortex: Any, action: str, question: str) -> None:
    decision = compliance_gate(
        action=action,
        metadata={"task_id": action, "question": question},
        client=cortex,
    )
    # Read of a ranking, not a lake write. The ask route gates the SQL.
    enforce(decision, mutation=False)


def _closed(message: str, *, status_code: int = 503) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "sql_ran": False,
            "code": "cortex_unavailable",
            "message": redact_secrets(message)[:400],
        },
        status_code=status_code,
    )


@router.post("/chat/plan")
def studio_chat_plan(body: PlanBody, cortex: CortexDep) -> Any:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if cortex is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "cortex_unavailable", "message": "Cortex client not configured"},
        )
    _gate(cortex, "studio.chat_plan", question)
    try:
        raw = cortex.insights_ontology(question)
    except InsightsError as exc:
        logger.warning("studio chat plan failed (%s)", exc.__class__.__name__)
        return _closed(str(exc), status_code=exc.status_code)
    payload = honest_envelope(raw) if isinstance(raw, dict) else {}
    turn = project_studio_turn(payload)
    turn["question"] = question
    return turn


@router.post("/chat/confirm")
def studio_chat_confirm(body: ConfirmBody, cortex: CortexDep) -> dict[str, Any]:
    """Authorize a later ask. This handler does not run SQL."""
    _gate(cortex, "studio.chat_confirm", body.question.strip())
    if not confirm_allowed(body.preview, body.clarify_reply):
        raise HTTPException(status_code=409, detail="clarify unanswered")
    blocks = bool((body.preview.get("clarify") or {}).get("blocks"))
    return {
        "execute": True,
        "sql_ran": False,
        "question": question_to_execute(
            body.question, body.clarify_reply, blocks=blocks
        ),
    }
