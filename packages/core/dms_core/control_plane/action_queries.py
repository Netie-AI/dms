"""Governed action reads for the ACT-02 routes: versions, evidence, cells, decisions.

Everything here is a tenant-scoped SELECT under the caller's RLS context
(``set_tenant_context``). Nothing writes and nothing commits.

Evidence (E9). An action may cite a governed answer by its envelope ``audit_id``,
which is the Cortex ledger pointer ``dms.ask_audit.cortex_entry_id``, not the
internal ``ask_id``. The citation holds only if that answer executed a query,
was validated, returned rows, and read the action type's source table. A blank
id is refused, because ``''`` is the column default and would match every ask
that never reached the ledger.

psycopg only (layers).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import psycopg

from dms_core.control_plane.action_types import object_key_hash


class EvidenceRefused(Exception):
    """The cited answer cannot back an action. ``code`` is a stable identifier."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _text(value: Any) -> Any:
    return None if value is None else str(value)


def _space_text(value: Any) -> str:
    """A Space id as comparable text: a UUID in canonical form, anything else stripped."""
    raw = "" if value is None else str(value).strip()
    try:
        return str(UUID(raw))
    except ValueError:
        return raw.lower()


def space_in_tenant(conn: psycopg.Connection, *, space_id: UUID) -> bool:
    """True when the Space is visible under the current tenant context."""
    row = conn.execute("SELECT 1 FROM dms.spaces WHERE id = %s", (space_id,)).fetchone()
    return row is not None


