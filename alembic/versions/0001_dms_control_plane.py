"""dms control plane: schema, RLS, versioned proposals

Revision ID: 0001_dms_control_plane
Revises:
Create Date: 2026-07-29

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_dms_control_plane"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tenant-scoped tables — RLS + FORCE (deny-by-default without matching GUC).
_TENANT_TABLES = (
    "departments",
    "memberships",
    "api_keys",
    "sessions",
    "spaces",
    "space_members",
    "data_sources",
    "acl_grants",
    "proposals",
    "proposal_versions",
    "ledger_ref",
    "query_run",
    "compute_pool",
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS dms")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # Application DB roles (NOLOGIN) — SET ROLE after auth; RLS still applies (FORCE).
    op.execute(
        """
        DO $$ BEGIN
          CREATE ROLE dms_viewer NOLOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        DO $$ BEGIN
          CREATE ROLE dms_steward NOLOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        DO $$ BEGIN
          CREATE ROLE dms_admin NOLOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE dms.tenants (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          slug TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE dms.users (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          email TEXT NOT NULL UNIQUE,
          display_name TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE dms.roles (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name TEXT NOT NULL UNIQUE
            CHECK (name IN ('viewer', 'steward', 'admin')),
          description TEXT
        );

        INSERT INTO dms.roles (name, description) VALUES
          ('viewer', 'Read-only within tenant'),
          ('steward', 'Propose and confirm amends'),
          ('admin', 'Tenant administration');

        CREATE TABLE dms.departments (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, name)
        );

        CREATE TABLE dms.memberships (
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES dms.users(id) ON DELETE CASCADE,
          role_id UUID NOT NULL REFERENCES dms.roles(id),
          department_id UUID REFERENCES dms.departments(id) ON DELETE SET NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, user_id)
        );

        CREATE TABLE dms.api_keys (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          role_id UUID NOT NULL REFERENCES dms.roles(id),
          key_hash TEXT NOT NULL,
          label TEXT NOT NULL,
          revoked_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE dms.sessions (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES dms.users(id) ON DELETE CASCADE,
          role_id UUID NOT NULL REFERENCES dms.roles(id),
          expires_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE dms.spaces (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          created_by UUID REFERENCES dms.users(id),
          state TEXT NOT NULL DEFAULT 'active'
            CHECK (state IN ('active', 'archived')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, name)
        );

        CREATE TABLE dms.space_members (
          space_id UUID NOT NULL REFERENCES dms.spaces(id) ON DELETE CASCADE,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES dms.users(id) ON DELETE CASCADE,
          PRIMARY KEY (space_id, user_id)
        );

        CREATE TABLE dms.data_sources (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID REFERENCES dms.spaces(id) ON DELETE SET NULL,
          kind TEXT NOT NULL,
          ref TEXT NOT NULL,
          scope TEXT NOT NULL CHECK (scope IN ('personal', 'team', 'company')),
          meta JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE dms.acl_grants (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          principal_user_id UUID REFERENCES dms.users(id) ON DELETE CASCADE,
          principal_department_id UUID REFERENCES dms.departments(id) ON DELETE CASCADE,
          resource_kind TEXT NOT NULL,
          resource_id UUID NOT NULL,
          permission TEXT NOT NULL CHECK (permission IN ('read', 'write', 'admin')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (
            (principal_user_id IS NOT NULL AND principal_department_id IS NULL)
            OR (principal_user_id IS NULL AND principal_department_id IS NOT NULL)
          )
        );

        CREATE TABLE dms.proposals (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID REFERENCES dms.spaces(id) ON DELETE SET NULL,
          created_by UUID REFERENCES dms.users(id),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE dms.proposal_versions (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          proposal_id UUID NOT NULL REFERENCES dms.proposals(id) ON DELETE CASCADE,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_num INT NOT NULL,
          diff JSONB NOT NULL DEFAULT '{}'::jsonb,
          idempotency_token TEXT NOT NULL,
          status TEXT NOT NULL
            CHECK (status IN (
              'draft', 'pending_confirm', 'applied', 'rejected', 'superseded'
            )),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (proposal_id, version_num),
          UNIQUE (idempotency_token)
        );

        -- Only one active (confirmable/draft) version per proposal.
        CREATE UNIQUE INDEX proposal_versions_one_active
          ON dms.proposal_versions (proposal_id)
          WHERE status IN ('draft', 'pending_confirm');

        -- Pointers to Cortex ledger entries only — no local hash chain.
        -- seq is a local ordering number for DMS-side reads.
        CREATE TABLE dms.ledger_ref (
          seq BIGSERIAL PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          cortex_entry_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, cortex_entry_id)
        );

        CREATE TABLE dms.query_run (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID REFERENCES dms.spaces(id) ON DELETE SET NULL,
          actor_user_id UUID REFERENCES dms.users(id),
          sql_text TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
          ledger_seq BIGINT REFERENCES dms.ledger_ref(seq),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE dms.compute_pool (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'duckdb',
          config JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, name)
        );
        """
    )

    # Grants — deny-by-default: no PUBLIC; role-scoped privileges.
    for table in ("tenants", "users", "roles", *_TENANT_TABLES):
        op.execute(f"REVOKE ALL ON dms.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON dms.{table} TO dms_viewer, dms_steward, dms_admin")
        if table not in ("tenants", "users", "roles"):
            op.execute(f"GRANT INSERT, UPDATE ON dms.{table} TO dms_steward, dms_admin")
            op.execute(f"GRANT DELETE ON dms.{table} TO dms_admin")
        else:
            op.execute(f"GRANT INSERT, UPDATE, DELETE ON dms.{table} TO dms_admin")

    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA dms TO dms_steward, dms_admin")
    op.execute("GRANT USAGE ON SCHEMA dms TO dms_viewer, dms_steward, dms_admin")

    # Login role (compose user `dms`) may SET ROLE into app roles.
    op.execute(
        """
        DO $$ BEGIN
          GRANT dms_viewer TO CURRENT_USER;
          GRANT dms_steward TO CURRENT_USER;
          GRANT dms_admin TO CURRENT_USER;
        EXCEPTION WHEN OTHERS THEN NULL; END $$;
        """
    )

    # RLS — tenant isolation via SET LOCAL dms.tenant_id
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE dms.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE dms.{table} FORCE ROW LEVEL SECURITY")
        # SELECT for all three app roles when GUC matches
        for role in ("dms_viewer", "dms_steward", "dms_admin"):
            op.execute(
                f"""
                CREATE POLICY {table}_select_{role} ON dms.{table}
                  FOR SELECT TO {role}
                  USING (tenant_id::text = current_setting('dms.tenant_id', true));
                """
            )
        # Writes: steward + admin
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
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS dms.{table} CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.roles CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.users CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.tenants CASCADE")
