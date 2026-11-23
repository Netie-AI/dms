"""Alembic revision: onboarding item kinds (FDE-ONBOARD-01).

``0007_ontology_data`` limits ``dms.ontology_items.kind`` to five values. An
onboarding run also proposes ``column_type``, ``value_map`` and
``verified_query`` items. This revision widens the CHECK. ``0007`` is not edited.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_onboard_item_kinds"
down_revision: str | None = "0009_action_writeback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Postgres names an inline column CHECK ``<table>_<column>_check``.
_CONSTRAINT = "ontology_items_kind_check"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE dms.ontology_items DROP CONSTRAINT {_CONSTRAINT};
        ALTER TABLE dms.ontology_items
          ADD CONSTRAINT {_CONSTRAINT}
          CHECK (kind IN (
            'object', 'link', 'measure', 'grain', 'pii',
            'column_type', 'value_map', 'verified_query'
          ));
        """
    )


def downgrade() -> None:
    # Rows of the three new kinds are not deleted here. Re-adding the narrower
    # CHECK fails naturally while any exist, so a downgrade cannot lose data.
    op.execute(
        f"""
        ALTER TABLE dms.ontology_items DROP CONSTRAINT {_CONSTRAINT};
        ALTER TABLE dms.ontology_items
          ADD CONSTRAINT {_CONSTRAINT}
          CHECK (kind IN ('object', 'link', 'measure', 'grain', 'pii'));
        """
    )
