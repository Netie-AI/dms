"""Spaces — scope objects: what a question is allowed to see.

Backed by the demo-core memory store (Postgres repos parked P-DMS-2). Creation is
therefore in-process and disappears on restart; ``persisted`` on the response says
so rather than letting the UI imply a Space survived a redeploy that it did not.
"""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep, SettingsDep, SpaceStoreDep, StoreBindingDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import space_source_pull_counts

router = APIRouter(prefix="/v1/spaces", tags=["spaces"])


class SpaceOut(BaseModel):
    id: str
    name: str
    source_count: int
    member_count: int


class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _canonical(space_id: str) -> str:
    from dms_api.wiring import canonical_space_id

    return canonical_space_id(space_id)


def _source_count(record_count: int, space_id: str, pulls: dict[str, int]) -> int:
    """Store-registered sources plus SQL-source tables landed into this Space.

    The stores count ``dms.data_sources`` (or a static seed). A SQL-source ingest
    records its tables in the bronze ingest registry, which neither store read,
    so a Space with 75 landed tables reported ``source_count: 0``.
    """
    return int(record_count) + pulls.get(_canonical(space_id), 0)


@router.get("")
def list_spaces(store: SpaceStoreDep, binding: StoreBindingDep) -> dict[str, Any]:
    pulls = space_source_pull_counts()
    spaces = [
        SpaceOut(
            id=s.id,
            name=s.name,
            source_count=_source_count(s.source_count, s.id, pulls),
            member_count=s.member_count,
        ).model_dump()
        for s in store.list_spaces()
    ]
    return {
        "spaces": spaces,
        "persisted": binding.persistent,
        "storage": binding.as_dict(),
        "hint": binding.hint,
    }


@router.post("", status_code=201)
def create_space(
    body: SpaceCreate,
    store: SpaceStoreDep,
    settings: SettingsDep,
    cortex: CortexDep,
    binding: StoreBindingDep,
) -> dict[str, Any]:
    # A Space is a scope object: creating one defines what a future question may
    # see, so it goes through the gate like every other mutation on this API.
    decision = compliance_gate(
        action="space.create",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "space.create", "name": body.name},
        client=cortex,
    )
    enforce(decision)

    try:
        record = store.create(body.name.strip())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "space": SpaceOut(
            id=record.id,
            name=record.name,
            source_count=record.source_count,
            member_count=record.member_count,
        ).model_dump(),
        # From the store that actually bound, never from the setting: with
        # DATABASE_URL set and Postgres unreachable this used to answer
        # persisted=true for a Space living in process memory.
        "persisted": binding.persistent,
        "hint": binding.hint,
        "storage": binding.as_dict(),
    }


@router.get("/{space_id}")
def get_space(space_id: str, store: SpaceStoreDep) -> SpaceOut:
    s = store.get(space_id)
    if s is None:
        raise HTTPException(status_code=404, detail="space_not_found")
    return SpaceOut(
        id=s.id,
        name=s.name,
        source_count=_source_count(s.source_count, s.id, space_source_pull_counts()),
        member_count=s.member_count,
    )


@router.get("/{space_id}/sources")
def space_sources(space_id: str, store: SpaceStoreDep, settings: SettingsDep) -> dict[str, Any]:
    """Sources attached to this Space — the concrete answer to "what can it see".

    This used to call the ``/v1/library/sources`` *endpoint function* directly,
    so its ``space_id`` parameter kept its FastAPI default - the ``Query(None)``
    FieldInfo object, which is truthy. With ``DATABASE_URL`` set, that reached
    ``UUID(<FieldInfo>)`` and every call answered 500. The helper takes a plain
    ``space_id`` and filters in the store query.
    """
    if store.get(space_id) is None:
        raise HTTPException(status_code=404, detail="space_not_found")
    from dms_api.routes.library import _list_sources

    sources = _list_sources(settings, space_id=space_id)
    truncated = [s for s in sources if s.get("truncated")]
    return {
        "space_id": space_id,
        "sources": sources,
        "count": len(sources),
        "truncated_count": len(truncated),
    }
