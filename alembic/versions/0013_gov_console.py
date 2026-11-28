"""Alembic revision: manager governance records (DMS-GOV-01).

Budgets, usage readback, upload findings, and alerts. No secret value column.
No cross-schema foreign key. The ask question stays in ``dms.ask_audit``.

Chain: ``0013_gov_console`` on ``0012_sign``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_gov_console"
down_revision: str | None = "0012_sign"
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
        CREATE TABLE dms.gov_assignment (
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL,
          email TEXT NOT NULL DEFAULT '',
          gov_role TEXT NOT NULL CHECK (gov_role IN ('manager', 'member')),
          daily_usd DOUBLE PRECISION CHECK (daily_usd IS NULL OR daily_usd >= 0),
          monthly_usd DOUBLE PRECISION CHECK (monthly_usd IS NULL OR monthly_usd >= 0),
          daily_tokens BIGINT CHECK (daily_tokens IS NULL OR daily_tokens >= 0),
          monthly_tokens BIGINT CHECK (monthly_tokens IS NULL OR monthly_tokens >= 0),
          allow_ask BOOLEAN NOT NULL DEFAULT true,
          allow_create BOOLEAN NOT NULL DEFAULT true,
          allow_connectors BOOLEAN NOT NULL DEFAULT true,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (tenant_id, user_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE dms.gov_policy (
          tenant_id UUID PRIMARY KEY REFERENCES dms.tenants(id) ON DELETE CASCADE,
          upload_action TEXT NOT NULL CHECK (upload_action IN ('block', 'redact', 'warn')),
          redact_sensitive BOOLEAN NOT NULL DEFAULT false
        );
        """
    )
    op.execute(
        """
        CREATE TABLE dms.gov_spend (
          event_id TEXT PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL,
          spent_at TIMESTAMPTZ NOT NULL,
          tokens BIGINT NOT NULL DEFAULT 0 CHECK (tokens >= 0),
          cost_usd DOUBLE PRECISION CHECK (cost_usd IS NULL OR cost_usd >= 0),
          model TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT 'ov'
        );
        CREATE INDEX gov_spend_by_user ON dms.gov_spend (tenant_id, user_id, spent_at);
        """
    )
    op.execute(
        """
        CREATE TABLE dms.gov_finding (
          finding_id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL,
          file_sha256 TEXT NOT NULL,
          filename TEXT NOT NULL DEFAULT '',
          finding_type TEXT NOT NULL,
          action TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );
        CREATE INDEX gov_finding_by_time ON dms.gov_finding (tenant_id, created_at);
        """
    )
    op.execute(
        """
        CREATE TABLE dms.gov_alert (
          alert_id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL DEFAULT '',
          kind TEXT NOT NULL,
          summary TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );
        """
    )
    for table in (
        "gov_assignment",
        "gov_policy",
        "gov_spend",
        "gov_finding",
        "gov_alert",
    ):
        _rls(table)


def downgrade() -> None:
    for table in (
        "gov_alert",
        "gov_finding",
        "gov_spend",
        "gov_policy",
        "gov_assignment",
    ):
        op.execute(f"DROP TABLE IF EXISTS dms.{table} CASCADE")
