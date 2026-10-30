"""Spaces — scope objects: what a question is allowed to see.

Backed by the demo-core memory store (Postgres repos parked P-DMS-2). Creation is
therefore in-process and disappears on restart; ``persisted`` on the response says
so rather than letting the UI imply a Space survived a redeploy that it did not.
"""

from __future__ import annotations

from typing import Any, Literal

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep, SpaceStoreDep, StoreBindingDep
from dms_api.gatekeeping import enforce
from dms_api.settings import Settings
from dms_api.wiring import (
    measure_detail,
    measures_confirm,
    measures_create,
    measures_list,
    measures_propose,
    measures_reject,
    space_ontology,
    space_ontology_rederive,
)

router = APIRouter(prefix="/v1/spaces", tags=["spaces"])


class SpaceOut(BaseModel):
    id: str
    name: str
    source_count: int
    member_count: int


class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.get("")
def list_spaces(store: SpaceStoreDep, binding: StoreBindingDep) -> dict[str, Any]:
    spaces = [
        SpaceOut(
            id=s.id,
            name=s.name,
            source_count=s.source_count,
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
        source_count=s.source_count,
        member_count=s.member_count,
    )


@router.get("/{space_id}/sources")
def space_sources(space_id: str, store: SpaceStoreDep, settings: SettingsDep) -> dict[str, Any]:
    """Sources attached to this Space — the concrete answer to "what can it see"."""
    if store.get(space_id) is None:
        raise HTTPException(status_code=404, detail="space_not_found")
    from dms_api.routes.library import list_sources

    sources = [s for s in list_sources(settings) if s.get("space_id") == space_id]
    return {"space_id": space_id, "sources": sources, "count": len(sources)}


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



# --- ONTO-CONFIRM-01 (dms#283): the steward workflow for measures ---------------
#
# Every mutation below runs in this order, and the order is the contract:
#   1. 404 for a Space this API does not know (before anything is said about roles);
#   2. the steward role, read from SERVER configuration (DR-0004: a request cannot name
#      its role; the identity-header middleware already refuses the headers);
#   3. compliance_gate -> enforce (Cortex F5, fail closed: unknown task, unreachable
#      gate and denial are all 403, and nothing has been written);
#   4. Cortex present (the ledger is the one audit trail; confirm and reject write to it);
#   5. the wiring function, which is the only thing that touches the store.
# The bodies forbid unknown fields, so a request that tries to name its own actor,
# tenant, role, state, source, SQL or expression is a 422 and stores nothing.

#: Roles that may decide a measure. Anything else, including a typo, is a denial.
STEWARD_ROLES = frozenset({"steward", "admin"})

_MEASURES = "/{space_id}/ontology/measures"


# Each body extends BaseModel directly and forbids extras: no field that was not declared
# is accepted, and the actor-trust invariant scan reads these classes by their own bases.


class MeasureProposeIn(BaseModel):
    """The proposer takes no input: it reads the Space's verified snapshot."""

    model_config = ConfigDict(extra="forbid")


class MeasureIn(BaseModel):
    """Manual entry. Creates a PROPOSED measure; confirming is a second, separate act.

    The length bounds only stop absurd input; the rules (name shape, aggregate enum,
    description copy) belong to the validator, which answers by code.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
    grain: str = Field(max_length=300)
    aggregate: str = Field(max_length=40)
    column: str = Field(default="*", max_length=300)
    description: str = Field(max_length=1000)


class MeasureConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_hash: str = Field(max_length=128)
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class MeasureRejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(max_length=1000)


def _known_space(space_id: str, store: SpaceStoreDep) -> None:
    if store.get(space_id) is None:
        raise HTTPException(status_code=404, detail=measure_detail("space_not_found"))


def _require_steward(settings: Settings) -> str:
    """Refuse unless the configured actor's role may decide measures. Returns the role.

    This reads configuration, not the request, and decides no policy: the compliance
    decision is Cortex's (``compliance_gate``, called in each route). This only keeps a
    read-only deployment from offering a Confirm that works.
    """
    role = settings.dms_actor_role
    if role not in STEWARD_ROLES:
        raise HTTPException(status_code=403, detail=measure_detail("steward_role_required"))
    return role


def _need_cortex(cortex: CortexDep) -> None:
    if cortex is None:
        raise HTTPException(status_code=503, detail=measure_detail("ledger_unavailable"))


def _meta(task_id: str, space_id: str, role: str, measure_id: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"task_id": task_id, "space_id": space_id, "actor_role": role}
    if measure_id is not None:
        out["measure_id"] = measure_id
    return out


@router.get(_MEASURES)
def list_space_measures(
    space_id: str, store: SpaceStoreDep, settings: SettingsDep
) -> dict[str, Any]:
    """Every measure of the Space with its state, evidence and why it can or cannot answer.

    No gate, like ``GET .../ontology``: it writes nothing. A viewer gets counts and
    booleans; the probe SQL and value are removed unless this deployment's actor may decide.
    """
    _known_space(space_id, store)
    role = settings.dms_actor_role
    return measures_list(space_id, role=role, can_decide=role in STEWARD_ROLES)


@router.post(f"{_MEASURES}/propose")
def propose_space_measures(
    space_id: str,
    store: SpaceStoreDep,
    cortex: CortexDep,
    settings: SettingsDep,
    body: MeasureProposeIn | None = None,
) -> dict[str, Any]:
    """Propose measures from the verified snapshot. Idempotent; never confirms anything."""
    _known_space(space_id, store)
    role = _require_steward(settings)
    task = "spaces.ontology.measures.propose"
    decision = compliance_gate(
        action=task,
        actor=settings.dms_actor_user_id,
        metadata=_meta(task, space_id, role),
        client=cortex,
    )
    enforce(decision)
    _need_cortex(cortex)
    return measures_propose(space_id, actor=settings.dms_actor_user_id)


@router.post(_MEASURES)
def create_space_measure(
    space_id: str,
    body: MeasureIn,
    response: Response,
    store: SpaceStoreDep,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Manual entry. Same validator and lake evidence as the proposer; PROPOSED only."""
    _known_space(space_id, store)
    role = _require_steward(settings)
    task = "spaces.ontology.measures.create"
    decision = compliance_gate(
        action=task,
        actor=settings.dms_actor_user_id,
        metadata=_meta(task, space_id, role),
        client=cortex,
    )
    enforce(decision)
    _need_cortex(cortex)
    out = measures_create(
        space_id,
        name=body.name,
        grain=body.grain,
        aggregate=body.aggregate,
        column=body.column,
        description=body.description,
        actor=settings.dms_actor_user_id,
    )
    response.status_code = 201 if out.get("created") else 200
    return out


@router.post(f"{_MEASURES}/{{measure_id}}/confirm")
def confirm_space_measure(
    space_id: str,
    measure_id: str,
    body: MeasureConfirmIn,
    store: SpaceStoreDep,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Confirm one proposed (or previously rejected) measure. All criteria come back together."""
    _known_space(space_id, store)
    role = _require_steward(settings)
    task = "spaces.ontology.measures.confirm"
    decision = compliance_gate(
        action=task,
        actor=settings.dms_actor_user_id,
        metadata=_meta(task, space_id, role, measure_id),
        client=cortex,
    )
    enforce(decision)
    _need_cortex(cortex)
    return measures_confirm(
        space_id,
        measure_id,
        definition_hash=body.definition_hash,
        name=body.name,
        description=body.description,
        actor=settings.dms_actor_user_id,
        cortex=cortex,
    )


@router.post(f"{_MEASURES}/{{measure_id}}/reject")
def reject_space_measure(
    space_id: str,
    measure_id: str,
    body: MeasureRejectIn,
    store: SpaceStoreDep,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Reject a proposed measure, or withdraw a confirmed one. A reason is mandatory."""
    _known_space(space_id, store)
    role = _require_steward(settings)
    task = "spaces.ontology.measures.reject"
    decision = compliance_gate(
        action=task,
        actor=settings.dms_actor_user_id,
        metadata=_meta(task, space_id, role, measure_id),
        client=cortex,
    )
    enforce(decision)
    _need_cortex(cortex)
    return measures_reject(
        space_id,
        measure_id,
        reason=body.reason,
        actor=settings.dms_actor_user_id,
        cortex=cortex,
    )
