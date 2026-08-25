"""Runs — ingest and query progress, one feed, with the failure reason attached.

Usability rule 7: an error names the file and the fix. That is only possible if
the receipt survives the request that produced it, so this reads the durable
tables (``dms.ingest_run`` receipts, ``dms.query_run`` status) rather than any
in-process state.

Without ``DATABASE_URL`` there is no durable history and the response says so —
``configured: false`` with an empty list. Inventing plausible rows on a page whose
whole job is "what actually happened" would be the worst possible place to fake.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from cortex_client import compliance_gate
from dms_core.control_plane.session import set_tenant_context
from fastapi import APIRouter, Query

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/runs", tags=["runs"])

_NOT_CONFIGURED_HINT = (
    "No DATABASE_URL — run history is not durable in this deployment. "
    "Set DATABASE_URL and apply alembic migrations to record ingest and query runs."
)


def _receipt_summary(receipt: dict[str, Any] | None) -> str:
    if not receipt:
        return ""
    seen = receipt.get("files_seen")
    ingested = receipt.get("ingested")
    attention = receipt.get("need_attention") or receipt.get("quarantined") or 0
    if seen is None:
        return str(receipt.get("summary") or "")
    return f"{seen} files · {ingested} ingested · {attention} need attention"


def _reasons(receipt: dict[str, Any] | None) -> list[dict[str, str]]:
    if not receipt:
        return []
    out: list[dict[str, str]] = []
    for row in receipt.get("files") or []:
        if not isinstance(row, dict) or row.get("classification") == "TABULAR_CLEAN":
            continue
        out.append(
            {
                "file": str(row.get("file", "")),
                "reason": str(row.get("reason", "")),
                "fix": str(row.get("fix", "")),
            }
        )
    return out


@router.get("")
def list_runs(
    settings: SettingsDep,
    cortex: CortexDep,
    kind: str | None = Query(None, description="ingest | query"),
    limit: int = Query(50, ge=1, le=200),
    space_id: str | None = Query(None, description="restrict the feed to one Space"),
) -> dict[str, Any]:
    """One feed of ingest and query runs, scoped to a Space when one is named.

    This route had no ``space_id`` at all. It filtered on ``tenant_id`` and joined
    ``dms.spaces`` only to read a display name, so a single unscoped call returned
    every Space's runs and **the caller could not have narrowed it if they wanted
    to** - not a skippable check like the Library previews, an absent one.
    Measured before the fix, on a configured control plane with two Spaces holding
    one run each, one request returned both.

    ``space_id`` is optional because the runs feed is legitimately a
    company-wide view. What changed is that naming a Space now means something:
    the filter goes into the SQL, so it cannot be forgotten by a later branch.
    The applied scope is named on the response for the same reason the previews
    name theirs - a feed that silently widened is the failure being fixed.
    """
    decision = compliance_gate(
        action="runs.read",
        metadata={"task_id": "runs.read", "space_id": space_id or ""},
        client=cortex,
    )
    # mutation=False: these are reads. gatekeeping.py is explicit that an
    # unreachable gate must not refuse a read - "refusing to answer a question
    # is not the same risk as applying an unrecorded change". Without this the
    # three routes would 403 whenever Cortex is down, which is a control
    # refusing legitimate work (R-0005), not a boundary holding.
    enforce(decision, mutation=False)
    if not settings.database_url:
        return {"configured": False, "hint": _NOT_CONFIGURED_HINT, "runs": []}

    tenant = UUID(settings.dms_tenant_id)
    space_uuid = UUID(space_id) if space_id else None
    runs: list[dict[str, Any]] = []
    with psycopg.connect(settings.database_url) as conn:
        set_tenant_context(conn, settings.dms_tenant_id, role="viewer")
        if kind in (None, "ingest"):
            rows = conn.execute(
                """
                SELECT i.id::text, i.status, i.receipt, i.created_at, s.name
                  FROM dms.ingest_run i
                  LEFT JOIN dms.spaces s ON s.id = i.space_id
                 WHERE i.tenant_id = %s
                   AND (%s::uuid IS NULL OR i.space_id = %s::uuid)
                 ORDER BY i.created_at DESC
                 LIMIT %s
                """,
                (tenant, space_uuid, space_uuid, limit),
            ).fetchall()
            for r in rows:
                receipt = r[2] if isinstance(r[2], dict) else {}
                runs.append(
                    {
                        "id": r[0],
                        "kind": "ingest",
                        "status": r[1],
                        "created_at": r[3].isoformat() if r[3] else None,
                        "space_name": r[4],
                        "detail": _receipt_summary(receipt),
                        "reasons": _reasons(receipt),
                    }
                )
        if kind in (None, "query"):
            rows = conn.execute(
                """
                SELECT q.id::text, q.status, q.sql_text, q.created_at, s.name, q.ledger_seq
                  FROM dms.query_run q
                  LEFT JOIN dms.spaces s ON s.id = q.space_id
                 WHERE q.tenant_id = %s
                   AND (%s::uuid IS NULL OR q.space_id = %s::uuid)
                 ORDER BY q.created_at DESC
                 LIMIT %s
                """,
                (tenant, space_uuid, space_uuid, limit),
            ).fetchall()
            for r in rows:
                sql_text = r[2] or ""
                runs.append(
                    {
                        "id": r[0],
                        "kind": "query",
                        "status": r[1],
                        "created_at": r[3].isoformat() if r[3] else None,
                        "space_name": r[4],
                        "detail": sql_text[:200] + ("…" if len(sql_text) > 200 else ""),
                        "ledger_seq": r[5],
                        "reasons": [],
                    }
                )
        conn.commit()

    runs.sort(key=lambda r: r["created_at"] or "", reverse=True)
    counts: dict[str, int] = {}
    for run in runs:
        counts[run["status"]] = counts.get(run["status"], 0) + 1
    return {
        "configured": True,
        "runs": runs[:limit],
        "counts": counts,
        "scope": f"space:{space_id}" if space_id else "all-spaces",
    }
