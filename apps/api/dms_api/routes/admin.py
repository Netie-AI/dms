"""Admin — people, roles, departments, who can see what, and compute pools.

Usability rule 11: name things by what people control. The UI calls the ACL table
"Who can see this"; the column names stay `acl_grants` because that is what the
ledger and the migrations call it, and a vocabulary that changes between the
screen and the audit trail is how a steward loses the thread.

Read-only for now. Granting access is a write with an ACL blast radius, and the
amend loop (proposal → confirm → ledger) is the pattern it has to go through when
it lands — not a bare POST added because a page looked empty.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from cortex_client import compliance_gate
from dms_core.control_plane.session import set_tenant_context
from fastapi import APIRouter

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/admin", tags=["admin"])

_NOT_CONFIGURED_HINT = (
    "No DATABASE_URL — the control plane (users, roles, ACL, pools) lives in Postgres. "
    "Set DATABASE_URL and apply alembic migrations to administer this tenant."
)


@router.get("/overview")
def admin_overview(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    """Users, roles, departments, ACL grants and pools for this tenant.

    The heaviest read in the API - emails, display names, every acl_grants row -
    and until now the only route of its size with no gate at all. Gated before
    the connection opens: a refusal that arrives after the rows are read has not
    refused anything (A-0007-03).
    """
    decision = compliance_gate(
        action="admin.overview.read",
        metadata={"task_id": "admin.overview.read"},
        client=cortex,
    )
    # mutation=False: these are reads. gatekeeping.py is explicit that an
    # unreachable gate must not refuse a read - "refusing to answer a question
    # is not the same risk as applying an unrecorded change". Without this the
    # three routes would 403 whenever Cortex is down, which is a control
    # refusing legitimate work (R-0005), not a boundary holding.
    enforce(decision, mutation=False)
    if not settings.database_url:
        return {
            "configured": False,
            "hint": _NOT_CONFIGURED_HINT,
            "tenant_id": settings.dms_tenant_id,
            "actor_role": settings.dms_actor_role,
            "users": [],
            "departments": [],
            "roles": [],
            "grants": [],
            "pools": [],
        }

    tenant = UUID(settings.dms_tenant_id)
    with psycopg.connect(settings.database_url) as conn:
        set_tenant_context(conn, settings.dms_tenant_id, role="admin")
        users = conn.execute(
            """
            SELECT u.id::text, u.email, u.display_name, r.name, d.name
              FROM dms.memberships m
              JOIN dms.users u ON u.id = m.user_id
              JOIN dms.roles r ON r.id = m.role_id
              LEFT JOIN dms.departments d ON d.id = m.department_id
             WHERE m.tenant_id = %s
             ORDER BY u.email
            """,
            (tenant,),
        ).fetchall()
        departments = conn.execute(
            "SELECT id::text, name FROM dms.departments WHERE tenant_id = %s ORDER BY name",
            (tenant,),
        ).fetchall()
        roles = conn.execute(
            "SELECT name, description FROM dms.roles ORDER BY name"
        ).fetchall()
        grants = conn.execute(
            """
            SELECT g.id::text, g.resource_kind, g.resource_id::text, g.permission,
                   u.email, d.name
              FROM dms.acl_grants g
              LEFT JOIN dms.users u ON u.id = g.principal_user_id
              LEFT JOIN dms.departments d ON d.id = g.principal_department_id
             WHERE g.tenant_id = %s
             ORDER BY g.created_at DESC
             LIMIT 200
            """,
            (tenant,),
        ).fetchall()
        pools = conn.execute(
            """
            SELECT id::text, name, kind, config
              FROM dms.compute_pool
             WHERE tenant_id = %s
             ORDER BY name
            """,
            (tenant,),
        ).fetchall()
        conn.commit()

    return {
        "configured": True,
        "tenant_id": settings.dms_tenant_id,
        "actor_role": settings.dms_actor_role,
        "users": [
            {"id": r[0], "email": r[1], "display_name": r[2], "role": r[3], "department": r[4]}
            for r in users
        ],
        "departments": [{"id": r[0], "name": r[1]} for r in departments],
        "roles": [{"name": r[0], "description": r[1]} for r in roles],
        "grants": [
            {
                "id": r[0],
                "resource_kind": r[1],
                "resource_id": r[2],
                "permission": r[3],
                "principal": r[4] or r[5] or "—",
                "principal_kind": "user" if r[4] else "department",
            }
            for r in grants
        ],
        "pools": [{"id": r[0], "name": r[1], "kind": r[2], "config": r[3]} for r in pools],
    }
