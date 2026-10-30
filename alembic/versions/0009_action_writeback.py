"""Alembic revision: governed action writeback (ACT-01).

Three tables for one typed, steward-confirmed action:

* ``dms.action_proposals`` marks a ``dms.proposals`` row as an action and pins
  the sha256 of its validated spec, the one version it may have and the sha256
  of that version's whole diff. ``confirm_proposal_version`` refuses any
  proposal listed here unless the caller is the action apply path, and
  ``create_proposal_version`` refuses to add a second version to one.
* ``dms.action_writeback`` is the only place an action writes. It is owned by
  DMS. A customer source system and an Excel file are never written. ``value``
  is a canonical Decimal string, and NULL means "no override, the source value
  applies". ``row_version`` bumps on every write, which makes the apply
  precondition ABA-safe. There is no DELETE grant: a clear is an UPDATE to NULL.
* ``dms.action_decisions`` is append-only (the ``0005_ask_audit`` precedent):
  SELECT and INSERT, and no UPDATE or DELETE for any app role. An applied row
  must have verified the re-read. It records the actor, and its approval_ref
  needs three visible characters (zero-width ones do not count). One
  ``ledger_seq`` backs one decision. ``ledger_seq`` points at ``dms.ledger_ref``;
  the one hash chain is in Cortex (hard rule 3). ``before_hash`` and
  ``after_hash`` are state digests, not a chain.

The append-only guarantee holds for the app roles only; the table owner and a
superuser can still edit, as with ``0005_ask_audit``.

No cross-schema foreign key. RLS ENABLE + FORCE and the tenant policy loop are
copied from ``0001`` / ``0007``.

Chain: ``0009_action_writeback`` on ``0008_ontology_approval_ref``. PR #461
adds ``0007_onto_measure_confirm`` on ``0006_onto_snapshot``; whichever lands
second renumbers so alembic keeps one head.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_action_writeback"
down_revision: str | None = "0008_ontology_approval_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = ("dms_viewer", "dms_steward", "dms_admin")
_WRITERS = ("dms_steward", "dms_admin")
_TENANT = "tenant_id::text = current_setting('dms.tenant_id', true)"
#: What does not count toward an approval_ref's 3 characters: whitespace, control
#: characters and the zero-width / invisible format characters (a Postgres ARE
#: bracket). The Python check in ``actions._require_ref`` is the first line.
_INVISIBLE = r"[[:space:][:cntrl:]\u00ad\u180e\u200b-\u200f\u2028-\u202f\u205f-\u206f\u3000\ufeff]"


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
        CREATE TABLE dms.action_proposals (
          proposal_id UUID PRIMARY KEY
            REFERENCES dms.proposals(id) ON DELETE CASCADE,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          action_type TEXT NOT NULL,
          source_kind TEXT NOT NULL,
          spec_sha TEXT NOT NULL,
          evidence_audit_id TEXT,
          version_id UUID NOT NULL UNIQUE REFERENCES dms.proposal_versions(id),
          diff_sha TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE dms.action_writeback (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          object_type TEXT NOT NULL,
          object_key JSONB NOT NULL,
          object_key_hash TEXT NOT NULL,
          field TEXT NOT NULL,
          value TEXT,
          row_version INT NOT NULL CHECK (row_version >= 1),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          last_version_id UUID REFERENCES dms.proposal_versions(id),
          UNIQUE (tenant_id, object_type, object_key_hash, field)
        );

        CREATE TABLE dms.action_decisions (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID,
          proposal_id UUID NOT NULL,
          proposal_version_id UUID NOT NULL UNIQUE
            REFERENCES dms.proposal_versions(id),
          decision TEXT NOT NULL CHECK (decision IN ('applied', 'declined')),
          action_type TEXT NOT NULL,
          object_type TEXT NOT NULL,
          object_key JSONB NOT NULL,
          field TEXT NOT NULL,
          op TEXT NOT NULL CHECK (op IN ('set', 'clear')),
          before JSONB,
          after JSONB,
          before_hash TEXT,
          after_hash TEXT,
          rows_changed INT NOT NULL CHECK (rows_changed IN (0, 1)),
          verified BOOLEAN NOT NULL,
          approval_ref TEXT NOT NULL CHECK (
            length(regexp_replace(approval_ref, '@INVISIBLE@', '', 'g')) >= 3
          ),
          actor_id TEXT NOT NULL CHECK (length(btrim(actor_id)) > 0),
          evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
          cortex_entry_id TEXT NOT NULL,
          ledger_seq BIGINT NOT NULL UNIQUE REFERENCES dms.ledger_ref(seq),
          decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (
            decision = 'declined'
            OR (verified AND rows_changed = 1 AND after_hash IS NOT NULL)
          )
        );

        CREATE INDEX action_decisions_by_time
          ON dms.action_decisions (tenant_id, decided_at);
        """.replace("@INVISIBLE@", _INVISIBLE)
    )
    _rls("action_proposals", update=False)
    _rls("action_writeback", update=True)
    # Append-only: SELECT and INSERT, nothing that edits or removes.
    _rls("action_decisions", update=False)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dms.action_decisions CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.action_writeback CASCADE")
    op.execute("DROP TABLE IF EXISTS dms.action_proposals CASCADE")
