"""Alembic revision: source_ref + ingest runs for T7 provenance spine."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_source_ref_ingest"
down_revision: str | None = "0001_dms_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.source_ref (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          kind TEXT NOT NULL,
          blob_hash TEXT,
          container TEXT NOT NULL,
          member TEXT,
          header_row INTEGER,
          col_map JSONB NOT NULL DEFAULT '{}'::jsonb,
          origin_uri TEXT,
          row_count BIGINT,
          ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          ingested_by UUID REFERENCES dms.users(id),
          UNIQUE (tenant_id, blob_hash, member)
        );

        CREATE TABLE dms.ingest_run (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID REFERENCES dms.spaces(id) ON DELETE SET NULL,
          status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'partial')),
          receipt JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID REFERENCES dms.users(id)
        );
        """
    )
    for table in ("source_ref", "ingest_run"):
        op.execute(f"REVOKE ALL ON dms.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON dms.{table} TO dms_viewer, dms_steward, dms_admin")
        op.execute(f"GRANT INSERT, UPDATE ON dms.{table} TO dms_steward, dms_admin")
        op.execute(f"GRANT DELETE ON dms.{table} TO dms_admin")
        op.execute(f"ALTER TABLE dms.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE dms.{table} FORCE ROW LEVEL SECURITY")
        for role in ("dms_viewer", "dms_steward", "dms_admin"):
            op.execute(
                f"""
                CREATE POLICY {table}_select_{role} ON dms.{table}
                  FOR SELECT TO {role}
                  USING (tenant_id::text = current_setting('dms.tenant_id', true));
                """
            )
        for role in ("dms_steward", "dms_admin"):
            op.execute(
                f"""
                CREATE POLICY {table}_insert_{role} ON dms.{table}
                  FOR INSERT TO {role}
                  WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
                """
            )
            op.execute(
                f"""
                CREATE POLICY {table}_update_{role} ON dms.{table}
                  FOR UPDATE TO {role}
                  USING (tenant_id::text = current_setting('dms.tenant_id', true))
                  WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
                """
            )
        op.execute(
            f"""
            CREATE POLICY {table}_delete_dms_admin ON dms.{table}
              FOR DELETE TO dms_admin
              USING (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.ingest_run CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.source_ref CASCADE")
