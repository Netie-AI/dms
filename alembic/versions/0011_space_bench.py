"""Alembic revision: Space benchmarks (FEAT-BENCH-SPACE-01).

Ground-truth questions live here, not in the verified-query table, so
retrieval cannot read them. No cross-schema foreign key.

Chain: ``0011_space_bench`` on ``0010_connections``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_space_bench"
down_revision: str | None = "0010_connections"
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
        op.execute(
            f"""
            CREATE POLICY {table}_insert_{role} ON dms.{table}
              FOR INSERT TO {role}
              WITH CHECK ({_TENANT});
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_update_{role} ON dms.{table}
              FOR UPDATE TO {role}
              USING ({_TENANT})
              WITH CHECK ({_TENANT});
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_delete_{role} ON dms.{table}
              FOR DELETE TO {role}
              USING ({_TENANT});
            """
        )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.space_benchmarks (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id TEXT NOT NULL,
          question TEXT NOT NULL,
          gold_sql TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('valid', 'invalid')),
          invalid_reason TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL CHECK (source IN ('steward', 'verified_example')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX space_benchmarks_by_space
          ON dms.space_benchmarks (tenant_id, space_id, created_at);

        CREATE TABLE dms.space_bench_runs (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id TEXT NOT NULL,
          status TEXT NOT NULL CHECK (
            status IN ('queued', 'running', 'done', 'cancelled', 'failed')
          ),
          progress_done INTEGER NOT NULL DEFAULT 0,
          progress_total INTEGER NOT NULL DEFAULT 0,
          cancel_requested BOOLEAN NOT NULL DEFAULT false,
          tally_json TEXT NOT NULL DEFAULT '{}',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX space_bench_runs_by_space
          ON dms.space_bench_runs (tenant_id, space_id, created_at);

        CREATE TABLE dms.space_bench_results (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          run_id UUID NOT NULL REFERENCES dms.space_bench_runs(id) ON DELETE CASCADE,
          benchmark_id UUID NOT NULL REFERENCES dms.space_benchmarks(id) ON DELETE CASCADE,
          question TEXT NOT NULL,
          served_sql TEXT NOT NULL DEFAULT '',
          outcome TEXT NOT NULL CHECK (
            outcome IN (
              'correct', 'WRONG', 'clarify', 'reconfirm', 'abstain', 'invalid_gold'
            )
          ),
          bucket TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL DEFAULT '',
          gold_n INTEGER NOT NULL DEFAULT 0,
          served_n INTEGER NOT NULL DEFAULT 0,
          ordered_rows BOOLEAN NOT NULL DEFAULT false,
          review TEXT CHECK (review IS NULL OR review IN ('good', 'bad')),
          review_note TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX space_bench_results_by_run
          ON dms.space_bench_results (run_id);
        """
    )
    _rls("space_benchmarks")
    _rls("space_bench_runs")
    _rls("space_bench_results")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.space_bench_results CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.space_bench_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.space_benchmarks CASCADE")
