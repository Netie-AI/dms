"""Skeleton mutation route — proves compliance_gate wiring for invariants."""

from __future__ import annotations

from dms_core import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class PingBody(BaseModel):
    message: str = "ping"


@router.post("/v1/skeleton/ping")
def skeleton_ping(body: PingBody) -> dict[str, str]:
    decision = compliance_gate(action="skeleton.ping", metadata={"message": body.message})
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    return {"echo": body.message, "gate": decision.reason}
