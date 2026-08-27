"""Alembic revision: Space-scoped Copilot result artifacts (XLSX-ORCH-11 / dms#31).

Additive. New table only. Not ingested originals (those stay on data_sources /
document_chunks — AirGPT #20). This row is kind xlsx_result.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_space_artifacts"
down_revision: str | None = "0003_document_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.space_artifacts (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID NOT NULL REFERENCES dms.spaces(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK (kind = 'xlsx_result'),
          blob_key TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          origin_path TEXT,
          sheets JSONB NOT NULL DEFAULT '[]'::jsonb,
          complete BOOLEAN NOT NULL DEFAULT false,
          missing_families JSONB NOT NULL DEFAULT '[]'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX space_artifacts_by_space
          ON dms.space_artifacts (tenant_id, space_id);
        """
    )
    op.execute("REVOKE ALL ON dms.space_artifacts FROM PUBLIC")
    op.execute(
        "GRANT SELECT ON dms.space_artifacts TO dms_viewer, dms_steward, dms_admin"
    )
    op.execute(
        "GRANT INSERT, UPDATE ON dms.space_artifacts TO dms_steward, dms_admin"
    )
    op.execute("GRANT DELETE ON dms.space_artifacts TO dms_admin")
    op.execute("ALTER TABLE dms.space_artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dms.space_artifacts FORCE ROW LEVEL SECURITY")
    for role in ("dms_viewer", "dms_steward", "dms_admin"):
        op.execute(
            f"""
            CREATE POLICY space_artifacts_select_{role} ON dms.space_artifacts
              FOR SELECT TO {role}
              USING (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
    for role in ("dms_steward", "dms_admin"):
        op.execute(
            f"""
            CREATE POLICY space_artifacts_insert_{role} ON dms.space_artifacts
              FOR INSERT TO {role}
              WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
        op.execute(
            f"""
            CREATE POLICY space_artifacts_update_{role} ON dms.space_artifacts
              FOR UPDATE TO {role}
              USING (tenant_id::text = current_setting('dms.tenant_id', true))
              WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
    op.execute(
        """
        CREATE POLICY space_artifacts_delete_dms_admin ON dms.space_artifacts
          FOR DELETE TO dms_admin
          USING (tenant_id::text = current_setting('dms.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.space_artifacts CASCADE")