def load_action_version(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    proposal_id: UUID,
    idempotency_token: str,
) -> dict[str, Any] | None:
    """The action version this token names, only if it belongs to ``proposal_id``."""
    row = conn.execute(
        """
        SELECT v.id, v.proposal_id, v.version_num, v.status, v.diff, p.space_id
          FROM dms.proposal_versions v
          JOIN dms.action_proposals a ON a.proposal_id = v.proposal_id
          JOIN dms.proposals p ON p.id = v.proposal_id
         WHERE v.idempotency_token = %s
           AND v.proposal_id = %s
           AND v.tenant_id = %s
        """,
        (idempotency_token, proposal_id, tenant_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "version_id": row[0],
        "proposal_id": row[1],
        "version_num": int(row[2]),
        "status": row[3],
        "diff": row[4] if isinstance(row[4], dict) else {},
        "space_id": row[5],
    }


def governed_evidence(
    conn: psycopg.Connection,
    *,
    audit_id: str | None,
    source_table: str,
    space_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Resolve an envelope ``audit_id`` to evidence an action may cite, or refuse.

    Refusals: ``evidence_unknown`` (blank, or no such ask in this tenant),
    ``evidence_wrong_space`` (the answer was asked in another Space than the
    action's), ``evidence_not_validated`` (badge is not validated, no executed
    SQL, or zero rows: E9), ``evidence_wrong_table`` (the answer never read the
    source table).

    An answer asked with no Space (``ask_audit.space_id`` is ``''``) is tenant-wide
    and may back an action in any Space of the tenant; one asked in a Space backs
    only actions in that Space.
    """
    ref = (audit_id or "").strip()
    if not ref:
        raise EvidenceRefused("evidence_unknown", "evidence_audit_id is blank")
    row = conn.execute(
        """
        SELECT question, badge, executed_sql, row_count, tables_read,
               cortex_entry_id, ledger_seq, space_id
          FROM dms.ask_audit
         WHERE cortex_entry_id = %s
         ORDER BY asked_at DESC
         LIMIT 1
        """,
        (ref,),
    ).fetchone()
    if row is None:
        raise EvidenceRefused("evidence_unknown", "no governed answer with that audit_id")
    question, badge, executed_sql, row_count, tables_read = row[0], row[1], row[2], row[3], row[4]
    asked_in = _space_text(row[7])
    if space_id is not None and asked_in and asked_in != _space_text(space_id):
        raise EvidenceRefused("evidence_wrong_space", "the cited answer was asked in another Space")
    if badge != "validated" or not (executed_sql or "").strip() or int(row_count or 0) <= 0:
        raise EvidenceRefused(
            "evidence_not_validated",
            "the cited answer must be validated, have executed SQL and return rows",
        )
    read = {str(t).strip().lower().rsplit(".", 1)[-1] for t in (tables_read or [])}
    if source_table.lower() not in read:
        raise EvidenceRefused(
            "evidence_wrong_table", f"the cited answer did not read {source_table}"
        )
    return {
        "audit_id": ref,
        "question": question,
        "badge": badge,
        "row_count": int(row_count),
    }


def cell_state(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    object_type: str,
    object_key: Mapping[str, str],
    field: str,
) -> dict[str, Any]:
    """One writeback cell as ``state_hash`` reads it; absent is value None, row_version 0."""
    row = conn.execute(
        """
        SELECT value, row_version
          FROM dms.action_writeback
         WHERE tenant_id = %s
           AND object_type = %s
           AND object_key_hash = %s
           AND field = %s
        """,
        (tenant_id, object_type, object_key_hash(object_key), field),
    ).fetchone()
    return {
        "object_type": object_type,
        "object_key": dict(object_key),
        "field": field,
        "value": None if row is None else row[0],
        "row_version": 0 if row is None else int(row[1]),
    }


def list_cells(
    conn: psycopg.Connection, *, tenant_id: UUID, object_type: str
) -> list[dict[str, Any]]:
    """Every writeback cell of one object type, cleared ones (value NULL) included."""
    rows = conn.execute(
        """
        SELECT w.object_key, w.field, w.value, w.row_version, w.updated_at,
               d.id
          FROM dms.action_writeback w
          LEFT JOIN dms.action_decisions d
            ON d.proposal_version_id = w.last_version_id
         WHERE w.tenant_id = %s
           AND w.object_type = %s
         ORDER BY w.object_key_hash, w.field
        """,
        (tenant_id, object_type),
    ).fetchall()
    return [
        {
            "object_type": object_type,
            "object_key": r[0],
            "field": r[1],
            "value": r[2],
            "row_version": int(r[3]),
            "updated_at": _iso(r[4]),
            "last_decision_id": _text(r[5]),
        }
        for r in rows
    ]


_DECISION_FIELDS = (
    "id",
    "space_id",
    "proposal_id",
    "proposal_version_id",
    "decision",
    "action_type",
    "object_type",
    "object_key",
    "field",
    "op",
    "before",
    "after",
    "before_hash",
    "after_hash",
    "rows_changed",
    "verified",
    "approval_ref",
    "evidence",
    "cortex_entry_id",
    "ledger_seq",
    "decided_at",
)


def decision_json(row: Mapping[str, Any]) -> dict[str, Any]:
    """A decision row as JSON-ready values (ids as text, time as ISO 8601)."""
    out: dict[str, Any] = {}
    for name in _DECISION_FIELDS:
        value = row.get(name)
        if name in {"id", "space_id", "proposal_id", "proposal_version_id"}:
            value = _text(value)
        elif name == "decided_at":
            value = _iso(value)
        out[name] = value
    return out


def list_decisions(
    conn: psycopg.Connection, *, tenant_id: UUID, space_id: UUID
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT {", ".join(_DECISION_FIELDS)}
          FROM dms.action_decisions
         WHERE tenant_id = %s
           AND space_id = %s
         ORDER BY decided_at DESC, id
         LIMIT 200
        """,
        (tenant_id, space_id),
    ).fetchall()
    return [decision_json(dict(zip(_DECISION_FIELDS, r, strict=True))) for r in rows]


def list_action_proposals(
    conn: psycopg.Connection, *, tenant_id: UUID, space_id: UUID
) -> list[dict[str, Any]]:
    """The latest version of every action proposal in one Space."""
    rows = conn.execute(
        """
        SELECT p.id, v.version_num, v.status, v.idempotency_token, v.diff, p.created_at
          FROM dms.proposals p
          JOIN dms.action_proposals a ON a.proposal_id = p.id
          JOIN LATERAL (
            SELECT * FROM dms.proposal_versions pv
             WHERE pv.proposal_id = p.id
             ORDER BY pv.version_num DESC LIMIT 1
          ) v ON true
         WHERE p.tenant_id = %s
           AND p.space_id = %s
         ORDER BY p.created_at DESC
         LIMIT 50
        """,
        (tenant_id, space_id),
    ).fetchall()
    return [
        {
            "proposal_id": str(r[0]),
            "version_num": int(r[1]),
            "status": r[2],
            "idempotency_token": r[3],
            "diff": r[4] or {},
            "created_at": _iso(r[5]),
        }
        for r in rows
    ]


__all__ = [
    "EvidenceRefused",
    "cell_state",
    "decision_json",
    "governed_evidence",
    "list_action_proposals",
    "list_cells",
    "list_decisions",
    "load_action_version",
    "space_in_tenant",
]
