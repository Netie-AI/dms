"""Pydantic request/response shapes for cortex-contract 1.1.0 wire calls.

Manifest / SubmitRequest / QueryResult live in ``cortex_contract`` — import those
from the pinned package, do not duplicate.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str
    space_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None


class AskResponse(BaseModel):
    answer: str | None = None
    abstained: bool = False
    receipt_id: str | None = None
    badge: str | None = None
    values: list[dict[str, Any]] = Field(default_factory=list)


class LedgerAppendRequest(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None


class LedgerAppendResponse(BaseModel):
    entry_id: str
    hash: str


class LedgerVerifyResponse(BaseModel):
    ok: bool = False
    first_break: str | None = None
    checked: int | None = None


class ToolRegistryResponse(BaseModel):
    tools: list[dict[str, Any]] = Field(default_factory=list)


# Deprecated aliases kept for packages/ledger until call sites migrate
AnswerRequest = AskRequest
AnswerResponse = AskResponse
HealthResponse = AskResponse  # unused; prefer Cortex /health outside contract
