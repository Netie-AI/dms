"""Leave-machine / mutation compliance gate.

Every FastAPI route that performs a mutation must call ``compliance_gate``
before side effects. Enforced by ``tests/invariants/test_boundaries.py``.
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
) -> ComplianceDecision:
    """Skeleton gate — always allows in T0; OpenVault wiring comes later."""
    _ = (actor, resource, metadata)
    return ComplianceDecision(allowed=True, reason="t0-allow", action=action)
