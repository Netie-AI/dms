"""Library — sources, Data Map, and read-only warehouse browse."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from dms_core.control_plane.session import set_tenant_context
from fastapi import APIRouter, HTTPException, Query

from dms_api.deps import SettingsDep
from dms_api.wiring import bronze_list, bronze_preview, warehouse_preview, warehouse_tables

router = APIRouter(prefix="/v1/library", tags=["library"])


def _hide_offline_fixtures(settings: SettingsDep) -> bool:
    """Live memory demo: hide offline Company fixtures from the stranger path."""
    return settings.dms_ask_mode == "live" and not settings.database_url


@router.get("/sources")
def list_sources(settings: SettingsDep) -> list[dict[str, Any]]:
    if not settings.database_url:
        # Offline fixture tree so Library is usable without Postgres.
        sources: list[dict[str, Any]] = [
            {
                "id": "src_q3_sales",
                "kind": "xlsx",
                "ref": "Finance/workbooks/sales_q3.xlsx",
                "scope": "team",
                "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "space_name": "Finance",
            },
            {
                "id": "src_q3_inv",
                "kind": "csv",
                "ref": "Finance/csv/inventory_snapshot.csv",
                "scope": "team",
                "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "space_name": "Finance",
            },
            {
                "id": "src_company_gl",
                "kind": "csv",
                "ref": "Company/finance/gl_export.csv",
                "scope": "company",
                "space_id": None,
                "space_name": None,
            },
        ]
        if _hide_offline_fixtures(settings):
            sources = [s for s in sources if s.get("space_id")]
        return sources
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
def data_map(
    settings: SettingsDep,
    space_id: str | None = Query(None),
) -> dict[str, Any]:
    bronze = bronze_list(space_id=space_id)
    sources = list_sources(settings)
    if space_id:
        sources = [s for s in sources if s.get("space_id") == space_id]
    warehouse = warehouse_tables()
    notes = [
        "Physical map — bronze tables carry _src[] / _ingest_id provenance.",
        "Warehouse preview is the local DuckDB demo lake (DbGate-style, read-only).",
    ]
    if not settings.database_url:
        notes.append(
            "Postgres sources empty — set DATABASE_URL (compose postgres) "
            "for control-plane sources."
        )
    return {
        "sources": sources,
        "bronze_tables": bronze,
        "warehouse_tables": warehouse,
        "database_configured": bool(settings.database_url),
        "note": " ".join(notes),
        "space_id": space_id,
    }


@router.get("/tree")
def library_tree_route(
    settings: SettingsDep,
    space_id: str | None = Query(None),
) -> dict[str, Any]:
    """Foldable Space repository tree (Sources / Bronze / Warehouse)."""
    from dms_api.wiring import library_tree as build_tree

    sources = list_sources(settings)
    space_name = None
    if space_id:
        for s in sources:
            if s.get("space_id") == space_id and s.get("space_name"):
                space_name = s["space_name"]
                break
    return build_tree(
        sources=sources,
        bronze=bronze_list(space_id=space_id),
        warehouse=warehouse_tables(),
        space_id=space_id,
        space_name=space_name,
    )


@router.get("/warehouse/tables")
def list_wh_tables() -> list[dict[str, Any]]:
    return warehouse_tables()


@router.get("/warehouse/{table}/preview")
def preview_wh_table(
    table: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        return warehouse_preview(table, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/bronze/{table:path}/preview")
def preview_bronze(
    table: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    try:
        return bronze_preview(table, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
