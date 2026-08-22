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
    api_key = getattr(client, "api_key", None)
    if api_key:
        headers["X-API-Key"] = str(api_key)

    try:
        with httpx.Client(base_url=str(base).rstrip("/"), timeout=10.0) as http:
            r = http.post("/dms/tasks/gate/check", json=payload, headers=headers)
    except httpx.HTTPError:
        return ComplianceDecision(
            allowed=False, reason="gate_unavailable", action=action, event_id=event_id
        )

    if r.status_code >= 500:
        return ComplianceDecision(
            allowed=False, reason="gate_unavailable", action=action, event_id=event_id
        )
    if r.status_code == 404:
        # Task catalog miss — fail closed for mutations; chat.ask may soft-fallback
        return ComplianceDecision(
            allowed=False, reason="gate_task_unknown", action=action, event_id=event_id
        )
    if r.status_code >= 400:
        return ComplianceDecision(
            allowed=False, reason="gate_refused", action=action, event_id=event_id
        )

    body = r.json() if r.content else {}
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
