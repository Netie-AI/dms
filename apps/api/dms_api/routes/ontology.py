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

from fastapi import APIRouter, HTTPException

from dms_api.cortex_read import cortex_get
from dms_api.deps import SettingsDep

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


@router.get("/{section}")
def ontology_section(
    section: str,
    settings: SettingsDep,
    pack: str | None = None,
) -> dict[str, Any]:
    if section not in SECTIONS or section == "":
        raise HTTPException(status_code=404, detail=f"unknown ontology section: {section}")
    return _fetch(settings, section, pack)
