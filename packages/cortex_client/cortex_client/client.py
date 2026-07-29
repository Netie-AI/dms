"""Thin HTTP client — DMS talks to Cortex as a service, not a library.

Method bodies are stubs until ``just sync-contract`` regenerates this package
from Cortex's published ``contract/openapi-1.0.0.json``.
"""

from __future__ import annotations

from typing import Any

from cortex_client.models import (
    AnswerRequest,
    AnswerResponse,
    HealthResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
)

CONTRACT_MAJOR = 1

_STUB_MSG = (
    "generated from Cortex contract/openapi-1.0.0.json — "
    "run just sync-contract once Cortex R1 publishes it"
)


class CortexClient:
    """Pin base_url to a Cortex image/tag via compose; contract major must be 1."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    def close(self) -> None:
        raise NotImplementedError(_STUB_MSG)

    def __enter__(self) -> CortexClient:
        raise NotImplementedError(_STUB_MSG)

    def __exit__(self, *args: Any) -> None:
        raise NotImplementedError(_STUB_MSG)

    def health(self) -> HealthResponse:
        raise NotImplementedError(_STUB_MSG)

    def answer(self, req: AnswerRequest) -> AnswerResponse:
        raise NotImplementedError(_STUB_MSG)

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        raise NotImplementedError(_STUB_MSG)

    def verify_ledger(self) -> dict[str, Any]:
        """Call Cortex chain verification; report first break in the response."""
        raise NotImplementedError(_STUB_MSG)
