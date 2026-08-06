"""Scoped chat ask — product path = live Cortex; demo = fallback only."""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex_client import compliance_gate
from dms_core.ask import AskServiceError, GroundingRefused
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dms_api.deps import AskServiceDep, CortexDep, SettingsDep, SpaceStoreDep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/chat", tags=["chat"])

_SOFT_GATE = frozenset({"gate_unavailable", "gate_task_unknown"})

#: Cortex names the offending table in the refusal; lift it so the message can
#: tell the customer which file to add rather than which manifest complained.
_MANIFEST_TABLE_RE = re.compile(r"table '([^']+)' is not named by this manifest")


def _missing_table(detail: str | None) -> str | None:
    m = _MANIFEST_TABLE_RE.search(detail or "")
    return m.group(1) if m else None

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
    #: Warehouse tables the user ticked in Studio. Narrows the session manifest,
    #: so the grounding is enforced by Cortex rather than hinted to the model.
    #: Capped because a "scope" listing everything is not a scope, and the list
    #: reaches manifest minting.
    grounded_tables: list[str] | None = Field(default=None, max_length=32)


class DrillthroughBody(BaseModel):
    token: str = Field(min_length=1)


def _space_refusal_envelope(
    *,
    space_id: str,
    space_name: str | None,
    missing_table: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Render a Space-boundary refusal as an answer, not as an error.

    dms#2 acceptance: a question needing a table this Space has no grant for
    returns ``abstained: true`` with a reason on the envelope. A green badge
    over a refusal is a P0, and so is a raw engine error where an answer goes.
    """
    from dms_api.wiring import build_validated_envelope

    where = f"in {space_name}" if space_name else "in this Space"
    subject = f"{missing_table} data" if missing_table else "data"
    return build_validated_envelope(
        answer_id=f"ans_refused_{space_id[:8]}",
        text=(
            f"I can't answer that {where}. It needs {subject}, which this Space "
            f"has no access to. Ask in a Space that holds it, or request the grant."
        ),
        badge="ABSTAIN",
        abstained=True,
        assumptions=["refused by the Space boundary"],
        ask_mode="live",
        space_id=space_id,
        session_id=session_id,
    )


def _stamp_demo_fallback(env: dict[str, Any], note: str) -> dict[str, Any]:
    """E6 — demo_fallback_used must set an unmissable banner flag."""
    from dms_api.wiring import build_validated_envelope

    return build_validated_envelope(
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
            tables=body.grounded_tables,
        )
    except GroundingRefused as exc:
        # Refusing is the fix, not the failure: this used to widen the manifest
        # to the whole demo warehouse while the UI read "Grounded in 1 file".
        # Demo fallback must not catch this either — answering from demo numbers
        # after refusing the requested scope is the same lie in a new costume.
        raise HTTPException(
            status_code=403,
            detail={
                "code": exc.code,
                "message": exc.message,
                "ungrantable_tables": list(exc.ungrantable),
                "grantable_tables": list(exc.grantable),
            },
        ) from exc
    except AskServiceError as exc:
        # A grounded question that needs a table the user did not tick is not a
        # failure — it is the scope working. Saying so beats handing back
        # "path_not_allowed: table 'alerts' is not named by this manifest",
        # which reads as a bug rather than as the answer to what was asked.
        if exc.code == "path_not_allowed" and body.grounded_tables:
            missing = _missing_table(exc.detail)
            chosen = ", ".join(body.grounded_tables)
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "outside_grounded_scope",
                    "message": (
                        f"That needs {missing or 'a table'}, which is not in the "
                        f"{len(body.grounded_tables)} file(s) you grounded this question in "
                        f"({chosen}). Widen the selection or clear it to use the whole Space."
                    ),
                    "grounded_tables": list(body.grounded_tables),
                    "required_table": missing,
                },
            ) from exc

        # The Space boundary refusing is the control working, and dms#2 requires
        # it to arrive as an envelope with abstained: true and a reason - not as
        # a raw "path_not_allowed / Unexpected status code: 403", which reads as
        # a crash. This is the half of the demo that must look deliberate.
        if exc.code == "path_not_allowed" and body.space_id:
            space = store.get(body.space_id)
            missing = _missing_table(exc.detail)
            return _space_refusal_envelope(
                space_id=body.space_id,
                space_name=getattr(space, "name", None),
                missing_table=missing,
                session_id=body.session_id,
            )

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
    decision = compliance_gate(
        action="chat.drillthrough",
        metadata={"task_id": "chat.drillthrough", "token_prefix": body.token[:12]},
        client=cortex,
    )
    if not decision.allowed and decision.reason not in _SOFT_GATE:
        raise HTTPException(status_code=403, detail=decision.reason)

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
