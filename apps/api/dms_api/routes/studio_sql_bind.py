"""Steward grant for a SQL source the studio land registered.

``DMS_SQL_SOURCE_BIND`` defaults off. While it is off, ``app.py`` does not
mount this router, so the path is a 404 and the OpenAPI document is main's.

The grant is the same book ``POST /v1/connect/sources/{source_id}/grants``
writes. This route does not take an OpenVault credential reference. It does
not grant on its own: the body is the steward's table list.
"""

from __future__ import annotations

import os
from typing import Any

from cortex_client import compliance_gate
from dms_core.control_plane.onto_data import ontology_data_store
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import grant_connected_tables

FLAG = "DMS_SQL_SOURCE_BIND"
_ON = frozenset({"1", "true", "yes", "on"})

router = APIRouter(prefix="/v1/studio", tags=["studio"])


def enabled() -> bool:
    """``DMS_SQL_SOURCE_BIND`` on for 1/true/yes/on. Unset is off."""
    return os.environ.get(FLAG, "0").strip().lower() in _ON


class SqlBindGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: list[str] = Field(min_length=1, max_length=64)


def _steward(settings: Any) -> str:
    actor = "" if settings.dms_actor_user_id is None else str(settings.dms_actor_user_id).strip()
    if not ontology_data_store().holds_steward_grant(role=settings.dms_actor_role, actor_id=actor):
        raise HTTPException(status_code=403, detail="steward_grant_required")
    return actor


@router.post("/sources/{source_id}/grants")
def grant_bound_sql_source(
    source_id: str,
    body: SqlBindGrantIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Grant a subset of the tables the SQL land proposed. No auto-grant."""
    actor = _steward(settings)
    decision = compliance_gate(
        action="studio.sql_source.grant",
        actor=actor,
        metadata={"task_id": "studio.sql_source.grant", "source_id": source_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return grant_connected_tables(
            source_id=source_id,
            tables=body.tables,
            actor=actor,
            token_id="studio",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="source_not_found") from None
    except ValueError as exc:
        code = str(exc)
        if code not in {"bad_table_name", "table_not_exposed", "source_exists"}:
            code = "bad_request"
        raise HTTPException(status_code=400, detail=code) from None
