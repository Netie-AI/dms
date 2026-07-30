"""Compliance / F5 gate — call-through only.

DMS holds no policy logic. F5 lives in Cortex. This module POSTs to the Cortex
gate/ask path via ``CortexClient`` once wired; until then it fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ComplianceDecision:
    allowed: bool
    reason: str
    action: str


def compliance_gate(
    *,
    action: str,
    actor: str | None = None,
    resource: str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
) -> ComplianceDecision:
    """Call through to Cortex F5. DMS must not evaluate allow/deny locally."""
    _ = (actor, resource, metadata)
    if client is None:
        # Fail closed until API injects a live CortexClient (T4 amend path).
        return ComplianceDecision(
            allowed=False,
            reason="gate_unavailable",
            action=action,
        )
    # Future: client-specific F5 endpoint. Keep signature stable.
    return ComplianceDecision(allowed=False, reason="gate_unavailable", action=action)
