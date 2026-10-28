"""Ontology — the shared vocabulary the product is built on, read from Cortex.

DMS renders the ontology; it never authors it. Object types, links, governed
actions and functions live as YAML in the engine's pack and are the same
definitions the answer plane routes against — a second copy here would be a
second truth, and the first thing to drift.

Cortex offline is a normal state, not an error: every route answers 200 with
``ok: false`` and a hint so the page can explain itself instead of blanking.
"""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from dms_core.control_plane.onto_data import _public_item, ontology_data_store
from fastapi import APIRouter, HTTPException

from dms_api.cortex_read import cortex_get
from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/ontology", tags=["ontology"])

#: DMS path suffix → Cortex path suffix. An allowlist, not a wildcard proxy:
#: an open proxy to the engine would defeat the port isolation in §1.
SECTIONS: dict[str, str] = {
    "": "",
    "objects": "/objects",
    "links": "/links",
    "actions": "/actions",
    "functions": "/functions",
    "metrics": "/metrics",
    "graph": "/graph",
}


def _fetch(settings: SettingsDep, section: str, pack: str | None) -> dict[str, Any]:
    suffix = SECTIONS[section]
    result = cortex_get(
        settings.cortex_url,
        f"/dms/ontology{suffix}",
        api_key=settings.cortex_api_key,
        params={"pack": pack} if pack else None,
    )
    if result["ok"]:
        return {"ok": True, "section": section or "summary", **(result["data"] or {})}
    return {
        "ok": False,
        "section": section or "summary",
        "error": result.get("error"),
        "hint": result.get("hint"),
    }


@router.get("")
def ontology_summary(settings: SettingsDep, pack: str | None = None) -> dict[str, Any]:
    return _fetch(settings, "", pack)


@router.get("/graph")
def ontology_graph(settings: SettingsDep, pack: str | None = None) -> dict[str, Any]:
    return _fetch(settings, "graph", pack)


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
) -> dict[str, Any]:
    if section not in SECTIONS or section == "":
        raise HTTPException(status_code=404, detail=f"unknown ontology section: {section}")
    return _fetch(settings, section, pack)
