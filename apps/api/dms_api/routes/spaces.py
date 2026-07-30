"""Spaces list — demo-core memory store (Postgres repos parked P-DMS-2)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dms_api.deps import SpaceStoreDep

router = APIRouter(prefix="/v1/spaces", tags=["spaces"])


class SpaceOut(BaseModel):
    id: str
    name: str
    source_count: int
    member_count: int


@router.get("")
def list_spaces(store: SpaceStoreDep) -> list[SpaceOut]:
    return [
        SpaceOut(
            id=s.id,
            name=s.name,
            source_count=s.source_count,
            member_count=s.member_count,
        )
        for s in store.list_spaces()
    ]


@router.get("/{space_id}")
def get_space(space_id: str, store: SpaceStoreDep) -> SpaceOut:
    s = store.get(space_id)
    if s is None:
        raise HTTPException(status_code=404, detail="space_not_found")
    return SpaceOut(
        id=s.id,
        name=s.name,
        source_count=s.source_count,
        member_count=s.member_count,
    )
