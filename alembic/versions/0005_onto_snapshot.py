"""Alembic revision: ontology measurements per version (ONTO-DERIVE-01 / dms#277).

One row per derive: the derived body (names, keys, types; never rows, never
credentials) and what ``Ontology.verify`` found. Append-only; the latest row
for a version is what the ask path reads.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_onto_snapshot"
down_revision: str | None = "0004_ontology_store"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.onto_snapshot (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID NOT NULL REFERENCES dms.ontology_version(id) ON DELETE CASCADE,
          body JSONB NOT NULL,
          violations JSONB NOT NULL DEFAULT '[]'::jsonb,
          verified BOOLEAN NOT NULL,
          measured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (NOT verified OR jsonb_array_length(violations) = 0)
        );

        CREATE INDEX onto_snapshot_by_version
          ON dms.onto_snapshot (tenant_id, version_id, measured_at);
        """
    )
    op.execute("REVOKE ALL ON dms.onto_snapshot FROM PUBLIC")
    op.execute("GRANT SELECT ON dms.onto_snapshot TO dms_viewer, dms_steward, dms_admin")
    # Append-only: no UPDATE grant for anyone.
    op.execute("GRANT INSERT ON dms.onto_snapshot TO dms_steward, dms_admin")
    op.execute("GRANT DELETE ON dms.onto_snapshot TO dms_admin")
    op.execute("ALTER TABLE dms.onto_snapshot ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dms.onto_snapshot FORCE ROW LEVEL SECURITY")
    for role in ("dms_viewer", "dms_steward", "dms_admin"):
        op.execute(
            f"""
            CREATE POLICY onto_snapshot_select_{role} ON dms.onto_snapshot
              FOR SELECT TO {role}
              USING (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
    for role in ("dms_steward", "dms_admin"):
        op.execute(
            f"""
            CREATE POLICY onto_snapshot_insert_{role} ON dms.onto_snapshot
              FOR INSERT TO {role}
              WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
    op.execute(
        """
        CREATE POLICY onto_snapshot_delete_dms_admin ON dms.onto_snapshot
          FOR DELETE TO dms_admin
          USING (tenant_id::text = current_setting('dms.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.onto_snapshot CASCADE")
