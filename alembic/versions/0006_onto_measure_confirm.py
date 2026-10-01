"""Alembic revision: measure confirm workflow columns (ONTO-CONFIRM-01 / dms#283).

ALTER only. dms.onto_measure (0004) had no writers; this adds the spec fields,
named CHECKs, one index and a BEFORE UPDATE trigger that freezes a measure's
definition (aggregate, column, grain, source, fingerprint) and allows only the
four legal state transitions. No new table, no GRANT, no POLICY: the 0004
steward/admin UPDATE and viewer SELECT policies under FORCE RLS already cover it.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_onto_measure_confirm"
down_revision: str | None = "0005_onto_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINTS = (
    "onto_measure_aggregate_ck",
    "onto_measure_name_ck",
    "onto_measure_star_ck",
    "onto_measure_source_ck",
    "onto_measure_hash_ck",
    "onto_measure_desc_ck",
    "onto_measure_confirmed_ck",
    "onto_measure_rejected_ck",
)


def upgrade() -> None:
    # definition_hash / schema_fingerprint have no default on purpose: the table
    # has no writers, so NOT NULL is safe, and a deployed table holding rows
    # fails this migration loudly rather than inventing a hash.
    op.execute(
        """
        ALTER TABLE dms.onto_measure
          ADD COLUMN description TEXT NOT NULL DEFAULT '',
          ADD COLUMN source TEXT NOT NULL DEFAULT 'derived',
          ADD COLUMN definition_hash TEXT NOT NULL,
          ADD COLUMN schema_fingerprint TEXT NOT NULL,
          ADD COLUMN evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
          ADD COLUMN decided_by UUID REFERENCES dms.users(id),
          ADD COLUMN decided_at TIMESTAMPTZ,
          ADD COLUMN decision_reason TEXT,
          ADD COLUMN ledger_entry_id TEXT;
        """
    )
    op.execute(
        """
        ALTER TABLE dms.onto_measure
          ADD CONSTRAINT onto_measure_aggregate_ck
            CHECK (aggregate IN ('count', 'count_distinct', 'sum', 'avg', 'min', 'max')),
          ADD CONSTRAINT onto_measure_name_ck
            CHECK (name ~ '^[a-z][a-z0-9_]{1,47}$'),
          ADD CONSTRAINT onto_measure_star_ck
            CHECK (column_name <> '*' OR aggregate = 'count'),
          ADD CONSTRAINT onto_measure_source_ck
            CHECK (source IN ('derived', 'manual')),
          ADD CONSTRAINT onto_measure_hash_ck
            CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
          ADD CONSTRAINT onto_measure_desc_ck
            CHECK (char_length(description) <= 200),
          ADD CONSTRAINT onto_measure_confirmed_ck
            CHECK (
              state <> 'confirmed'
              OR (
                description <> ''
                AND decided_by IS NOT NULL
                AND decided_at IS NOT NULL
                AND coalesce(ledger_entry_id, '') <> ''
              )
            ),
          ADD CONSTRAINT onto_measure_rejected_ck
            CHECK (
              state <> 'rejected'
              OR (
                decided_by IS NOT NULL
                AND decided_at IS NOT NULL
                AND coalesce(decision_reason, '') <> ''
              )
            );
        """
    )
    op.execute(
        """
        CREATE INDEX onto_measure_by_version
          ON dms.onto_measure (tenant_id, version_id, state);
        """
    )
    op.execute(
        """
        CREATE FUNCTION dms.onto_measure_guard() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.version_id <> OLD.version_id
             OR NEW.aggregate <> OLD.aggregate OR NEW.column_name <> OLD.column_name
             OR NEW.grain <> OLD.grain OR NEW.source <> OLD.source
             OR NEW.schema_fingerprint <> OLD.schema_fingerprint
          THEN
            RAISE EXCEPTION 'onto_measure_definition_frozen';
          END IF;
          IF (OLD.state, NEW.state) IN (('proposed', 'confirmed'), ('rejected', 'confirmed')) THEN
            RETURN NEW;
          ELSIF (OLD.state, NEW.state) IN (('proposed', 'rejected'), ('confirmed', 'rejected')) THEN
            IF NEW.name <> OLD.name OR NEW.description <> OLD.description
               OR NEW.definition_hash <> OLD.definition_hash
               OR NEW.evidence IS DISTINCT FROM OLD.evidence
            THEN
              RAISE EXCEPTION 'onto_measure_definition_frozen';
            END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'onto_measure_illegal_transition: % -> %', OLD.state, NEW.state;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER onto_measure_guard
          BEFORE UPDATE ON dms.onto_measure
          FOR EACH ROW EXECUTE FUNCTION dms.onto_measure_guard();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS onto_measure_guard ON dms.onto_measure")
    op.execute("DROP FUNCTION IF EXISTS dms.onto_measure_guard()")
    op.execute("DROP INDEX IF EXISTS dms.onto_measure_by_version")
    for name in _CONSTRAINTS:
        op.execute(f"ALTER TABLE dms.onto_measure DROP CONSTRAINT IF EXISTS {name}")
    op.execute(
        """
        ALTER TABLE dms.onto_measure
          DROP COLUMN IF EXISTS description,
          DROP COLUMN IF EXISTS source,
          DROP COLUMN IF EXISTS definition_hash,
          DROP COLUMN IF EXISTS schema_fingerprint,
          DROP COLUMN IF EXISTS evidence,
          DROP COLUMN IF EXISTS decided_by,
          DROP COLUMN IF EXISTS decided_at,
          DROP COLUMN IF EXISTS decision_reason,
          DROP COLUMN IF EXISTS ledger_entry_id;
        """
    )
