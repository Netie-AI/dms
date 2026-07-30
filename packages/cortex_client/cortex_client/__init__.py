"""HTTP-only Cortex client (cortex-contract major 1).

DMS may import ``cortex_contract`` (models + canonical_manifest_bytes) but must
never ``import CortexOS``. Gate is call-through only — F5 lives in Cortex.
"""

from __future__ import annotations

from cortex_client.client import CortexClient
from cortex_client.gate import ComplianceDecision, compliance_gate
from cortex_client.models import (
    AnswerRequest,
    AnswerResponse,
    AskRequest,
    AskResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
    LedgerVerifyResponse,
    ToolRegistryResponse,
)

__all__ = [
    "CortexClient",
    "ComplianceDecision",
    "compliance_gate",
    "AskRequest",
    "AskResponse",
    "AnswerRequest",
    "AnswerResponse",
    "LedgerAppendRequest",
    "LedgerAppendResponse",
    "LedgerVerifyResponse",
    "ToolRegistryResponse",
]

CONTRACT_VERSION = "1.1.0"
