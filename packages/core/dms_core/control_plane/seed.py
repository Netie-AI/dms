"""Seed demo tenant / spaces / sources — no PII (synthetic emails)."""

from __future__ import annotations

import logging
import uuid

import psycopg

from dms_core.control_plane.session import set_tenant_context

logger = logging.getLogger(__name__)

# Stable IDs so UI fixtures and smoke scripts can pin them
TENANT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SPACE_Q3 = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
SPACE_MARGIN = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


def seed_demo_tenant(conninfo: str) -> None:
    with psycopg.connect(conninfo) as conn:
        exists = conn.execute(
            "SELECT 1 FROM dms.tenants WHERE id = %s", (TENANT_ID,)
        ).fetchone()
        if exists:
            logger.info("seed: demo tenant already present")
            conn.commit()
            return

        conn.execute(
            "INSERT INTO dms.tenants (id, slug, name) VALUES (%s, 'demo', 'Demo tenant')",
            (TENANT_ID,),
        )
        conn.execute(
            """
            INSERT INTO dms.users (id, email, display_name)
            VALUES (%s, 'steward@demo.local', 'Demo Steward')
            """,
            (USER_ID,),
        )
        role = conn.execute("SELECT id FROM dms.roles WHERE name = 'steward'").fetchone()
        assert role is not None
        set_tenant_context(conn, TENANT_ID, role="admin")
        conn.execute(
            """
            INSERT INTO dms.memberships (tenant_id, user_id, role_id)
            VALUES (%s, %s, %s)
            """,
            (TENANT_ID, USER_ID, role[0]),
        )
        for sid, name in ((SPACE_Q3, "Q3 Audit"), (SPACE_MARGIN, "Margin sandbox")):
            conn.execute(
                """
                INSERT INTO dms.spaces (id, tenant_id, name, created_by)
                VALUES (%s, %s, %s, %s)
                """,
                (sid, TENANT_ID, name, USER_ID),
            )
            conn.execute(
                """
                INSERT INTO dms.space_members (space_id, tenant_id, user_id)
                VALUES (%s, %s, %s)
                """,
                (sid, TENANT_ID, USER_ID),
            )
        # Synthetic sources attached to Q3 Audit
        for kind, ref in (
            ("xlsx", "Q3_sales_final_v2.xlsx"),
            ("xlsx", "KL_branch_sales.xlsx"),
            ("sql", "transactions"),
        ):
            conn.execute(
                """
                INSERT INTO dms.data_sources (tenant_id, space_id, kind, ref, scope, meta)
                VALUES (%s, %s, %s, %s, 'company', '{}'::jsonb)
                """,
                (TENANT_ID, SPACE_Q3, kind, ref),
            )
        conn.execute(
            """
            INSERT INTO dms.data_sources (tenant_id, space_id, kind, ref, scope, meta)
            VALUES (%s, %s, 'sql', 'inventory', 'team', '{}'::jsonb),
                   (%s, %s, 'sql', 'locations', 'team', '{}'::jsonb)
            """,
            (TENANT_ID, SPACE_MARGIN, TENANT_ID, SPACE_MARGIN),
        )
        conn.commit()
        logger.info("seed: demo tenant + spaces created")
