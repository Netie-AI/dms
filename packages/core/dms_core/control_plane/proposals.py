"""Versioned proposals — idempotency tokens and confirm/apply."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any
from uuid import UUID

import psycopg

from dms_core.control_plane.advisory_lock import advisory_lock


class ConflictError(Exception):
    """Another confirm already applied this proposal (HTTP 409 equivalent)."""

    status_code = 409


class StaleTokenError(Exception):
    """Idempotency token no longer matches an active proposal version."""

    status_code = 409


def _token_for(proposal_id: UUID, version_num: int, diff: dict[str, Any]) -> str:
    payload = json.dumps(
        {"proposal_id": str(proposal_id), "version": version_num, "diff": diff},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def create_proposal_version(
    conn: psycopg.Connection,
    *,
    proposal_id: UUID,
    tenant_id: UUID,
    diff: dict[str, Any],
    status: str = "pending_confirm",
) -> dict[str, Any]:
    """Insert a new version; supersede prior active versions (invalidates tokens)."""
    conn.execute(
        """
        UPDATE dms.proposal_versions
           SET status = 'superseded'
         WHERE proposal_id = %s
           AND status IN ('draft', 'pending_confirm')
        """,
        (proposal_id,),
    )
    row = conn.execute(
        """
        SELECT COALESCE(MAX(version_num), 0) + 1
          FROM dms.proposal_versions
         WHERE proposal_id = %s
        """,
        (proposal_id,),
    ).fetchone()
    assert row is not None
    version_num = int(row[0])
    token = _token_for(proposal_id, version_num, diff)
    version_id = uuid.uuid4()
    conn.execute(
        """
        INSERT INTO dms.proposal_versions (
          id, proposal_id, tenant_id, version_num, diff, idempotency_token, status
        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
        """,
        (version_id, proposal_id, tenant_id, version_num, json.dumps(diff), token, status),
    )
    return {
        "id": version_id,
        "proposal_id": proposal_id,
        "version_num": version_num,
        "idempotency_token": token,
        "status": status,
    }


def confirm_proposal_version(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    idempotency_token: str,
    lock_timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    """Apply a pending version under advisory lock.

    Exactly one concurrent confirm wins; the other raises ConflictError (409).
    A superseded / non-pending token raises StaleTokenError.
    """
    with advisory_lock(
        conn, tenant_id, "proposal_versions", timeout_seconds=lock_timeout_seconds
    ):
        row = conn.execute(
            """
            SELECT id, proposal_id, status, version_num
              FROM dms.proposal_versions
             WHERE idempotency_token = %s
               AND tenant_id = %s
             FOR UPDATE
            """,
            (idempotency_token, tenant_id),
        ).fetchone()
        if row is None:
            raise StaleTokenError("unknown or foreign idempotency token")
        version_id, proposal_id, status, version_num = row
        if status != "pending_confirm":
            if status == "applied":
                raise ConflictError("proposal version already applied")
            raise StaleTokenError(f"token not active (status={status})")

        # Mark applied; unique partial index already ensures one active version.
        updated = conn.execute(
            """
            UPDATE dms.proposal_versions
               SET status = 'applied'
             WHERE id = %s
               AND status = 'pending_confirm'
            RETURNING id
            """,
            (version_id,),
        ).fetchone()
        if updated is None:
            raise ConflictError("lost race on confirm")
        return {
            "id": version_id,
            "proposal_id": proposal_id,
            "version_num": version_num,
            "status": "applied",
        }
