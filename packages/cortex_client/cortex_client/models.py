"""Pydantic request/response shapes for cortex-contract 1.1.0 wire calls.

Manifest / SubmitRequest / QueryResult live in ``cortex_contract`` — import those
from the pinned package, do not duplicate.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    question: str
    space_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None


class AskResponse(BaseModel):
    """Facade over contract Answer — accepts both flat and nested shapes."""

    model_config = ConfigDict(extra="ignore")

    answer: str | None = None
    abstained: bool = False
    receipt_id: str | None = None
    badge: str | None = None
    values: list[dict[str, Any]] = Field(default_factory=list)
    sql_used: str | None = None
    assumptions: str | list[str] | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    route: str | None = None
    contributing_sources: list[dict[str, Any]] = Field(default_factory=list)
    audit_id: str | None = None

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> AskResponse:  # type: ignore[override]
        if not isinstance(obj, dict):
            return super().model_validate(obj, **kwargs)
        data = dict(obj)
        # Contract Answer: answer/audit_id/provenance/route/rows/sql_used/suggestions
        prov = data.get("provenance")
        if isinstance(prov, dict):
            data.setdefault("badge", prov.get("badge"))
            assumptions = prov.get("assumptions")
            if assumptions is not None:
                data.setdefault("assumptions", assumptions)
        if data.get("audit_id") and not data.get("receipt_id"):
            data["receipt_id"] = data["audit_id"]
        # Flatten row items that may be {additional_properties: ...}
        rows = data.get("rows")
        if isinstance(rows, list):
            flat: list[dict[str, Any]] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                if "additional_properties" in r and isinstance(r["additional_properties"], dict):
                    flat.append(dict(r["additional_properties"]))
                else:
                    # strip unset metadata keys
                    flat.append({k: v for k, v in r.items() if k != "additional_properties"})
            data["rows"] = flat
        route = data.get("route")
        if route in ("abstain", "blocked") or (
            isinstance(data.get("badge"), str) and data["badge"].lower() in ("abstain", "blocked")
        ):
            data["abstained"] = True
        return super().model_validate(data, **kwargs)


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
