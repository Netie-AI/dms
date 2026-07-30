"""Library — sources attached to Spaces (control plane)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from dms_core.control_plane.session import set_tenant_context
from fastapi import APIRouter

from dms_api.deps import SettingsDep
from dms_api.wiring import bronze_list

router = APIRouter(prefix="/v1/library", tags=["library"])


@router.get("/sources")
def list_sources(settings: SettingsDep) -> list[dict[str, Any]]:
    if not settings.database_url:
        return []
    with psycopg.connect(settings.database_url) as conn:
        set_tenant_context(conn, settings.dms_tenant_id, role="viewer")
        rows = conn.execute(
            """
            SELECT d.id::text, d.kind, d.ref, d.scope, d.space_id::text, s.name
              FROM dms.data_sources d
              LEFT JOIN dms.spaces s ON s.id = d.space_id
             WHERE d.tenant_id = %s
             ORDER BY d.created_at DESC
            """,
            (UUID(settings.dms_tenant_id),),
        ).fetchall()
        conn.commit()
    return [
        {
            "id": r[0],
            "kind": r[1],
            "ref": r[2],
            "scope": r[3],
            "space_id": r[4],
            "space_name": r[5],
        }
        for r in rows
    ]


@router.get("/data-map")
def data_map(settings: SettingsDep) -> dict[str, Any]:
    bronze = bronze_list()
    sources = list_sources(settings)
    return {
        "sources": sources,
        "bronze_tables": bronze,
        "note": "Physical map — bronze tables carry _src_ref_id / _src_row / _ingest_id",
    }
