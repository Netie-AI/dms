"""Space-scoped resulting-workbook artifacts (dms#31 / XLSX-ORCH-11).

Separate table from ``data_sources`` / ``document_chunks``. Those hold
ingested originals (AirGPT #20). This holds Copilot *results* (kind xlsx_result).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg

from dms_core.control_plane.session import AppRole, set_tenant_context

KIND = "xlsx_result"


def register_artifact(
    conninfo: str,
    *,
    tenant_id: UUID | str,
    space_id: UUID | str,
    artifact_id: UUID | str,
    blob_key: str,
    sha256: str,
    origin_path: str | None,
    sheets: list[str],
    complete: bool,
    missing_families: list[str],
    role: AppRole = "steward",
) -> str:
    """Insert one ``dms.space_artifacts`` row. Returns the id."""
    if not space_id:
        raise ValueError("space_id_required")
    with psycopg.connect(conninfo) as conn:
        set_tenant_context(conn, tenant_id, role=role)
        owned = conn.execute(
            """
            SELECT 1 FROM dms.spaces
             WHERE id::text = %s AND tenant_id::text = %s
            """,
            (str(space_id), str(tenant_id)),
        ).fetchone()
        if owned is None:
            raise ValueError("space_not_in_tenant")
        row = conn.execute(
            """
            INSERT INTO dms.space_artifacts
              (id, tenant_id, space_id, kind, blob_key, sha256, origin_path,
               sheets, complete, missing_families)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
            RETURNING id::text
            """,
            (
                str(artifact_id),
                str(tenant_id),
                str(space_id),
                KIND,
                blob_key,
                sha256,
                origin_path,
                json.dumps(list(sheets)),
                complete,
                json.dumps(list(missing_families)),
            ),
        ).fetchone()
        assert row is not None
        conn.commit()
        return str(row[0])


def get_artifact(
    conninfo: str,
    *,
    tenant_id: UUID | str,
    artifact_id: UUID | str,
    space_id: str | None = None,
    role: AppRole = "viewer",
) -> dict[str, Any] | None:
    with psycopg.connect(conninfo) as conn:
        set_tenant_context(conn, tenant_id, role=role)
        if space_id:
            row = conn.execute(
                """
                SELECT id::text, space_id::text, kind, blob_key, sha256,
                       origin_path, sheets, complete, missing_families
                  FROM dms.space_artifacts
                 WHERE id::text = %s AND tenant_id::text = %s
                   AND space_id::text = %s
                """,
                (str(artifact_id), str(tenant_id), str(space_id)),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id::text, space_id::text, kind, blob_key, sha256,
                       origin_path, sheets, complete, missing_families
                  FROM dms.space_artifacts
                 WHERE id::text = %s AND tenant_id::text = %s
                """,
                (str(artifact_id), str(tenant_id)),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "space_id": row[1],
            "kind": row[2],
            "path": row[3],
            "sha256": row[4],
            "origin_path": row[5],
            "sheets": list(row[6] or []),
            "complete": bool(row[7]),
            "missing_families": list(row[8] or []),
        }
