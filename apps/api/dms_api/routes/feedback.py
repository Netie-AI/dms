"""Answer feedback (UX-FEEDBACK-01).

``DMS_FEEDBACK_LOOP`` defaults to off. While it is not the digit 1, ``app.py``
does not mount this router, so every path here is a 404 and the OpenAPI
document is main's.

Yes files a verified example only when this process, or the ask audit, already
recorded a governed answer with that SQL. No, Fix it, and Request review do
not file one. Steward approval of a queued fix re-runs the model SQL gates,
then files. Lookup never replays the filed row.
"""

from __future__ import annotations

from typing import Any, Literal

from cortex_client import compliance_gate
from dms_core.control_plane.onto_data import ontology_data_store
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import AskAuditDep, CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import (
    feedback_approve,
    feedback_audits,
    feedback_fix,
    feedback_governed,
    feedback_queue,
    feedback_report,
    feedback_reviews,
    feedback_thumb,
)

FLAG = "DMS_FEEDBACK_LOOP"

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])


def enabled() -> bool:
    """``DMS_FEEDBACK_LOOP=1`` exactly. Unset is off."""
    import os

    return os.environ.get(FLAG, "0").strip() == "1"


class ThumbIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(default="", max_length=8000)
    audit_id: str = Field(default="", max_length=128)
    vote: Literal["up", "down"]
    reason: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=2000)


class FixIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=8000)
    interpretation: str = Field(default="", max_length=2000)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=8000)
    interpretation: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=2000)
    space_id: str = Field(default="", max_length=128)
    question: str = Field(default="", max_length=2000)


def _actor(settings: Any) -> str:
    if settings.dms_actor_user_id is None:
        return ""
    return str(settings.dms_actor_user_id).strip()


def _steward(settings: Any) -> str:
    actor = _actor(settings)
    role = "" if settings.dms_actor_role is None else str(settings.dms_actor_role)
    if not ontology_data_store().holds_steward_grant(role=role, actor_id=actor):
        raise HTTPException(status_code=403, detail="steward_grant_required")
    return actor


def _http(exc: BaseException) -> HTTPException:
    if type(exc).__name__ == "FeedbackDisabled":
        return HTTPException(status_code=404, detail="Not Found")
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="review_not_found")
    if isinstance(exc, ValueError) and exc.args and isinstance(exc.args[0], str):
        return HTTPException(status_code=400, detail=exc.args[0])
    raise exc


def _ledger(
    cortex: Any,
    settings: Any,
    *,
    event_type: str,
    payload: dict[str, Any],
    audit: dict[str, Any] | None,
) -> None:
    """Best-effort Cortex append. The in-memory audit row is not a second chain."""
    if audit is None or cortex is None:
        return
    try:
        from cortex_client.models import LedgerAppendRequest

        appended = cortex.ledger_append(
            LedgerAppendRequest(
                event_type=event_type,
                payload=payload,  # type: ignore[arg-type]
                actor=_actor(settings) or "dms",
            )
        )
    except Exception:
        return
    entry_id = getattr(appended, "entry_id", None)
    if entry_id:
        audit["ledger_entry_id"] = str(entry_id)


