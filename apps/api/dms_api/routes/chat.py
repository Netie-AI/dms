"""Scoped chat ask — product path = live Cortex; demo = fallback only."""

from __future__ import annotations

import logging
from typing import Any

from cortex_client import compliance_gate
from dms_core.ask import AskServiceError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dms_api.deps import AskServiceDep, CortexDep, SettingsDep, SpaceStoreDep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/chat", tags=["chat"])

_SOFT_GATE = frozenset({"gate_unavailable", "gate_task_unknown"})
_POLICY_CODES = frozenset(
    {
        "pool_mismatch",
        "pool_required",
        "pool_saturated",
        "session_unbound",
        "session_expired",
        "session_bind_failed",
        "manifest_signature_invalid",
        "path_not_allowed",
        "statement_not_allowed",
        "manifest_rejected",
        "manifest_unknown_issuer",
        "manifest_expired",
    }
)


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    space_id: str | None = None
    session_id: str | None = None


@router.post("/ask")
def chat_ask(
    body: AskBody,
    settings: SettingsDep,
    store: SpaceStoreDep,
    cortex: CortexDep,
    ask: AskServiceDep,
) -> dict[str, Any]:
    if body.space_id and store.get(body.space_id) is None:
        raise HTTPException(status_code=404, detail="space_not_found")

    decision = compliance_gate(
        action="chat.ask",
        metadata={"question": body.question, "space_id": body.space_id, "task_id": "chat.ask"},
        client=cortex,
    )

    want_demo = settings.dms_ask_mode == "demo"
    if want_demo:
        if not decision.allowed and decision.reason not in _SOFT_GATE:
            raise HTTPException(status_code=403, detail=decision.reason)
        env = ask.demo_ask(body.question, space_id=body.space_id)
        env["ask_mode"] = "demo"
        return env

    if not decision.allowed and decision.reason not in _SOFT_GATE:
        raise HTTPException(status_code=403, detail=decision.reason)

    if cortex is None:
        if settings.dms_demo_fallback:
            env = ask.demo_ask(body.question, space_id=body.space_id)
            env["ask_mode"] = "demo"
            env.setdefault("assumptions", []).append("fallback — Cortex client missing")
            return env
        raise HTTPException(
            status_code=503,
            detail={"code": "cortex_unavailable", "message": "Cortex client not configured"},
        )

    try:
        return ask.live_ask(
            body.question,
            space_id=body.space_id,
            session_id=body.session_id,
        )
    except AskServiceError as exc:
        # Never mask policy refusals with demo numbers (0 confidently wrong).
        if settings.dms_demo_fallback and exc.code not in _POLICY_CODES:
            logger.warning("live ask failed (%s); demo fallback", exc.code)
            env = ask.demo_ask(body.question, space_id=body.space_id)
            env["ask_mode"] = "demo"
            env.setdefault("assumptions", []).append(f"fallback after live error: {exc.code}")
            return env
        status = 409 if exc.code in {"session_unbound", "session_expired"} else 403
        if exc.code == "pool_saturated":
            status = 429
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": exc.detail or exc.code},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        if settings.dms_demo_fallback:
            logger.warning("live ask failed: %s; demo fallback", exc)
            env = ask.demo_ask(body.question, space_id=body.space_id)
            env["ask_mode"] = "demo"
            env.setdefault("assumptions", []).append("fallback — live ask failed")
            return env
        raise HTTPException(
            status_code=503,
            detail={"code": "live_ask_failed", "message": str(exc)[:400]},
        ) from exc
