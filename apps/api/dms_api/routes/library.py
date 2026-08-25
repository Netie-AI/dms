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


def _scope_label(space_id: str | None) -> str:
    """Name the scope that was applied, so the answer says what it was answered under.

    "company-default" is the UI's "Company (default ACL)" option, not "unscoped" - the
    distinction is the whole of A-0007 and the response should not be ambiguous about it.
    """
    return f"space:{space_id}" if space_id else "company-default"


def _refuse(code: str, table: str, space_id: str | None) -> HTTPException:
    """Structured refusal. Never an empty 200 - that reads as a table with no rows."""
    return HTTPException(
        status_code=403,
        detail={
            "code": code,
            "message": f"{table} not in scope",
            "scope": _scope_label(space_id),
        },
    )


@router.get("/warehouse/{table}/preview")
def preview_wh_table(
    table: str,
    cortex: CortexDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    space_id: str | None = Query(None),
) -> dict[str, Any]:
    """Preview a warehouse table under the named Space, or the company default ACL.

    The scope check used to be nested inside ``if space_id:``, so omitting the parameter
    skipped it entirely (A-0007). It now always runs: ``warehouse_tables(space_id=None)``
    resolves the company default scope rather than the whole warehouse.
    """
    decision = compliance_gate(
        action="library.preview_warehouse",
        metadata={
            "task_id": "library.preview_warehouse",
            "table": table[:120],
            "scope": _scope_label(space_id),
        },
        client=cortex,
    )
    # mutation=False: this is a read, and gatekeeping.py's stated posture is that an
    # unreachable gate must not refuse a read the way it refuses a write. The scope
    # check below is NOT subject to that - it runs whether or not Cortex answered, so
    # an outage costs the audit record, never the boundary.
    enforce(decision, mutation=False)

    allowed = {t["table"] for t in warehouse_tables(space_id=space_id)}
    if table.strip().lower() not in {a.lower() for a in allowed}:
        raise _refuse("warehouse_not_in_space", table, space_id)
    try:
        preview = warehouse_preview(table, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    preview["scope"] = _scope_label(space_id)
    return preview


@router.get("/bronze/{table:path}/preview")
def preview_bronze(
    table: str,
    cortex: CortexDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    space_id: str | None = Query(None),
) -> dict[str, Any]:
    """Preview a bronze table under the named Space, or the company default ACL.

    Same A-0007 shape as the warehouse route above. Bronze tables are uploads, so the
    company default is every table registered to some Space - an upload registered to
    none is not previewable under any scope.
    """
    decision = compliance_gate(
        action="library.preview_bronze",
        metadata={
            "task_id": "library.preview_bronze",
            "table": table[:120],
            "scope": _scope_label(space_id),
        },
        client=cortex,
    )
    enforce(decision, mutation=False)  # read posture - see the warehouse route above

    if space_id:
        # Only a named Space can actually refuse here. bronze_list(space_id=None)
        # returns every registered upload, so listing on the company-default path
        # would open a second DuckDB connection to compute a set that refuses
        # nothing - and DuckDB is single-writer, so a needless read is a needless
        # chance to collide with an in-flight ingest (P-DMS-34).
        allowed = {t["table"] for t in bronze_list(space_id=space_id)}
        raw = table.strip().lower()
        allowed_lower = {a.lower() for a in allowed}
        allowed_base = {a.lower().rsplit(".", 1)[-1] for a in allowed}
        if raw not in allowed_lower and raw.rsplit(".", 1)[-1] not in allowed_base:
            raise _refuse("bronze_not_in_space", table, space_id)
    try:
        preview = bronze_preview(table, limit=limit, offset=offset)
    except ValueError as exc:
        # Same answer as an ungranted table, deliberately. 404 for "no such table"
        # beside 403 for "real but not yours" is an enumeration oracle.
        raise _refuse("bronze_not_in_space", table, space_id) from exc
    preview["scope"] = _scope_label(space_id)
    return preview


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
