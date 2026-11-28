"""Pipeline webhooks, bench, and semantic import (FEAT-PIPELINE-STEP-01).

``DMS_PIPELINE_HOOKS`` defaults to off. While it is not the digit 1, ``app.py``
does not mount this router, so every path here is a 404.

Registration stores an OpenVault reference. The HMAC key is resolved at
delivery and is not written to the row, the log, or the response. Payloads
carry ids and outcomes. ``include_values`` is the Space owner's opt-in for
the envelope ``values`` list.

Semantic import returns 501 until FEAT-SEMANTIC-IMPORT-01 is merged. A
dry_run body does not import. The bench route does not grade the 52 and does
not invent WRONG=0 when no cases are declared.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from cortex_client import compliance_gate
from dms_core.control_plane.webhook_sign import (
    HEADER,
    WebhookSecretError,
    build_payload,
    canonical_json,
    event_for_envelope,
    outcome_for_envelope,
    ref_key_id,
    signature_header,
)
from dms_core.control_plane.webhook_store import (
    InMemoryWebhookStore,
    WebhookStore,
    WebhookSubscription,
)
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SpaceStoreDep
from dms_api.gatekeeping import enforce

logger = logging.getLogger(__name__)

FLAG = "DMS_PIPELINE_HOOKS"
router = APIRouter(prefix="/v1/pipeline", tags=["pipeline"])

_EVENTS = frozenset({"answer.served", "clarify.needed", "benchmark.finished"})


def enabled() -> bool:
    """``DMS_PIPELINE_HOOKS=1`` exactly. Unset and ``0`` are off."""
    return os.environ.get(FLAG, "0").strip() == "1"


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=12, max_length=500)
    secret_ref: str = Field(min_length=1, max_length=128)
    events: list[str] = Field(min_length=1, max_length=3)
    include_values: bool = False


class BenchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str = Field(min_length=1, max_length=128)
    dry_run: bool = True
    document: dict[str, Any] | None = None


def _https_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    return bool(parsed.hostname)


def _events(names: list[str]) -> tuple[str, ...]:
    if any(name not in _EVENTS for name in names):
        raise HTTPException(status_code=400, detail="webhook_event")
    # Keep the caller's order, drop duplicates.
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def _store(app: Any) -> WebhookStore:
    current = getattr(app.state, "webhook_store", None)
    if current is None:
        current = InMemoryWebhookStore()
        app.state.webhook_store = current
    return current


def _known_space(store: Any, space_id: str) -> None:
    if store.get(space_id) is None:
        raise HTTPException(status_code=404, detail="space_not_found")


def _resolver(app: Any):
    custom = getattr(app.state, "webhook_resolver", None)
    if custom is not None:
        return custom
    return _openvault_resolver


def _poster(app: Any):
    custom = getattr(app.state, "webhook_poster", None)
    if custom is not None:
        return custom
    return _post_once


def _openvault_resolver(secret_ref: str) -> bytes:
    """Resolve one OpenVault reference. Failures raise a code and log nothing else."""
    key_id = ref_key_id(secret_ref)
    if key_id is None:
        raise WebhookSecretError()
    from dms_api.settings import get_settings

    settings = get_settings()
    token = os.environ.get("DMS_OV_SERVICE_TOKEN", "").strip()
    headers = {"X-OpenVault-Reveal": "intentional"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        res = httpx.get(
            f"{settings.openvault_url.rstrip('/')}/api/keys/{key_id}/secret",
            headers=headers,
            timeout=5.0,
        )
    except httpx.HTTPError:
        raise WebhookSecretError() from None
    if res.status_code >= 400:
        raise WebhookSecretError()
    try:
        body = res.json()
    except ValueError:
        raise WebhookSecretError() from None
    secret = body.get("secret") if isinstance(body, dict) else None
    if not isinstance(secret, str) or secret == "":
        raise WebhookSecretError()
    return secret.encode("utf-8")


def _post_once(url: str, body: bytes, headers: dict[str, str]) -> None:
    """One POST. No retry: a second delivery would double-notify the receiver."""
    httpx.post(url, content=body, headers=headers, timeout=5.0)


def dispatch(
    app: Any,
    *,
    event: str,
    space_id: str,
    outcome: str,
    route: str | None,
    audit_id: str | None = None,
    run_id: str | None = None,
    values: list[Any] | None = None,
) -> None:
    """Record the proof and POST a signed body to each matching subscription.

    A resolver or poster failure is logged as a static code. It does not
    change the caller's envelope.
    """
    book = _store(app)
    book.note_proof(
        space_id=space_id,
        event=event,
        outcome=outcome,
        route=route,
        audit_id=audit_id,
        run_id=run_id,
    )
    resolve = _resolver(app)
    post = _poster(app)
    for sub in book.list_for_space(space_id):
        if event not in sub.events:
            continue
        payload = build_payload(
            event=event,
            space_id=space_id,
            outcome=outcome,
            route=route,
            audit_id=audit_id,
            run_id=run_id,
            include_values=sub.include_values,
            values=values if sub.include_values else None,
        )
        raw = canonical_json(payload)
        try:
            secret = resolve(sub.secret_ref)
            header = signature_header(secret, raw)
        except WebhookSecretError:
            logger.warning("webhook_secret_unresolved")
            continue
        headers = {HEADER: header, "Content-Type": "application/json"}
        try:
            post(sub.url, raw, headers)
        except Exception:
            logger.warning("webhook_delivery_failed")


def notify_envelope(app: Any, env: dict[str, Any], *, space_id: str | None) -> None:
    """Side effect after an ask. Does not mutate ``env``."""
    if not enabled() or not space_id:
        return
    event = event_for_envelope(env)
    if event is None:
        return
    audit = env.get("audit_id")
    route = env.get("served_route") or env.get("route")
    values = env.get("values") if isinstance(env.get("values"), list) else None
    dispatch(
        app,
        event=event,
        space_id=space_id,
        outcome=outcome_for_envelope(env),
        route=str(route) if isinstance(route, str) else None,
        audit_id=str(audit) if isinstance(audit, str) else None,
        values=values,
    )


@router.post("/webhooks", status_code=201)
def register_webhook(
    body: RegisterIn,
    request: Request,
    spaces: SpaceStoreDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    _known_space(spaces, body.space_id)
    if not _https_url(body.url):
        raise HTTPException(status_code=400, detail="webhook_url")
    if ref_key_id(body.secret_ref) is None:
        raise HTTPException(status_code=400, detail="bad_secret_ref")
    events = _events(body.events)
    decision = compliance_gate(
        action="pipeline.webhook.register",
        metadata={"task_id": "pipeline.webhook.register", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision)
    sub = WebhookSubscription(
        id=str(uuid4()),
        space_id=body.space_id,
        url=body.url,
        secret_ref=body.secret_ref,
        events=events,
        include_values=body.include_values,
    )
    _store(request.app).add(sub)
    return sub.public()


@router.get("/webhooks")
def list_webhooks(
    request: Request,
    spaces: SpaceStoreDep,
    cortex: CortexDep,
    space_id: str = Query(min_length=1, max_length=128),
) -> dict[str, Any]:
    _known_space(spaces, space_id)
    decision = compliance_gate(
        action="pipeline.webhook.read",
        metadata={"task_id": "pipeline.webhook.read", "space_id": space_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    rows = _store(request.app).list_for_space(space_id)
    return {"space_id": space_id, "subscriptions": [row.public() for row in rows]}


@router.post("/bench")
def run_bench(
    body: BenchIn,
    request: Request,
    spaces: SpaceStoreDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    """No declared cases. Does not report WRONG=0 for a bench that did not run."""
    _known_space(spaces, body.space_id)
    decision = compliance_gate(
        action="pipeline.bench.run",
        metadata={"task_id": "pipeline.bench.run", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision)
    run_id = str(uuid4())
    report: dict[str, Any] = {
        "space_id": body.space_id,
        "run_id": run_id,
        "outcome": "error",
        "code": "benchmark_not_configured",
        "wrong": None,
        "correct": 0,
        "cases": 0,
        "route": "pipeline.bench",
    }
    dispatch(
        request.app,
        event="benchmark.finished",
        space_id=body.space_id,
        outcome="error",
        route="pipeline.bench",
        run_id=run_id,
    )
    return report


@router.post("/semantic/import")
def semantic_import(
    body: ImportIn,
    spaces: SpaceStoreDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    """dbt sync target. Dry run plans. A real import waits on FEAT-SEMANTIC-IMPORT-01."""
    _known_space(spaces, body.space_id)
    decision = compliance_gate(
        action="pipeline.semantic.import",
        metadata={"task_id": "pipeline.semantic.import", "space_id": body.space_id},
        client=cortex,
    )
    enforce(decision)
    if body.dry_run:
        return {
            "outcome": "served",
            "route": "pipeline.semantic_import",
            "dry_run": True,
            "space_id": body.space_id,
        }
    raise HTTPException(
        status_code=501,
        detail={
            "code": "semantic_import_not_implemented",
            "message": "FEAT-SEMANTIC-IMPORT-01 is not merged",
        },
    )


@router.get("/proof")
def fetch_proof(
    request: Request,
    spaces: SpaceStoreDep,
    cortex: CortexDep,
    space_id: str = Query(min_length=1, max_length=128),
) -> dict[str, Any]:
    """Last pipeline proof for the Space: ids and outcome, never row values."""
    _known_space(spaces, space_id)
    decision = compliance_gate(
        action="pipeline.proof.read",
        metadata={"task_id": "pipeline.proof.read", "space_id": space_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    found = _store(request.app).latest_proof(space_id)
    if found is None:
        return {
            "space_id": space_id,
            "event": None,
            "outcome": "error",
            "route": "pipeline.proof",
            "code": "proof_not_found",
            "audit_id": None,
            "run_id": None,
        }
    return found
