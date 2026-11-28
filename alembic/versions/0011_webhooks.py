"""Alembic revision: pipeline webhook subscriptions (FEAT-PIPELINE-STEP-01).

One row per registered URL. ``secret_ref`` is an OpenVault reference. This
table has no secret column and no row-value column. Events are append-only
ids and outcomes. No cross-schema foreign key. No hash chain.

Chain: ``0011_webhooks`` on ``0010_connections``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_webhooks"
down_revision: str | None = "0010_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = ("dms_viewer", "dms_steward", "dms_admin")
_WRITERS = ("dms_steward", "dms_admin")
_TENANT = "tenant_id::text = current_setting('dms.tenant_id', true)"


def _rls(table: str) -> None:
    op.execute(f"REVOKE ALL ON dms.{table} FROM PUBLIC")
    op.execute(f"GRANT SELECT ON dms.{table} TO {', '.join(_ROLES)}")
    op.execute(f"GRANT INSERT ON dms.{table} TO {', '.join(_WRITERS)}")
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


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.webhook_subscriptions (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id TEXT NOT NULL,
          url TEXT NOT NULL CHECK (
            char_length(url) <= 500
            AND position('@' in url) = 0
            AND url LIKE 'https://%'
          ),
          secret_ref TEXT NOT NULL CHECK (
            char_length(secret_ref) <= 128
            AND position('://' in secret_ref) = 0
          ),
          events TEXT[] NOT NULL,
          include_values BOOLEAN NOT NULL DEFAULT false,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX webhook_subscriptions_by_space
          ON dms.webhook_subscriptions (tenant_id, space_id);

        CREATE TABLE dms.webhook_events (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id TEXT NOT NULL,
          event TEXT NOT NULL CHECK (
            event IN ('answer.served', 'clarify.needed', 'benchmark.finished')
          ),
          outcome TEXT NOT NULL CHECK (
            outcome IN ('served', 'clarify', 'reconfirm', 'refused', 'error')
          ),
          route TEXT NOT NULL DEFAULT '',
          audit_id TEXT,
          run_id TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX webhook_events_by_space
          ON dms.webhook_events (tenant_id, space_id, created_at DESC);
        """
    )
    _rls("webhook_subscriptions")
    _rls("webhook_events")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.webhook_events CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.webhook_subscriptions CASCADE")
