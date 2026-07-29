"""Session GUC helpers for RLS (SET LOCAL dms.tenant_id)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import psycopg

AppRole = Literal["viewer", "steward", "admin"]

_PG_ROLE = {
    "viewer": "dms_viewer",
    "steward": "dms_steward",
    "admin": "dms_admin",
}


def set_tenant_context(
    conn: psycopg.Connection,
    tenant_id: UUID | str,
    *,
    role: AppRole = "viewer",
) -> None:
    """Bind RLS to ``tenant_id`` and SET ROLE to the matching dms_* role.

    Call inside an open transaction so SET LOCAL applies for that txn only.
    """
    pg_role = _PG_ROLE[role]
    tid = str(tenant_id)
    conn.execute("SELECT set_config('dms.tenant_id', %s, true)", (tid,))
    conn.execute("SELECT set_config('dms.role', %s, true)", (role,))
    # SET ROLE cannot be parameterized; role names are fixed allowlist.
    conn.execute(f"SET LOCAL ROLE {pg_role}")
