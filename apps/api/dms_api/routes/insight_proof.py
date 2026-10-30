"""INS-PROOF-02 - ``POST /v1/insight-proof/run`` (flag ``DMS_INSIGHT_PROOF``, default off).

Proven insights for a Space: every figure a cell of SQL that ran twice through
Cortex submit with equal rows, the grouped total conserved against a no-join
raw total, one Cortex ledger pointer per insight, and one ask-audit row per
candidate. With the flag off ``app.py`` does not mount this router, so every
path here is a 404 and the OpenAPI document is main's.

F5 fails CLOSED here. ``routes/insights.py`` soft-proceeds when the gate is
unreachable or does not know the task, because it is a read; this route
appends to the ledger, so no decision is a 503 and a deny is a 403
(``gatekeeping.enforce``). Who may run it is F5's decision, not a local policy.

The route never reads ``DMS_DEMO_FALLBACK`` or a demo pack, never returns a
batch-level "complete", and passes every string it returns through
``redact_secrets``. It reaches the executor only through ``dms_api.wiring``.

INS-PROOF-03 adds the steward pin routes under ``/pins``: preview, confirm (a
single-use token), replay and revoke, plus the list. Every one calls the gate
first and fails closed the same way, before any submit, ledger call or write.
Who may confirm is F5's decision; there is no local policy (rule 8). Nothing is
pinned, replayed or applied without a steward's request.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from cortex_client import compliance_gate
from cortex_client.insights import redact_secrets
from dms_core.ask import ExecutedTrace
from dms_core.control_plane.ask_audit import AskAuditRecord, AskAuditStorePort
from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from dms_api import wiring
from dms_api.ask_audit import record_ask, take_executed
from dms_api.deps import AskAuditDep, AskServiceDep, CortexDep, SettingsDep, SpaceStoreDep
from dms_api.gatekeeping import NO_DECISION_REASONS, enforce

logger = logging.getLogger(__name__)

FLAG = "DMS_INSIGHT_PROOF"
ACTION = "insight_proof.run"
#: Refusal codes after a ledger append was sent but no trusted receipt came back.
UNCONFIRMED_LEDGER_CODES = frozenset({"ledger_append_failed", "ledger_receipt_invalid"})

router = APIRouter(prefix="/v1/insight-proof", tags=["insight-proof"])


def enabled() -> bool:
    """``DMS_INSIGHT_PROOF=1`` exactly, read from the environment (settings.py is held)."""
    return os.environ.get(FLAG, "0").strip() == "1"


class InsightProofRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str | None = None
    #: An identifier, not free text. The ledger gets only its hash (``session_hash``).
    session_id: str = Field(default="insights", pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    top: int = Field(default=6, ge=1, le=20)


class _ConfirmingStore:
    """Forwards to the audit store and says whether THIS write returned normally.

    ``record_ask`` never raises: a failed write is counted in ``dropped``. A
    proven insight needs to know its own row landed, so ``ok`` is set only when
    the inner ``record`` returns.
    """

    def __init__(self, inner: AskAuditStorePort) -> None:
        self._inner = inner
        self.backend = getattr(inner, "backend", "")
        self.ok = False

    @property
    def dropped(self) -> int:
        return int(self._inner.dropped)

    @dropped.setter
    def dropped(self, value: int) -> None:
        self._inner.dropped = value

    def record(self, rec: AskAuditRecord) -> None:
        self._inner.record(rec)
        self.ok = True

    def list_between(self, **kwargs: Any) -> list[AskAuditRecord]:
        return self._inner.list_between(**kwargs)


def _unavailable(message: str, key: str | None) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "code": "cortex_unavailable",
            "values": [],
            "message": redact_secrets(message, key)[:400],
        },
        status_code=503,
    )


def _redacted(obj: Any, key: str | None) -> Any:
    if isinstance(obj, str):
        return redact_secrets(obj, key)
    if isinstance(obj, dict):
        return {_redacted(k, key): _redacted(v, key) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redacted(v, key) for v in obj]
    return obj


def _served_statement(p: Any) -> ExecutedTrace:
    """The audit row's evidence for a proven insight: the statement whose rows it shows.

    The trace holds the raw total, the negative-parts count and the wrapper twice;
    the first two are pinned by hash in the proof and the ledger payload. The row
    records the wrapper once, with its row count, and the ledger entry it points at.
    """
    trace = p.trace if isinstance(p.trace, ExecutedTrace) else ExecutedTrace()
    hits = [(sql, n) for sql, n in trace.statements if sql == p.sql]
    return ExecutedTrace(statements=tuple(hits[-1:]), ledger=trace.ledger)


@router.get("/status")
def insight_proof_status() -> dict[str, Any]:
    return {"enabled": True}


@router.post("/run")
def insight_proof_run(
    body: InsightProofRunIn,
    settings: SettingsDep,
    store: SpaceStoreDep,
    cortex: CortexDep,
    ask: AskServiceDep,
    audit: AskAuditDep,
) -> Any:
    # (a) An unknown Space is a rejected request: no gate, no submit, no audit row.
    if body.space_id is not None and store.get(body.space_id) is None:
        raise HTTPException(status_code=404, detail="space_not_found")
    # (b) No engine, no proof. DMS_DEMO_FALLBACK is not read on this route.
    if cortex is None:
        return _unavailable("Cortex client not configured", None)
    key = getattr(cortex, "api_key", None)
    # (c) Fail closed: this route appends to the ledger. The gate names the one
    # configured actor (DR-0004 Option A), the same identity the ledger append and
    # the audit row carry. ``compliance_gate`` puts only ``filled_template`` on the
    # wire, so the Space goes there too, where F5 can scope the task by it.
    decision = compliance_gate(
        action=ACTION,
        actor=settings.dms_actor_user_id,
        metadata={
            "task_id": ACTION,
            "space_id": body.space_id,
            "filled_template": {"space_id": body.space_id},
        },
        client=cortex,
    )
    if not decision.allowed:
        if getattr(decision, "reason", None) in NO_DECISION_REASONS:
            return _unavailable("compliance gate gave no decision; nothing was run", key)
        enforce(decision, mutation=True)
    # (d) Start from a clean per-thread trace.
    take_executed(ask)
    asked_at = datetime.now(UTC)
    # (e)
    try:
        run = wiring.prove_insights(
            ask,
            cortex,
            space_id=body.space_id,
            session_id=body.session_id,
            top=body.top,
            actor=settings.dms_actor_user_id,
        )
    except Exception as exc:
        record_ask(
            audit,
            settings,
            question="insight:run",
            space_id=body.space_id,
            asked_at=asked_at,
            error=exc,
            executed=take_executed(ask),
        )
        raise
    if getattr(run, "status", None) == "off":
        # The flag was turned off after the app was built: behave as unmounted.
        raise HTTPException(status_code=404, detail="Not Found")

    insights: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = list(run.refused)
    orphans: list[str] = [str(r["orphan_ledger_entry"]) for r in refused
                          if r.get("orphan_ledger_entry")]
    # (f) one audit row per candidate; (g) a proven insight needs its row.
    for entry in run.refused:
        trace = entry.get("trace")
        record_ask(
            audit,
            settings,
            question="insight:" + str(entry.get("insight_id") or entry["candidate"]),
            space_id=body.space_id,
            asked_at=asked_at,
            envelope=entry["envelope"],
            executed=trace if isinstance(trace, ExecutedTrace) else None,
        )
    for p in run.proven:
        confirm = _ConfirmingStore(audit)
        record_ask(
            confirm,  # type: ignore[arg-type]
            settings,
            question="insight:" + p.insight_id,
            space_id=body.space_id,
            asked_at=asked_at,
            envelope=p.envelope,
            executed=_served_statement(p),
        )
        if not confirm.ok:
            entry_id = str(p.envelope.get("audit_id") or "")
            refused.append(
                wiring.demote_insight(p, "audit_unrecorded", orphan_ledger_entry=entry_id)
            )
            if entry_id and entry_id not in orphans:
                orphans.append(entry_id)
            continue
        insights.append({"envelope": p.envelope, "proof": p.proof})

    # (h)
    out = {
        "insights": insights,
        "refused": [
            {
                "candidate": r["candidate"],
                "code": r["code"],
                "text": str((r.get("envelope") or {}).get("text") or ""),
            }
            for r in refused
        ],
        "counts": {"proven": len(insights), "refused": len(refused)},
        "orphan_ledger_entries": orphans,
        # Appends whose outcome DMS does not know: no receipt, or one it would not
        # trust. They may be on the chain; contract 1.2.0 cannot read them back.
        "unconfirmed_ledger_appends": sum(
            1 for r in refused
            if str(r["code"]).split(":", 1)[0] in UNCONFIRMED_LEDGER_CODES
        ),
        "scope": run.scope,
    }
    return JSONResponse(_redacted(jsonable_encoder(out), key))


# --------------------------------------------------------------------------
# INS-PROOF-03 - steward pins
# --------------------------------------------------------------------------

#: Closed: register refusal code -> HTTP status. Anything unlisted is a 409.
PIN_STATUS: dict[str, int] = {
    "insight_not_found": 404,
    "pin_not_found": 404,
    "confirm_token_unknown": 409,
    "confirm_token_used": 409,
    "confirm_token_expired": 410,
    "stale_preview": 409,
    "already_pinned": 409,
    "measure_not_approved": 409,
    "scored_write_blocked": 409,
    "not_pinned": 409,
    "ledger_append_failed": 503,
    "ledger_receipt_invalid": 503,
    "cortex_unavailable": 503,
    "cortex_submit_failed": 503,
    "pin_commit_failed": 500,
    "revoke_commit_failed": 500,
}


class PinPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str | None = None
    insight_id: str = Field(pattern=r"^ins_[0-9a-f]{16}$")


class PinConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)


def _undecided(decision: Any, cortex: Any) -> JSONResponse | None:
    """After ``compliance_gate``: no decision is a 503, a deny is a 403 (enforce)."""
    if not decision.allowed:
        if getattr(decision, "reason", None) in NO_DECISION_REASONS:
            return _unavailable(
                "compliance gate gave no decision; nothing was run",
                getattr(cortex, "api_key", None),
            )
        enforce(decision, mutation=True)
    return None


def _pin_refusal(res: dict[str, Any], key: str | None) -> JSONResponse:
    code = str(res.get("code") or "")
    if code == "off":
        # The flag was turned off after the app was built: behave as unmounted.
        raise HTTPException(status_code=404, detail="Not Found")
    body = {k: v for k, v in res.items() if k not in ("trace", "envelope")}
    return JSONResponse(_redacted(jsonable_encoder(body), key),
                        status_code=PIN_STATUS.get(code, 409))


def _known_space(store: Any, space_id: str | None) -> None:
    if space_id is not None and store.get(space_id) is None:
        raise HTTPException(status_code=404, detail="space_not_found")


@router.post("/pins/preview")
def insight_proof_pin_preview(
    body: PinPreviewIn, store: SpaceStoreDep, cortex: CortexDep, ask: AskServiceDep
) -> Any:
    _known_space(store, body.space_id)
    if cortex is None:
        return _unavailable("Cortex client not configured", None)
    key = getattr(cortex, "api_key", None)
    decision = compliance_gate(
        action="insight_proof.pin.preview",
        metadata={"task_id": "insight_proof.pin.preview", "space_id": body.space_id},
        client=cortex,
    )
    if (stop := _undecided(decision, cortex)) is not None:
        return stop
    take_executed(ask)
    res = wiring.preview_pin(ask, space_id=body.space_id, insight_id=body.insight_id)
    take_executed(ask)
    if not res.get("ok"):
        return _pin_refusal(res, key)
    out = {k: res[k] for k in ("envelope", "proof", "token", "expires_at")}
    return JSONResponse(_redacted(jsonable_encoder(out), key))


@router.post("/pins/confirm")
def insight_proof_pin_confirm(
    body: PinConfirmIn,
    settings: SettingsDep,
    cortex: CortexDep,
    ask: AskServiceDep,
    audit: AskAuditDep,
) -> Any:
    if cortex is None:
        return _unavailable("Cortex client not configured", None)
    key = getattr(cortex, "api_key", None)
    space_id = wiring.pin_owner_space(ask, token=body.token)
    decision = compliance_gate(
        action="insight_proof.pin.confirm",
        metadata={"task_id": "insight_proof.pin.confirm", "space_id": space_id},
        client=cortex,
    )
    if (stop := _undecided(decision, cortex)) is not None:
        return stop
    take_executed(ask)
    asked_at = datetime.now(UTC)
    res = wiring.confirm_pin(ask, cortex, token=body.token, actor=settings.dms_actor_user_id)
    take_executed(ask)
    if not res.get("ok"):
        return _pin_refusal(res, key)
    confirm = _ConfirmingStore(audit)
    record_ask(
        confirm,  # type: ignore[arg-type]
        settings,
        question="insight:" + str(res["insight_id"]),
        space_id=space_id,
        asked_at=asked_at,
        envelope=res["envelope"],
        executed=res["trace"],
    )
    out = {k: res[k] for k in ("asset_id", "insight_id", "approved_at", "approved_by",
                               "expected_result_hash", "ledger_entry_id")}
    # The pin and its ledger entry are committed; a dropped audit row is reported,
    # never hidden (INS-PROOF-02 rule g, applied to the confirm).
    out["audit_recorded"] = confirm.ok
    if not confirm.ok:
        logger.error("insight pin %s committed without its audit row", res["asset_id"])
    return JSONResponse(_redacted(jsonable_encoder(out), key))


@router.post("/pins/{asset_id}/replay")
def insight_proof_pin_replay(
    asset_id: str,
    settings: SettingsDep,
    cortex: CortexDep,
    ask: AskServiceDep,
    audit: AskAuditDep,
) -> Any:
    if cortex is None:
        return _unavailable("Cortex client not configured", None)
    key = getattr(cortex, "api_key", None)
    space_id = wiring.pin_owner_space(ask, asset_id=asset_id)
    decision = compliance_gate(
        action="insight_proof.replay",
        metadata={"task_id": "insight_proof.replay", "space_id": space_id},
        client=cortex,
    )
    if (stop := _undecided(decision, cortex)) is not None:
        return stop
    take_executed(ask)
    asked_at = datetime.now(UTC)
    res = wiring.replay_pin(ask, cortex, asset_id, actor=settings.dms_actor_user_id)
    take_executed(ask)
    if not res.get("ok"):
        return _pin_refusal(res, key)
    confirm = _ConfirmingStore(audit)
    record_ask(
        confirm,  # type: ignore[arg-type]
        settings,
        question="insight:" + str(res["insight_id"]),
        space_id=space_id,
        asked_at=asked_at,
        envelope=res["envelope"],
        executed=res["trace"],
    )
    if not confirm.ok and res["envelope"].get("abstained") is False:
        # (g) a served replay needs its row: no row, no figure; name the entry.
        entry_id = str(res["envelope"].get("audit_id") or "")
        res = wiring.demote_replay(res, "audit_unrecorded", orphan_ledger_entry=entry_id)
    out = {k: res[k] for k in ("asset_id", "verdict", "envelope", "proof")}
    if res.get("orphan_ledger_entry"):
        out["orphan_ledger_entry"] = res["orphan_ledger_entry"]
    return JSONResponse(_redacted(jsonable_encoder(out), key))


@router.post("/pins/{asset_id}/revoke")
def insight_proof_pin_revoke(
    asset_id: str, settings: SettingsDep, cortex: CortexDep, ask: AskServiceDep
) -> Any:
    if cortex is None:
        return _unavailable("Cortex client not configured", None)
    key = getattr(cortex, "api_key", None)
    space_id = wiring.pin_owner_space(ask, asset_id=asset_id)
    decision = compliance_gate(
        action="insight_proof.revoke",
        metadata={"task_id": "insight_proof.revoke", "space_id": space_id},
        client=cortex,
    )
    if (stop := _undecided(decision, cortex)) is not None:
        return stop
    res = wiring.revoke_pin(ask, cortex, asset_id, actor=settings.dms_actor_user_id)
    if not res.get("ok"):
        return _pin_refusal(res, key)
    out = {k: res[k] for k in ("asset_id", "revoked_at", "revoke_ledger_entry_id")}
    return JSONResponse(_redacted(jsonable_encoder(out), key))


@router.get("/pins")
def insight_proof_pins(
    store: SpaceStoreDep,
    cortex: CortexDep,
    ask: AskServiceDep,
    space_id: str | None = Query(default=None),
) -> Any:
    _known_space(store, space_id)
    if cortex is None:
        return _unavailable("Cortex client not configured", None)
    key = getattr(cortex, "api_key", None)
    decision = compliance_gate(
        action="insight_proof.pins.read",
        metadata={"task_id": "insight_proof.pins.read", "space_id": space_id},
        client=cortex,
    )
    if (stop := _undecided(decision, cortex)) is not None:
        return stop
    pins = wiring.list_pinned(ask, space_id=space_id)
    return JSONResponse(_redacted(jsonable_encoder({"pins": pins}), key))
