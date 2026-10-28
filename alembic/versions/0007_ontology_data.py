"""Alembic revision: revocable ontology items (ONTO-DATA-01).

``0003_document_chunks`` already owns revision 0003. This is the next head
after ``0006_onto_snapshot``. Tables: ``ontology_versions`` and
``ontology_items``. An item is proposed until a steward approves it, and a
revoke takes it out of the next ask.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_ontology_data"
down_revision: str | None = "0006_onto_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("ontology_items", "ontology_versions")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.ontology_versions (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID REFERENCES dms.spaces(id) ON DELETE CASCADE,
          source TEXT NOT NULL,
          provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_by UUID,
          ts TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX ontology_versions_by_space
          ON dms.ontology_versions (tenant_id, space_id, ts);

        CREATE TABLE dms.ontology_items (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID NOT NULL REFERENCES dms.ontology_versions(id) ON DELETE CASCADE,
          kind TEXT NOT NULL
            CHECK (kind IN ('object', 'link', 'measure', 'grain', 'pii')),
          payload JSONB NOT NULL,
          status TEXT NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed', 'approved', 'revoked')),
          source TEXT NOT NULL,
          provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_by UUID,
          approved_by UUID,
          ts TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX ontology_items_by_status
          ON dms.ontology_items (tenant_id, status, kind);
        CREATE INDEX ontology_items_by_version
          ON dms.ontology_items (version_id);
        """
    )
    for table in _TABLES:
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
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS dms.{table} CASCADE")
