"""Compliance / F5 gate — call-through only.

DMS holds no policy logic. F5 lives in Cortex. This module is a pure HTTP
call-through to the Cortex gate endpoint once ``just sync-contract`` has
regenerated the client from the published OpenAPI spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_STUB_MSG = (
    "generated from Cortex contract/openapi-1.0.0.json — "
    "run just sync-contract once Cortex R1 publishes it"
)


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
    """Call through to Cortex F5. DMS must not evaluate allow/deny locally."""
    _ = (action, actor, resource, metadata)
    raise NotImplementedError(_STUB_MSG)
