"""Steward semantic-model import (FEAT-SEMANTIC-IMPORT-01).

``DMS_SEMANTIC_IMPORT`` defaults to off. While it is not the digit 1,
``app.py`` does not mount this router, so both paths are a 404 and the
OpenAPI document is main's. The file is the request body. This route does
not fetch a URL. Nothing here reads question text.
"""

from __future__ import annotations

import os
from typing import Any

from cortex_client import compliance_gate
from dms_core.control_plane.onto_data import ontology_data_store
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import (
    approve_semantic_examples,
    export_semantic_space,
    import_semantic_file,
)

FLAG = "DMS_SEMANTIC_IMPORT"
_MAX_BYTES = 1_048_576

router = APIRouter(prefix="/v1/spaces", tags=["semantic-import"])


def enabled() -> bool:
    """``DMS_SEMANTIC_IMPORT=1`` exactly. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


def _dry_run(value: str | None) -> bool:
    """Omitted is a dry run. Only an explicit false token writes."""
    if value is None or not value.strip():
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _steward(settings: Any) -> str:
    actor = "" if settings.dms_actor_user_id is None else str(settings.dms_actor_user_id).strip()
    if not ontology_data_store().holds_steward_grant(role=settings.dms_actor_role, actor_id=actor):
        raise HTTPException(status_code=403, detail="steward_grant_required")
    return actor


def _http(exc: BaseException) -> HTTPException:
    if type(exc).__name__ == "SemanticImportDisabled":
        return HTTPException(status_code=404, detail="Not Found")
    if isinstance(exc, ValueError) and exc.args and isinstance(exc.args[0], str):
        return HTTPException(status_code=400, detail=exc.args[0])
    raise exc


@router.post("/{space_id}/semantic-import")
async def semantic_import(
    space_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
    file: UploadFile = File(...),
    dry_run: str | None = Form(None),
) -> dict[str, Any]:
    """Upload one model file. ``dry_run`` defaults true and writes nothing."""
    actor = _steward(settings)
    write = not _dry_run(dry_run)
    decision = compliance_gate(
        action="semantic.import",
        actor=actor,
        metadata={"task_id": "semantic.import", "space_id": space_id, "dry_run": not write},
        client=cortex,
    )
    enforce(decision, mutation=write)
    raw = await file.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="file_too_large")
    try:
        return import_semantic_file(raw, space_id=space_id, dry_run=not write, actor=actor)
    except Exception as exc:  # noqa: BLE001 - a typed code, or the flag, else re-raised
        raise _http(exc) from None


@router.post("/{space_id}/semantic-import/approve")
def semantic_import_approve(
    space_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Approve imported examples. Steward and the compliance gate, then the write."""
    actor = _steward(settings)
    decision = compliance_gate(
        action="semantic.import.approve",
        actor=actor,
        metadata={"task_id": "semantic.import.approve", "space_id": space_id},
        client=cortex,
    )
    enforce(decision, mutation=True)
    try:
        approved = approve_semantic_examples(space_id=space_id, actor=actor)
    except Exception as exc:  # noqa: BLE001 - a typed code, or the flag, else re-raised
        raise _http(exc) from None
    return {"approved": approved, "served": False}


@router.get("/{space_id}/semantic-export")
def semantic_export(
    space_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
    export_format: str = Query("osi", alias="format"),
) -> dict[str, Any]:
    """Export the approved Space. ``format=osi`` is OSI 0.1.1."""
    _steward(settings)
    decision = compliance_gate(
        action="semantic.export",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "semantic.export", "space_id": space_id, "format": export_format},
        client=cortex,
    )
    enforce(decision, mutation=False)
    try:
        return export_semantic_space(space_id=space_id, export_format=export_format)
    except Exception as exc:  # noqa: BLE001 - a typed code, or the flag, else re-raised
        raise _http(exc) from None
