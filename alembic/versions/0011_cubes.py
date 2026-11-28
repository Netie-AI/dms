"""Alembic revision: governed metric cubes (FEAT-METRIC-CUBE-01).

Steward-approved cube documents. A proposal is not served. No cross-schema
foreign key. ``space_id`` is text, same as ``connect_sources``.

Chain: ``0011_cubes`` on ``0010_connections``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_cubes"
down_revision: str | None = "0010_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = ("dms_viewer", "dms_steward", "dms_admin")
_WRITERS = ("dms_steward", "dms_admin")
_TENANT = "tenant_id::text = current_setting('dms.tenant_id', true)"


def _rls(table: str) -> None:
    op.execute(f"REVOKE ALL ON dms.{table} FROM PUBLIC")
    op.execute(f"GRANT SELECT ON dms.{table} TO {', '.join(_ROLES)}")
    op.execute(f"GRANT INSERT, UPDATE ON dms.{table} TO {', '.join(_WRITERS)}")
    op.execute(f"ALTER TABLE dms.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE dms.{table} FORCE ROW LEVEL SECURITY")
    for role in _ROLES:
        op.execute(
            f"""
            CREATE POLICY {table}_select_{role} ON dms.{table}
              FOR SELECT TO {role}
              USING ({_TENANT});
            """
        )
    for role in _WRITERS:
        op.execute(
            f"""
            CREATE POLICY {table}_insert_{role} ON dms.{table}
              FOR INSERT TO {role}
              WITH CHECK ({_TENANT});
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_update_{role} ON dms.{table}
              FOR UPDATE TO {role}
              USING ({_TENANT})
              WITH CHECK ({_TENANT});
            """
        )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.metric_cubes (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id TEXT NOT NULL,
          name TEXT NOT NULL,
          body JSONB NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'revoked')),
          created_by TEXT NOT NULL DEFAULT '',
          approved_by TEXT NOT NULL DEFAULT '',
          ts TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX metric_cubes_by_space
          ON dms.metric_cubes (tenant_id, space_id, status);

        CREATE UNIQUE INDEX metric_cubes_one_approved
          ON dms.metric_cubes (tenant_id, space_id, name)
          WHERE status = 'approved';
        """
    )
    _rls("metric_cubes")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.metric_cubes")
