"""Hosted DMS consume of Cortex GET|POST /v1/insights (INSIGHTS-HOST-01 / #196).

Forwards the configured OpenVault founder key (``settings.cortex_api_key``).
Never invents LIVE_KEY. Never claims live :5000 green. Fail closed when Cortex
or (for generate) OpenVault is unreachable. No demo-number fallback.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from cortex_client import compliance_gate
from cortex_client.insights import InsightsError, honest_envelope, redact_secrets
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/insights", tags=["insights"])

_SOFT_ASK = frozenset({"gate_unavailable", "gate_task_unknown"})


class InsightsAskIn(BaseModel):
    intent: str = ""
    question: str = ""
    ask: bool = True
    generate: bool = False
    session_id: str = "demo"
    space_id: str | None = None


def openvault_reachable(base_url: str, timeout: float = 1.5) -> bool:
    """True when OpenVault answers a health/JWKS probe. Never claims :5000 green."""
    base = (base_url or "").rstrip("/")
    if not base:
        return False
    try:
        with httpx.Client(timeout=timeout) as client:
            for path in ("/api/healthz", "/health", "/keys/jwks", "/api/health"):
                try:
                    res = client.get(f"{base}{path}")
                except httpx.HTTPError:
                    continue
                if res.status_code < 500 and res.status_code != 404:
                    return True
    except httpx.HTTPError:
        return False
    return False


def _closed(
    *,
    code: str,
    message: str,
    status_code: int = 503,
    extra: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "status": "REFUSE",
            "values": [],
            "code": code,
            "live_5000_ci": False,
            "message": redact_secrets(message, extra)[:400],
        },
        status_code=status_code,
    )


def _from_error(exc: InsightsError, extra: str | None) -> JSONResponse:
    if isinstance(exc.payload, dict):
        return JSONResponse(honest_envelope(exc.payload), status_code=exc.status_code)
    return _closed(
        code=exc.code,
        message=str(exc),
        status_code=exc.status_code,
        extra=extra,
    )


def _require_cortex(cortex: Any) -> JSONResponse | None:
    if cortex is None:
        return _closed(
            code="cortex_unavailable",
            message="Cortex client not configured",
        )
    return None


@router.get("")
@router.get("/")
def insights_law(cortex: CortexDep) -> Any:
    missing = _require_cortex(cortex)
    if missing is not None:
        return missing
    assert cortex is not None
    try:
        return honest_envelope(cortex.insights_law())
    except InsightsError as exc:
        logger.warning("insights GET law failed (%s)", exc.__class__.__name__)
        return _from_error(exc, getattr(cortex, "api_key", None))


@router.get("/keys")
def insights_keys(cortex: CortexDep) -> Any:
    missing = _require_cortex(cortex)
    if missing is not None:
        return missing
    assert cortex is not None
    try:
        return honest_envelope(cortex.insights_keys())
    except InsightsError as exc:
        logger.warning("insights GET keys failed (%s)", exc.__class__.__name__)
        return _from_error(exc, getattr(cortex, "api_key", None))


@router.get("/identity")
def insights_identity(cortex: CortexDep) -> Any:
    missing = _require_cortex(cortex)
    if missing is not None:
        return missing
    assert cortex is not None
    try:
        return honest_envelope(cortex.insights_identity())
    except InsightsError as exc:
        logger.warning("insights GET identity failed (%s)", exc.__class__.__name__)
        return _from_error(exc, getattr(cortex, "api_key", None))


@router.get("/ontology")
def insights_ontology(cortex: CortexDep, q: str = "") -> Any:
    intent = (q or "").strip()
    if not intent:
        raise HTTPException(status_code=400, detail="q is required")
    missing = _require_cortex(cortex)
    if missing is not None:
        return missing
    assert cortex is not None
    try:
        return honest_envelope(cortex.insights_ontology(intent))
    except InsightsError as exc:
        logger.warning("insights GET ontology failed (%s)", exc.__class__.__name__)
        return _from_error(exc, getattr(cortex, "api_key", None))


@router.post("")
@router.post("/")
def insights_ask(body: InsightsAskIn, settings: SettingsDep, cortex: CortexDep) -> Any:
    intent = (body.intent or body.question or "").strip()
    if not intent:
        raise HTTPException(status_code=400, detail="intent or question is required")

    missing = _require_cortex(cortex)
    if missing is not None:
        return missing
    assert cortex is not None

    decision = compliance_gate(
        action="insights.ask",
        metadata={"task_id": "insights.ask", "space_id": body.space_id},
        client=cortex,
    )
    # Query, not a lake write: catalog-miss/unreachable F5 may proceed; Cortex
    # HTTP still fails closed below. A hard F5 deny stays 403.
    if not decision.allowed and decision.reason not in _SOFT_ASK:
        enforce(decision, mutation=True)

    if body.generate and not openvault_reachable(settings.openvault_url):
        return _closed(
            code="openvault_unavailable",
            message="OpenVault unavailable; generate insights fail closed",
            extra=getattr(cortex, "api_key", None),
        )

    try:
        return honest_envelope(
            cortex.insights_ask(
                intent=intent,
                question=body.question,
                ask=body.ask,
                generate=body.generate,
                session_id=body.session_id,
                space_id=body.space_id,
                consumer="dms",
            )
        )
    except InsightsError as exc:
        logger.warning("insights POST failed (%s)", exc.__class__.__name__)
        return _from_error(exc, getattr(cortex, "api_key", None))
