"""Compliance / F5 gate — HTTP call-through to Cortex only.

F5 lives at POST /dms/tasks/gate/check (not on contract 1.1 allowlist).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx


@dataclass(frozen=True)
class ComplianceDecision:
    allowed: bool
    reason: str
    action: str
    event_id: str | None = None


def compliance_gate(
    *,
    action: str,
    actor: str | None = None,
    resource: str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
) -> ComplianceDecision:
    """Call through to Cortex F5. DMS must not evaluate allow/deny locally."""
    meta = metadata or {}
    if client is None:
        return ComplianceDecision(
            allowed=False,
            reason="gate_unavailable",
            action=action,
        )

    base = getattr(client, "base_url", None)
    if not base:
        return ComplianceDecision(allowed=False, reason="gate_unavailable", action=action)

    event_id = str(meta.get("event_id") or uuid4())
    task_id = str(meta.get("task_id") or action)
    filled = dict(meta.get("filled_template") or {})
    if resource:
        filled.setdefault("resource", resource)
    payload = {
        "event_id": event_id,
        "task_id": task_id,
        "filled_template": filled,
        "actor": actor or "user",
    }
    headers: dict[str, str] = {}
    key = getattr(client, "api_key", None)
    if key:
        headers["X-API-Key"] = str(key)
    unavailable = ComplianceDecision(
        allowed=False, reason="gate_unavailable", action=action, event_id=event_id
    )
    # Every way of reaching the gate and reading its answer that is not an answer fails closed
    # as gate_unavailable: a base_url httpx rejects (InvalidURL is not an HTTPError), a transport
    # error, and a body that is not JSON. The gate is shared by every mutation route, so none of
    # these may escape as a bare 500.
    try:
        with httpx.Client(base_url=str(base).rstrip("/"), timeout=10.0) as http:
            r = http.post(
                "/dms/tasks/gate/check",
                json=payload,
                headers=headers or None,
            )
    except Exception:  # noqa: BLE001 - fail closed on any failure to reach the gate
        return unavailable

    if r.status_code >= 500:
        return unavailable
    if r.status_code == 404:
        # Task catalog miss — fail closed for mutations; chat.ask may soft-fallback
        return ComplianceDecision(
            allowed=False, reason="gate_task_unknown", action=action, event_id=event_id
        )
    if r.status_code >= 400:
        return ComplianceDecision(
            allowed=False, reason="gate_refused", action=action, event_id=event_id
        )

    try:
        body = r.json() if r.content else {}
    except ValueError:  # a 2xx that is not JSON is not an answer the gate gave
        return unavailable
    if not isinstance(body, dict):  # a JSON list or scalar is not a decision either
        return unavailable
    status = str(body.get("status") or "").lower()
    executable = body.get("executable")
    allowed = executable is True or status in {"ok", "allowed", "pass", "passed"}
    reason = status or ("allowed" if allowed else "denied")
    return ComplianceDecision(
        allowed=allowed,
        reason=reason,
        action=action,
        event_id=body.get("event_id") or event_id,
    )
