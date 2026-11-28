"""CHAT-01 plan preview. Ontology HTTP only. SQL stays on POST /v1/chat/ask.

No model pick. This route does not generate and does not post an insights ask.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from cortex_client import compliance_gate
from cortex_client.insights import InsightsError, honest_envelope, redact_secrets
from dms_core.ask_stream import public_event, stream_enabled, valid_ask_id, wait_events
from dms_core.studio_chat_turn import (
    confirm_allowed,
    project_studio_turn,
    question_to_execute,
)
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
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
    # Read of a ranking, not a lake write. The ask route gates the SQL.
    decision = compliance_gate(
        action="studio.chat_plan",
        metadata={"task_id": "studio.chat_plan", "question": question},
        client=cortex,
    )
    enforce(decision, mutation=False)
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
    decision = compliance_gate(
        action="studio.chat_confirm",
        metadata={"task_id": "studio.chat_confirm", "question": body.question.strip()},
        client=cortex,
    )
    enforce(decision, mutation=False)
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


def _frame(event: dict[str, Any]) -> bytes:
    """One SSE data line. Stage, time, status, and a catalog table when one was read."""
    body = json.dumps(public_event(event), separators=(",", ":"), sort_keys=True)
    return f"data: {body}\n\n".encode()


async def _event_frames(ask_id: str) -> AsyncIterator[bytes]:
    # Do not call Request.is_disconnected here. It waits on the receive
    # channel and holds the first events until the client goes away.
    yield b"retry: 86400000\n\n"
    index = 0
    while True:
        batch, done = await asyncio.to_thread(wait_events, ask_id, index, 0.5)
        for event in batch:
            index += 1
            if not isinstance(event, dict):
                continue
            stage = event.get("stage")
            status = event.get("status")
            t_ms = event.get("t_ms")
            if (
                not isinstance(stage, str)
                or not isinstance(status, str)
                or not isinstance(t_ms, int)
            ):
                continue
            yield _frame({"stage": stage, "t_ms": t_ms, "status": status})
        if done:
            return


async def _disabled_frames() -> AsyncIterator[bytes]:
    """Flag off. One end event, no stages, and the ask body is not read."""
    yield b"retry: 86400000\n\n"
    yield b'data: {"enabled":false}\n\n'


def events_router() -> APIRouter:
    """Always mounted. ``DMS_ASK_STREAM`` off answers ``enabled: false`` and stores nothing."""
    out = APIRouter(prefix="/v1/studio", tags=["studio"])

    @out.get("/ask/{ask_id}/events")
    async def studio_ask_events(ask_id: str, cortex: CortexDep) -> StreamingResponse:
        if not valid_ask_id(ask_id):
            raise HTTPException(status_code=404, detail="ask_not_found")
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        if not stream_enabled():
            return StreamingResponse(
                _disabled_frames(),
                media_type="text/event-stream",
                headers=headers,
            )
        decision = compliance_gate(
            action="studio.ask_events",
            metadata={"task_id": "studio.ask_events"},
            client=cortex,
        )
        enforce(decision, mutation=False)
        return StreamingResponse(
            _event_frames(ask_id),
            media_type="text/event-stream",
            headers=headers,
        )

    return out
