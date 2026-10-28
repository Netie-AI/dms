"""Compliance / F5 gate — HTTP call-through to Cortex only.

F5 lives at POST /dms/tasks/choose (not on contract 1.1 allowlist). Choose records
the task event and returns Cortex's verdict in one call. The older gate/check route
expects an event that already exists, so a mutation's random event id was unknown to
it, and gate/check accepted that id as a pass without recording anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

    task_id = str(meta.get("task_id") or action)
    filled = dict(meta.get("filled_template") or {})
    if resource:
        filled.setdefault("resource", resource)
    payload = {
        "task_id": task_id,
        "filled_template": filled,
        "actor": actor or "user",
        # Choose defaults to accepted=true, which records a suggestion acceptance.
        # A mutation is not a suggestion, so only the event and the verdict are wanted.
        "accepted": False,
    }
    headers: dict[str, str] = {}
    key = getattr(client, "api_key", None)
    if key:
        headers["X-API-Key"] = str(key)
    try:
        with httpx.Client(base_url=str(base).rstrip("/"), timeout=10.0) as http:
            r = http.post(
                "/dms/tasks/choose",
                json=payload,
                headers=headers or None,
            )
    except httpx.HTTPError:
        return ComplianceDecision(allowed=False, reason="gate_unavailable", action=action)

    if r.status_code >= 500:
        return ComplianceDecision(allowed=False, reason="gate_unavailable", action=action)
    if r.status_code == 404:
        # Task catalog miss — fail closed for mutations; chat.ask may soft-fallback
        return ComplianceDecision(allowed=False, reason="gate_task_unknown", action=action)
    if r.status_code >= 400:
        return ComplianceDecision(allowed=False, reason="gate_refused", action=action)

    body = r.json() if r.content else {}
    # Choose wraps the verdict; a bare {"status", "executable"} body is read as-is.
    verdict = body.get("verdict") if isinstance(body.get("verdict"), dict) else body
    status = str(verdict.get("status") or "").lower()
    executable = verdict.get("executable")
    allowed = executable is True or status in {"ok", "allowed", "pass", "passed"}
    reason = status or ("allowed" if allowed else "denied")
    return ComplianceDecision(
        allowed=allowed,
        reason=reason,
        action=action,
        event_id=body.get("event_id") or verdict.get("event_id"),
    )
