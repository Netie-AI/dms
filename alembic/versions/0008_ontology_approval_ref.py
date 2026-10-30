"""Alembic revision: text approval fields on ontology items (ONTO-PG-01).

``0007_ontology_data`` stores ``approved_by`` as UUID and has no
``approval_ref``. An approval actor is text, and one approval of a version
needs a shared text reference. This revision adds the column and widens
``approved_by`` to text. ``0007`` is not edited.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_ontology_approval_ref"
down_revision: str | None = "0007_ontology_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE dms.ontology_items
          ALTER COLUMN approved_by TYPE TEXT USING approved_by::text;
        ALTER TABLE dms.ontology_items
          ADD COLUMN approval_ref TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE dms.ontology_items
          DROP COLUMN IF EXISTS approval_ref;
        ALTER TABLE dms.ontology_items
          ALTER COLUMN approved_by TYPE UUID USING (
            CASE
              WHEN approved_by IS NULL OR btrim(approved_by) = '' THEN NULL
              WHEN approved_by ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN approved_by::uuid
              ELSE NULL
            END
          );
        """
    )