def _public_audit(audit: dict[str, Any] | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    return {
        "id": audit.get("id"),
        "action": audit.get("action"),
        "asset_id": audit.get("asset_id"),
        "reason": audit.get("reason"),
        "ledger_entry_id": audit.get("ledger_entry_id"),
    }


@router.get("/status")
def feedback_status(cortex: CortexDep, settings: SettingsDep) -> dict[str, bool]:
    decision = compliance_gate(
        action="feedback.status",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "feedback.status"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return {"enabled": True}


@router.get("/audit")
def list_feedback_audit(cortex: CortexDep, settings: SettingsDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="feedback.audit_read",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "feedback.audit_read"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return {"rows": feedback_audits()}


@router.get("/reviews")
def list_feedback_reviews(cortex: CortexDep, settings: SettingsDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="feedback.reviews_read",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "feedback.reviews_read"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    _steward(settings)
    return {"reviews": feedback_reviews()}


@router.post("/thumb")
def post_thumb(
    body: ThumbIn,
    cortex: CortexDep,
    settings: SettingsDep,
    audit: AskAuditDep,
) -> dict[str, Any]:
    actor = _actor(settings)
    decision = compliance_gate(
        action="feedback.thumb",
        actor=actor,
        metadata={"task_id": "feedback.thumb", "space_id": body.space_id, "vote": body.vote},
        client=cortex,
    )
    enforce(decision)
    governed = False
    if body.vote == "up":
        try:
            rows = audit.list_between(since=None, until=None, limit=200)
        except Exception:
            rows = []
        governed = feedback_governed(
            space_id=body.space_id,
            question=body.question,
            sql=body.sql,
            audit_rows=rows,
        )
    try:
        result = feedback_thumb(
            space_id=body.space_id,
            question=body.question,
            sql=body.sql,
            vote=body.vote,
            reason=body.reason,
            note=body.note,
            governed=governed,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 - checker code or FeedbackDisabled
        raise _http(exc) from None
    _ledger(
        cortex,
        settings,
        event_type="feedback.thumb",
        payload={"vote": body.vote, "space_id": body.space_id, "audit_id": body.audit_id},
        audit=result.get("audit"),
    )
    return {
        "vote": result["vote"],
        "filed": result["filed"],
        "asset_id": result.get("asset_id"),
        "audit": _public_audit(result.get("audit")),
    }


@router.post("/fix")
def post_fix(
    body: FixIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(settings)
    decision = compliance_gate(
        action="feedback.fix",
        actor=actor,
        metadata={"task_id": "feedback.fix", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision)
    try:
        result = feedback_fix(
            space_id=body.space_id,
            question=body.question,
            sql=body.sql,
            interpretation=body.interpretation,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 - checker code or FeedbackDisabled
        raise _http(exc) from None
    _ledger(
        cortex,
        settings,
        event_type="feedback.fix",
        payload={"space_id": body.space_id, "filed": False},
        audit=result.get("audit"),
    )
    return {
        "ok": True,
        "filed": False,
        "rows": result.get("rows") or [],
        "row_count": result.get("row_count") or 0,
        "truncated": result.get("truncated") is True,
        "interpretation": result.get("interpretation") or "",
        "audit": _public_audit(result.get("audit")),
    }


@router.post("/review")
def post_review(
    body: ReviewIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(settings)
    decision = compliance_gate(
        action="feedback.review",
        actor=actor,
        metadata={"task_id": "feedback.review", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision)
    try:
        result = feedback_queue(
            space_id=body.space_id,
            question=body.question,
            sql=body.sql,
            interpretation=body.interpretation,
            note=body.note,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 - FeedbackDisabled or a missing field
        raise _http(exc) from None
    _ledger(
        cortex,
        settings,
        event_type="feedback.review",
        payload={"space_id": body.space_id, "review_id": result.get("id")},
        audit=result.get("audit"),
    )
    return {
        "id": result["id"],
        "status": result["status"],
        "filed": False,
        "audit": _public_audit(result.get("audit")),
    }


@router.post("/reviews/{review_id}/approve")
def post_approve(
    review_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _steward(settings)
    decision = compliance_gate(
        action="feedback.approve",
        actor=actor,
        metadata={"task_id": "feedback.approve", "review_id": review_id},
        client=cortex,
    )
    enforce(decision)
    try:
        result = feedback_approve(review_id=review_id, actor=actor)
    except Exception as exc:  # noqa: BLE001 - checker code, missing review, or flag off
        raise _http(exc) from None
    _ledger(
        cortex,
        settings,
        event_type="feedback.approve",
        payload={"review_id": review_id, "asset_id": result.get("asset_id") or ""},
        audit=result.get("audit"),
    )
    return {
        "id": review_id,
        "status": (result.get("review") or {}).get("status"),
        "filed": result.get("filed") is True,
        "asset_id": result.get("asset_id"),
        "audit": _public_audit(result.get("audit")),
    }


@router.post("/report")
def post_report(
    body: ReportIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    actor = _actor(settings)
    decision = compliance_gate(
        action="feedback.report",
        actor=actor,
        metadata={"task_id": "feedback.report", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision)
    try:
        result = feedback_report(
            note=body.note,
            space_id=body.space_id,
            question=body.question,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 - FeedbackDisabled or an empty note
        raise _http(exc) from None
    failure = result.get("failure") or {}
    _ledger(
        cortex,
        settings,
        event_type="feedback.report",
        payload={"space_id": body.space_id, "stage": "report"},
        audit=result.get("audit"),
    )
    return {
        "id": failure.get("id"),
        "filed": False,
        "stage": failure.get("stage"),
        "reason": failure.get("reason"),
        "audit": _public_audit(result.get("audit")),
    }
