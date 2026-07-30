"""HTTP client for cortex-contract 1.1.0 — five operationIds only.

Generated-shaped module under ``cortex_client``; regenerate via
``python scripts/sync_contract.py`` after Cortex publishes a new minor.
Talks to Cortex over HTTP only — never imports CortexOS.
"""

from __future__ import annotations

from typing import Any

import httpx
from cortex_contract.execution import Manifest, QueryResult, SubmitRequest

from cortex_client.models import (
    AskRequest,
    AskResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
    LedgerVerifyResponse,
    ToolRegistryResponse,
)

CONTRACT_MAJOR = 1


class CortexClient:
    """Pin base_url to a Cortex image/tag via compose; contract major must be 1."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CortexClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def ask(self, req: AskRequest) -> AskResponse:
        r = self._client.post("/v1/contract/ask", json=req.model_dump(mode="json"))
        r.raise_for_status()
        return AskResponse.model_validate(r.json())

    def submit(self, req: SubmitRequest) -> QueryResult:
        r = self._client.post("/v1/contract/submit", json=req.model_dump(mode="json"))
        r.raise_for_status()
        return QueryResult.model_validate(r.json())

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        r = self._client.post(
            "/v1/contract/ledger/append", json=req.model_dump(mode="json")
        )
        r.raise_for_status()
        return LedgerAppendResponse.model_validate(r.json())

    def verify_ledger(self) -> LedgerVerifyResponse:
        r = self._client.post("/v1/contract/ledger/verify", json={})
        r.raise_for_status()
        return LedgerVerifyResponse.model_validate(r.json())

    def tool_registry(self) -> ToolRegistryResponse:
        r = self._client.get("/v1/contract/tools")
        r.raise_for_status()
        return ToolRegistryResponse.model_validate(r.json())


# Back-compat aliases used by T0 smoke / ledger facade
class LedgerAppendRequestLegacy(LedgerAppendRequest):
    pass
