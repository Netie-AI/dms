"""Alembic revision: persisted SQL connections (R2-CONNECT-CORE-01).

One row per connected source. The credential is an OpenVault reference
(``cred_ref``). This table stores that reference and nothing else about the login.
Grants are steward-editable rows (``granted`` flips; the row stays). Audit
and the auto-ontology job are append-only. No cross-schema foreign key.

Chain: ``0010_connections`` on ``0009_action_writeback``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_connections"
down_revision: str | None = "0009_action_writeback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = ("dms_viewer", "dms_steward", "dms_admin")
_WRITERS = ("dms_steward", "dms_admin")
_TENANT = "tenant_id::text = current_setting('dms.tenant_id', true)"


def _rls(table: str, *, update: bool) -> None:
    op.execute(f"REVOKE ALL ON dms.{table} FROM PUBLIC")
    op.execute(f"GRANT SELECT ON dms.{table} TO {', '.join(_ROLES)}")
    privs = "INSERT, UPDATE" if update else "INSERT"
    op.execute(f"GRANT {privs} ON dms.{table} TO {', '.join(_WRITERS)}")
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
        if update:
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
        CREATE TABLE dms.connect_sources (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id TEXT NOT NULL,
          connector_id TEXT NOT NULL,
          kind TEXT NOT NULL CHECK (
            kind IN ('postgresql', 'mysql', 'mariadb', 'sqlserver', 'sqlite')
          ),
          host TEXT NOT NULL CHECK (
            position('@' in host) = 0 AND position('://' in host) = 0
          ),
          port INTEGER NOT NULL CHECK (port >= 0 AND port <= 65535),
          database_name TEXT NOT NULL,
          cred_ref TEXT NOT NULL,
          mode TEXT NOT NULL CHECK (mode IN ('live', 'extract')),
          read_only BOOLEAN NOT NULL DEFAULT true,
          server_version TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX connect_sources_by_tenant ON dms.connect_sources (tenant_id);

        CREATE TABLE dms.connect_grants (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          source_id UUID NOT NULL REFERENCES dms.connect_sources(id) ON DELETE CASCADE,
          table_name TEXT NOT NULL,
          granted BOOLEAN NOT NULL DEFAULT true,
          UNIQUE (source_id, table_name)
        );

        CREATE TABLE dms.connect_audit (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          source_id UUID,
          action TEXT NOT NULL,
          actor TEXT NOT NULL,
          token_id TEXT NOT NULL,
          detail TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (char_length(detail) <= 80)
        );

        CREATE TABLE dms.connect_onto_jobs (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          source_id UUID NOT NULL REFERENCES dms.connect_sources(id) ON DELETE CASCADE,
          status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested')),
          tables TEXT[] NOT NULL DEFAULT '{}',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    _rls("connect_sources", update=False)
    _rls("connect_grants", update=True)
    _rls("connect_audit", update=False)
    _rls("connect_onto_jobs", update=False)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.connect_onto_jobs CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.connect_audit CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.connect_grants CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.connect_sources CASCADE")
