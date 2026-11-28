"""Alembic revision: SCIM directory (SCIM-01).

Users, groups, manager mappings, the provisioning audit, and the bearer
reference. The bearer plaintext is not a column. ``token_sha256`` is the
verifier. The secret stays in OpenVault under ``cred_ref``.

No cross-schema foreign key.

Chain: ``0014_scim`` on ``0013_gov_console``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_scim"
down_revision: str | None = "0013_gov_console"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = ("dms_viewer", "dms_steward", "dms_admin")
_WRITERS = ("dms_steward", "dms_admin")
_TENANT = "tenant_id::text = current_setting('dms.tenant_id', true)"


def _rls(table: str) -> None:
    op.execute(f"REVOKE ALL ON dms.{table} FROM PUBLIC")
    op.execute(f"GRANT SELECT ON dms.{table} TO {', '.join(_ROLES)}")
    op.execute(f"GRANT INSERT, UPDATE, DELETE ON dms.{table} TO {', '.join(_WRITERS)}")
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
        for verb in ("INSERT", "UPDATE", "DELETE"):
            check = f"WITH CHECK ({_TENANT})" if verb != "DELETE" else ""
            using = f"USING ({_TENANT})" if verb != "INSERT" else ""
            op.execute(
                f"""
                CREATE POLICY {table}_{verb.lower()}_{role} ON dms.{table}
                  FOR {verb} TO {role}
                  {using}
                  {check};
                """
            )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.scim_token (
          tenant_id UUID PRIMARY KEY REFERENCES dms.tenants(id) ON DELETE CASCADE,
          cred_ref TEXT NOT NULL,
          token_sha256 TEXT NOT NULL CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
          rotated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE dms.scim_user (
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          id TEXT NOT NULL,
          user_name TEXT NOT NULL,
          external_id TEXT NOT NULL DEFAULT '',
          active BOOLEAN NOT NULL DEFAULT true,
          display_name TEXT NOT NULL DEFAULT '',
          given_name TEXT NOT NULL DEFAULT '',
          family_name TEXT NOT NULL DEFAULT '',
          department TEXT NOT NULL DEFAULT '',
          manager_ref TEXT NOT NULL DEFAULT '',
          emails_json TEXT NOT NULL DEFAULT '[]',
          role_name TEXT NOT NULL DEFAULT 'viewer',
          gov_role TEXT NOT NULL DEFAULT 'member',
          spaces_json TEXT,
          sso_user_id TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX scim_user_name_lower
          ON dms.scim_user (tenant_id, lower(user_name));
        """
    )
    op.execute(
        """
        CREATE TABLE dms.scim_group (
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          id TEXT NOT NULL,
          display_name TEXT NOT NULL,
          external_id TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE dms.scim_member (
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          group_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          PRIMARY KEY (tenant_id, group_id, user_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE dms.scim_map (
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          id TEXT NOT NULL,
          kind TEXT NOT NULL CHECK (kind IN ('department', 'group')),
          source_value TEXT NOT NULL,
          priority INT NOT NULL DEFAULT 100,
          role_name TEXT NOT NULL CHECK (role_name IN ('admin', 'steward', 'analyst', 'viewer')),
          gov_role TEXT NOT NULL CHECK (gov_role IN ('manager', 'member')),
          daily_usd DOUBLE PRECISION,
          monthly_usd DOUBLE PRECISION,
          daily_tokens BIGINT,
          monthly_tokens BIGINT,
          allow_ask BOOLEAN NOT NULL DEFAULT true,
          allow_create BOOLEAN NOT NULL DEFAULT true,
          allow_connectors BOOLEAN NOT NULL DEFAULT true,
          spaces_json TEXT NOT NULL DEFAULT '[]',
          PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE dms.scim_audit (
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          id TEXT NOT NULL,
          at TIMESTAMPTZ NOT NULL DEFAULT now(),
          action TEXT NOT NULL,
          resource_id TEXT NOT NULL DEFAULT '',
          actor TEXT NOT NULL DEFAULT '',
          detail TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (tenant_id, id)
        );
        """
    )
    for table in (
        "scim_token",
        "scim_user",
        "scim_group",
        "scim_member",
        "scim_map",
        "scim_audit",
    ):
        _rls(table)


def downgrade() -> None:
    for table in (
        "scim_audit",
        "scim_map",
        "scim_member",
        "scim_group",
        "scim_user",
        "scim_token",
    ):
        op.execute(f"DROP TABLE IF EXISTS dms.{table} CASCADE")
