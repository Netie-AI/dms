"""Thin HTTP client — DMS talks to Cortex as a service, not a library."""

from __future__ import annotations

from typing import Any

import httpx

from cortex_client.models import (
    AnswerRequest,
    AnswerResponse,
    HealthResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
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

    def health(self) -> HealthResponse:
        r = self._client.get("/health")
        r.raise_for_status()
        body = HealthResponse.model_validate(r.json())
        major = int(str(body.contract).split(".", 1)[0])
        if major != CONTRACT_MAJOR:
            raise RuntimeError(
                f"cortex-contract major mismatch: got {body.contract}, need {CONTRACT_MAJOR}.x"
            )
        return body

    def answer(self, req: AnswerRequest) -> AnswerResponse:
        r = self._client.post("/v1/answer", json=req.model_dump())
        r.raise_for_status()
        return AnswerResponse.model_validate(r.json())

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        r = self._client.post("/v1/ledger/append", json=req.model_dump())
        r.raise_for_status()
        return LedgerAppendResponse.model_validate(r.json())
