"""Ontology the product renders, read from the Space's own store.

DMS does not proxy Cortex ``/dms/ontology`` (that route is gone). Object types,
links, and approved measures come from ``space_ontology`` plus the approved
onto_data items for the Space. When that Space has neither, the response is the
pack ``ontology_spine.yaml`` and ``source`` is ``pack``.

``pack`` stays on the signature so older clients and the MCP metrics tool keep
calling the same function. It does not select a Cortex pack.
"""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from dms_core.control_plane.onto_data import _public_item, ontology_data_store
from fastapi import APIRouter, HTTPException

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import read_live_ontology

router = APIRouter(prefix="/v1/ontology", tags=["ontology"])

#: Allowlist, not a wildcard. An unknown section is 404.
SECTIONS = frozenset({"", "objects", "links", "actions", "functions", "metrics", "graph"})


def _read(section: str, *, space_id: str | None, pack: str | None) -> dict[str, Any]:
    _ = pack
    return read_live_ontology(section, space_id=space_id)


@router.get("")
def ontology_summary(
    settings: SettingsDep,
    pack: str | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    _ = settings
    return _read("summary", space_id=space_id, pack=pack)


@router.get("/graph")
def ontology_graph(
    settings: SettingsDep,
    pack: str | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    _ = settings
    return _read("graph", space_id=space_id, pack=pack)


def _require_steward(settings: SettingsDep) -> None:
    store = ontology_data_store()
    if not store.holds_steward_grant(
        role=settings.dms_actor_role, actor_id=settings.dms_actor_user_id
    ):
        raise HTTPException(status_code=403, detail="steward_grant_required")


@router.post("/data/compile")
def compile_ontology_data(
    settings: SettingsDep,
    cortex: CortexDep,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Store a ``compile_source`` proposal as proposed items. Never served."""
    _require_steward(settings)
    decision = compliance_gate(
        action="ontology.compile",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "ontology.compile", "space_id": space_id or ""},
        client=cortex,
    )
    enforce(decision)
    from dms_api.wiring import compile_ontology_proposal

    return compile_ontology_proposal(
        actor=settings.dms_actor_user_id,
        tenant_id=settings.dms_tenant_id,
        space_id=space_id,
    )


@router.get("/data/diff")
def diff_ontology_data(
    settings: SettingsDep,
    cortex: CortexDep,
    version_id: str | None = None,
) -> dict[str, Any]:
    """Proposed items in one version against every approved item."""
    _require_steward(settings)
    decision = compliance_gate(
        action="ontology.diff",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "ontology.diff"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return ontology_data_store().diff(version_id)


@router.post("/data/items/{item_id}/approve")
def approve_ontology_item(
    item_id: str,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    _require_steward(settings)
    decision = compliance_gate(
        action="ontology.approve",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "ontology.approve", "item_id": item_id},
        client=cortex,
    )
    enforce(decision)
    row = ontology_data_store().approve(item_id, actor=settings.dms_actor_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ontology_item_not_found")
    return _public_item(row)


@router.post("/data/items/{item_id}/revoke")
def revoke_ontology_item(
    item_id: str,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    _require_steward(settings)
    decision = compliance_gate(
        action="ontology.revoke",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "ontology.revoke", "item_id": item_id},
        client=cortex,
    )
    enforce(decision)
    row = ontology_data_store().revoke(item_id, actor=settings.dms_actor_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ontology_item_not_found")
    return _public_item(row)


@router.get("/{section}")
def ontology_section(
    section: str,
    settings: SettingsDep,
    pack: str | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    _ = settings
    if section not in SECTIONS or section == "":
        raise HTTPException(status_code=404, detail=f"unknown ontology section: {section}")
    return _read(section, space_id=space_id, pack=pack)
