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


class DrillthroughBody(BaseModel):
    token: str = Field(min_length=1)


def _stamp_demo_fallback(env: dict[str, Any], note: str) -> dict[str, Any]:
    """E6 — demo_fallback_used must set an unmissable banner flag."""
    from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

    env = build_answer_envelope(
        answer_id=str(env.get("answer_id") or "ans_fallback"),
        text=str(env.get("text") or ""),
        badge=str(env.get("badge") or "ABSTAIN"),
        abstained=bool(env.get("abstained")),
        values=list(env.get("values") or []),
        sql_used=env.get("sql_used"),
        assumptions=list(env.get("assumptions") or []) + [note],
        as_of=env.get("as_of"),
        contributing_sources=list(env.get("contributing_sources") or []),
        drillthrough_token=env.get("drillthrough_token"),
        audit_id=env.get("audit_id"),
        ask_mode="demo",
        demo_fallback_used=True,
        demo_fallback_banner=True,
        space_id=env.get("space_id"),
        session_id=env.get("session_id"),
        rows=list(env.get("rows") or []),
        chart=env.get("chart"),
        suggestions=list(env.get("suggestions") or []),
        route=env.get("route"),
    )
    assert_envelope_valid(env)
    return env


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
            return _stamp_demo_fallback(env, "fallback — Cortex client missing")
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
            return _stamp_demo_fallback(env, f"fallback after live error: {exc.code}")
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
            return _stamp_demo_fallback(env, "fallback — live ask failed")
        raise HTTPException(
            status_code=503,
            detail={"code": "live_ask_failed", "message": str(exc)[:400]},
        ) from exc


@router.post("/drillthrough")
def chat_drillthrough(
    body: DrillthroughBody,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    """T7 — show contributing rows for a live answer token (contract 1.2)."""
    if cortex is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "cortex_unavailable", "message": "Cortex client not configured"},
        )
    try:
        from cortex_client.models import DrillthroughRequest

        resp = cortex.drillthrough(DrillthroughRequest(token=body.token))
        return resp.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        logger.warning("drillthrough failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"code": "drillthrough_failed", "message": str(exc)[:400]},
        ) from exc
