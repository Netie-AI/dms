"""Alembic revision: space-scoped document chunk index (RAG-01 / dms#24)."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_document_chunks"
down_revision: str | None = "0002_source_ref_ingest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.document_chunks (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID NOT NULL REFERENCES dms.spaces(id) ON DELETE CASCADE,
          source_id UUID NOT NULL REFERENCES dms.data_sources(id) ON DELETE CASCADE,
          chunk_index INTEGER NOT NULL,
          content TEXT NOT NULL,
          blob_key TEXT,
          embed_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, space_id, source_id, chunk_index)
        );

        CREATE INDEX document_chunks_by_space
          ON dms.document_chunks (tenant_id, space_id);
        """
    )
    op.execute("REVOKE ALL ON dms.document_chunks FROM PUBLIC")
    op.execute(
        "GRANT SELECT ON dms.document_chunks TO dms_viewer, dms_steward, dms_admin"
    )
    op.execute(
        "GRANT INSERT, UPDATE ON dms.document_chunks TO dms_steward, dms_admin"
    )
    op.execute("GRANT DELETE ON dms.document_chunks TO dms_admin")
    op.execute("ALTER TABLE dms.document_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dms.document_chunks FORCE ROW LEVEL SECURITY")
    for role in ("dms_viewer", "dms_steward", "dms_admin"):
        op.execute(
            f"""
            CREATE POLICY document_chunks_select_{role} ON dms.document_chunks
              FOR SELECT TO {role}
              USING (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
    for role in ("dms_steward", "dms_admin"):
        op.execute(
            f"""
            CREATE POLICY document_chunks_insert_{role} ON dms.document_chunks
              FOR INSERT TO {role}
              WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
        op.execute(
            f"""
            CREATE POLICY document_chunks_update_{role} ON dms.document_chunks
              FOR UPDATE TO {role}
              USING (tenant_id::text = current_setting('dms.tenant_id', true))
              WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
    op.execute(
        """
        CREATE POLICY document_chunks_delete_dms_admin ON dms.document_chunks
          FOR DELETE TO dms_admin
          USING (tenant_id::text = current_setting('dms.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.document_chunks CASCADE")
