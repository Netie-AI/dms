"""HTTP-only Cortex client.

Import paths are stable. Method bodies raise until regenerated from Cortex's
published ``contract/openapi-1.0.0.json`` via ``just sync-contract`` (R1).
DMS must never ``import CortexOS`` — only this package over HTTP.
Pinned to cortex-contract major 1.
"""

from __future__ import annotations

from cortex_client.client import CortexClient
from cortex_client.gate import ComplianceDecision, compliance_gate
from cortex_client.models import (
    AnswerRequest,
    AnswerResponse,
    HealthResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
)

__all__ = [
    "CortexClient",
    "ComplianceDecision",
    "compliance_gate",
    "AnswerRequest",
    "AnswerResponse",
    "HealthResponse",
    "LedgerAppendRequest",
    "LedgerAppendResponse",
]

CONTRACT_VERSION = "1.0.0"
