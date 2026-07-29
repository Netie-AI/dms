"""HTTP-only Cortex client.

Generated from ``contract/openapi-1.0.0.json`` (hand-maintained stub for T0).
DMS must never ``import CortexOS`` — only this client over HTTP.
Pinned to cortex-contract major 1.
"""

from __future__ import annotations

from cortex_client.client import CortexClient
from cortex_client.models import (
    AnswerRequest,
    AnswerResponse,
    HealthResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
)

__all__ = [
    "CortexClient",
    "AnswerRequest",
    "AnswerResponse",
    "HealthResponse",
    "LedgerAppendRequest",
    "LedgerAppendResponse",
]

CONTRACT_VERSION = "1.0.0"
