"""Library — sources, Data Map, and read-only warehouse browse."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from cortex_client import compliance_gate
from dms_core.control_plane.session import set_tenant_context
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import (
    bronze_list,
    bronze_preview,
    reveal_origin_uri,
    search_document_chunks,
    warehouse_preview,
    warehouse_tables,
)

router = APIRouter(prefix="/v1/library", tags=["library"])


class RevealBody(BaseModel):
    path: str = Field(min_length=1, max_length=2048)


def _hide_offline_fixtures(settings: SettingsDep) -> bool:
    """Live memory demo: hide offline Company fixtures from the stranger path."""
    return settings.dms_ask_mode == "live" and not settings.database_url


def _list_sources(settings: SettingsDep, *, space_id: str | None = None) -> list[dict[str, Any]]:
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
    else:
        with psycopg.connect(settings.database_url) as conn:
            set_tenant_context(conn, settings.dms_tenant_id, role="viewer")
            if space_id:
                rows = conn.execute(
                    """
                    SELECT d.id::text, d.kind, d.ref, d.scope, d.space_id::text, s.name
                      FROM dms.data_sources d
                      LEFT JOIN dms.spaces s ON s.id = d.space_id
                     WHERE d.tenant_id = %s AND d.space_id = %s::uuid
                     ORDER BY d.created_at DESC
                    """,
                    (UUID(settings.dms_tenant_id), UUID(space_id)),
                ).fetchall()
            else:
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
        sources = [
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
    if space_id:
        sources = [s for s in sources if s.get("space_id") == space_id]
    return sources


@router.get("/sources")
def list_sources(
    settings: SettingsDep,
    space_id: str | None = Query(None),
) -> list[dict[str, Any]]:
    return _list_sources(settings, space_id=space_id)


@router.get("/chunks/search")
def search_chunks(
    settings: SettingsDep,
    space_id: str = Query(..., description="Space that owns the document chunks"),
    q: str = Query(..., min_length=1, description="Lexical search query"),
    limit: int = Query(8, ge=1, le=100),
    source_ids: list[str] | None = Query(None),
) -> list[dict[str, Any]]:
    """Ranked chunk search scoped at storage query (RAG-02)."""
    _ = settings
    return search_document_chunks(
        space_id=space_id,
        q=q,
        limit=limit,
        source_ids=source_ids,
    )


@router.get("/data-map")
def data_map(
    settings: SettingsDep,
    space_id: str | None = Query(None),
) -> dict[str, Any]:
    bronze = bronze_list(space_id=space_id)
    sources = _list_sources(settings, space_id=space_id)
    warehouse = warehouse_tables(space_id=space_id)
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

    sources = _list_sources(settings, space_id=space_id)
    space_name = None
    if space_id:
        for s in sources:
            if s.get("space_name"):
                space_name = s["space_name"]
                break
    return build_tree(
        sources=sources,
        bronze=bronze_list(space_id=space_id),
        warehouse=warehouse_tables(space_id=space_id),
        space_id=space_id,
        space_name=space_name,
    )


@router.get("/warehouse/tables")
def list_wh_tables(space_id: str | None = Query(None)) -> list[dict[str, Any]]:
    return warehouse_tables(space_id=space_id)


@router.get("/warehouse/{table}/preview")
def preview_wh_table(
    table: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    space_id: str | None = Query(None),
) -> dict[str, Any]:
    if space_id:
        allowed = {t["table"] for t in warehouse_tables(space_id=space_id)}
        if table.strip().lower() not in {a.lower() for a in allowed}:
            raise HTTPException(
                status_code=403,
                detail={"code": "warehouse_not_in_space", "message": f"{table} not in Space scope"},
            )
    try:
        return warehouse_preview(table, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/bronze/{table:path}/preview")
def preview_bronze(
    table: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    space_id: str | None = Query(None),
) -> dict[str, Any]:
    if space_id:
        allowed = {t["table"] for t in bronze_list(space_id=space_id)}
        raw = table.strip().lower()
        allowed_lower = {a.lower() for a in allowed}
        allowed_base = {a.lower().rsplit(".", 1)[-1] for a in allowed}
        if raw not in allowed_lower and raw.rsplit(".", 1)[-1] not in allowed_base:
            raise HTTPException(
                status_code=403,
                detail={"code": "bronze_not_in_space", "message": f"{table} not in Space scope"},
            )
    try:
        return bronze_preview(table, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reveal")
def reveal_path_route(body: RevealBody, cortex: CortexDep) -> dict[str, Any]:
    """Open Explorer on an allowlisted filesystem origin_uri (REVEAL-01)."""
    decision = compliance_gate(
        action="library.reveal",
        metadata={"task_id": "library.reveal", "path": body.path[:200]},
        client=cortex,
    )
    enforce(decision)
    result = reveal_origin_uri(body.path)
    if result.get("error") == "path_not_allowlisted":
        raise HTTPException(status_code=403, detail="path_not_allowlisted")
    return result
