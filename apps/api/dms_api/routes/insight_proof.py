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
from fastapi import APIRouter, HTTPException
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

router = APIRouter(prefix="/v1/insight-proof", tags=["insight-proof"])


def enabled() -> bool:
    """``DMS_INSIGHT_PROOF=1`` exactly, read from the environment (settings.py is held)."""
    return os.environ.get(FLAG, "0").strip() == "1"


class InsightProofRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str | None = None
    session_id: str = Field(default="insights", min_length=1, max_length=128)
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
    # (c) Fail closed: this route appends to the ledger.
    decision = compliance_gate(
        action=ACTION,
        metadata={"task_id": ACTION, "space_id": body.space_id},
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
            ask, cortex, space_id=body.space_id, session_id=body.session_id, top=body.top
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
        "scope": run.scope,
    }
    return JSONResponse(_redacted(jsonable_encoder(out), key))
