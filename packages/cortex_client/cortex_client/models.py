"""Pydantic models mirroring cortex-contract 1.0.0 OpenAPI schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    contract: str
    engine: str


class AnswerRequest(BaseModel):
    question: str
    space_id: str | None = None
    tenant_id: str | None = None


class AnswerResponse(BaseModel):
    answer: str | None = None
    abstained: bool = False
    receipt_id: str | None = None


class LedgerAppendRequest(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None


class LedgerAppendResponse(BaseModel):
    entry_id: str
    hash: str
