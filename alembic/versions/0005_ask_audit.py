"""Alembic revision: per-ask audit record (BANK-02 / dms#269).

One row per ask: who (the deployment identity under DR-0004 Option A), what was
asked, the executed SQL, the tables it read, and what came back. Append-only: the
app roles get SELECT and INSERT and no UPDATE or DELETE, so a row cannot be
edited or removed through the application. This is a record, not a ledger: no
hash chain here (the one chain is in Cortex), only a ``cortex_entry_id`` pointer.

If another migration also descends from ``0004_ontology_store`` (PR #316 adds
0005_onto_snapshot), the later of the two to merge re-points ``down_revision``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_ask_audit"
down_revision: str | None = "0004_ontology_store"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.ask_audit (
          ask_id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          asked_at TIMESTAMPTZ NOT NULL,
          actor TEXT NOT NULL,
          actor_kind TEXT NOT NULL CHECK (actor_kind IN ('person', 'deployment')),
          space_id TEXT NOT NULL DEFAULT '',
          question TEXT NOT NULL,
          executed_sql TEXT NOT NULL DEFAULT '',
          tables_read TEXT[] NOT NULL DEFAULT '{}',
          badge TEXT NOT NULL CHECK (badge IN ('validated', 'abstain', 'error')),
          badge_level TEXT NOT NULL DEFAULT '',
          abstain_reason TEXT NOT NULL DEFAULT '',
          row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
          cortex_entry_id TEXT NOT NULL DEFAULT '',
          ask_mode TEXT NOT NULL DEFAULT 'live'
        );

        CREATE INDEX ask_audit_by_time ON dms.ask_audit (tenant_id, asked_at);
        """
    )
    op.execute("REVOKE ALL ON dms.ask_audit FROM PUBLIC")
    op.execute("GRANT SELECT ON dms.ask_audit TO dms_viewer, dms_steward, dms_admin")
    # Append-only: INSERT for the roles that write, and nothing that edits or removes.
    op.execute("GRANT INSERT ON dms.ask_audit TO dms_steward, dms_admin")
    op.execute("ALTER TABLE dms.ask_audit ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dms.ask_audit FORCE ROW LEVEL SECURITY")
    for role in ("dms_viewer", "dms_steward", "dms_admin"):
        op.execute(
            f"""
            CREATE POLICY ask_audit_select_{role} ON dms.ask_audit
              FOR SELECT TO {role}
              USING (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )
    for role in ("dms_steward", "dms_admin"):
        op.execute(
            f"""
            CREATE POLICY ask_audit_insert_{role} ON dms.ask_audit
              FOR INSERT TO {role}
              WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.ask_audit CASCADE")
