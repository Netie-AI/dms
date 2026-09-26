"""Spaces — scope objects: what a question is allowed to see.

Backed by the demo-core memory store (Postgres repos parked P-DMS-2). Creation is
therefore in-process and disappears on restart; ``persisted`` on the response says
so rather than letting the UI imply a Space survived a redeploy that it did not.
"""

from __future__ import annotations

from typing import Any, Literal

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep, SettingsDep, SpaceStoreDep, StoreBindingDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import (
    space_ontology,
    space_ontology_rederive,
    space_source_pull_counts,
)

router = APIRouter(prefix="/v1/spaces", tags=["spaces"])


class SpaceOut(BaseModel):
    id: str
    name: str
    source_count: int
    member_count: int
    #: Set only when the warehouse could not be read (``warehouse_unavailable``):
    #: ``source_count`` then omits SQL-source tables, and says so.
    degraded: dict[str, str] | None = None


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
    pulls, degraded = space_source_pull_counts()
    spaces = [
        SpaceOut(
            id=s.id,
            name=s.name,
            source_count=_source_count(s.source_count, s.id, pulls),
            member_count=s.member_count,
        ).model_dump(exclude_none=True)
        for s in store.list_spaces()
    ]
    out: dict[str, Any] = {
        "spaces": spaces,
        "persisted": binding.persistent,
        "storage": binding.as_dict(),
        "hint": binding.hint,
    }
    if degraded:
        out["degraded"] = degraded
    return out


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
        ).model_dump(exclude_none=True),
        # From the store that actually bound, never from the setting: with
        # DATABASE_URL set and Postgres unreachable this used to answer
        # persisted=true for a Space living in process memory.
        "persisted": binding.persistent,
        "hint": binding.hint,
        "storage": binding.as_dict(),
    }


@router.get("/{space_id}", response_model_exclude_none=True)
def get_space(space_id: str, store: SpaceStoreDep) -> SpaceOut:
    s = store.get(space_id)
    if s is None:
        raise HTTPException(status_code=404, detail="space_not_found")
    pulls, degraded = space_source_pull_counts()
    return SpaceOut(
        id=s.id,
        name=s.name,
        source_count=_source_count(s.source_count, s.id, pulls),
        member_count=s.member_count,
        degraded=degraded,
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
    from dms_api.routes.library import _list_sources_status

    sources, degraded = _list_sources_status(settings, space_id=space_id)
    truncated = [s for s in sources if s.get("truncated")]
    out: dict[str, Any] = {
        "space_id": space_id,
        "sources": sources,
        "count": len(sources),
        "truncated_count": len(truncated),
    }
    if degraded:
        out["degraded"] = degraded
    return out


# --- ONTO-DERIVE-01 (dms#277): the Space's own ontology, shown honestly --------


class OntologySourceIn(BaseModel):
    """A source connection for a keys-only catalog read. Never stored, never echoed."""

    kind: Literal["sqlserver", "mysql", "postgresql"]
    host: str
    database: str
    user: str
    password: str = Field(default="", max_length=256)
    port: int | None = None
    encrypt: bool = True
    trust_server_certificate: bool = False


class OntologyDeriveIn(BaseModel):
    source: OntologySourceIn | None = None


@router.get("/{space_id}/ontology")
def get_space_ontology(space_id: str, store: SpaceStoreDep) -> dict[str, Any]:
    """Objects and links with verified/unverified status and the violation that failed."""
    if store.get(space_id) is None:
        raise HTTPException(status_code=404, detail="space not found")
    return space_ontology(space_id)


@router.post("/{space_id}/ontology/derive")
def derive_space_ontology(
    space_id: str,
    store: SpaceStoreDep,
    cortex: CortexDep,
    settings: SettingsDep,
    body: OntologyDeriveIn | None = None,
) -> dict[str, Any]:
    """Re-derive and re-verify an existing Space's ontology. Reads keys, never rows."""
    if store.get(space_id) is None:
        raise HTTPException(status_code=404, detail="space not found")
    decision = compliance_gate(
        action="spaces.ontology.derive",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "spaces.ontology.derive", "space_id": space_id},
        client=cortex,
    )
    enforce(decision)
    source = body.source.model_dump() if body is not None and body.source is not None else None
    return space_ontology_rederive(space_id, source=source)
