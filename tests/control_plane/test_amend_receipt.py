"""Confirming an amend without a receipt must not leave an applied version.

The ledger append used to sit in a bare ``except`` that set
``ledger_entry_id=None`` and committed regardless, so a confirm whose receipt
failed still answered 200 with status "applied" and left an accepted proposal
version that nothing in the ledger points at. That is a governed write with no
record that it happened, produced in precisely the outage where the record
matters (R-0011).

The append now runs inside the transaction, before the commit, so a failed
receipt rolls the status flip back.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from dms_core.control_plane.proposals import (
    ConflictError,
    confirm_proposal_version,
    create_proposal_version,
)
from dms_core.control_plane.session import set_tenant_context

pytestmark = pytest.mark.usefixtures("migrated_db")


def _proposal(conn: psycopg.Connection, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    set_tenant_context(conn, tenant_id, role="steward")
    proposal_id = uuid.uuid4()
    conn.execute(
        """
        INSERT INTO dms.proposals (id, tenant_id, created_by)
        VALUES (%s, %s, %s)
        """,
        (proposal_id, tenant_id, user_id),
    )
    return create_proposal_version(
        conn,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        diff={"table": "inventory", "set": {"reorder_level_kg": 10}},
    )


def test_rollback_after_confirm_leaves_the_version_unapplied(
    conn: psycopg.Connection, two_tenants: dict
) -> None:
    """The transactional property the route now depends on.

    If the ledger append raises after ``confirm_proposal_version`` and before the
    commit, the status flip must not survive - otherwise the route's fail-closed
    receipt would be decorative.
    """
    tenant, user = two_tenants["alpha"], two_tenants["user"]
    ver = _proposal(conn, tenant, user)
    conn.commit()

    set_tenant_context(conn, tenant, role="steward")
    confirm_proposal_version(
        conn, tenant_id=tenant, idempotency_token=ver["idempotency_token"]
    )
    conn.rollback()  # stand-in for the failed ledger append

    set_tenant_context(conn, tenant, role="steward")
    status = conn.execute(
        "SELECT status FROM dms.proposal_versions WHERE id = %s", (ver["id"],)
    ).fetchone()
    assert status is not None
    assert status[0] == "pending_confirm", "a rolled-back confirm left the version applied"


def test_the_token_still_works_after_a_rolled_back_confirm(
    conn: psycopg.Connection, two_tenants: dict
) -> None:
    """A receipt failure must be retryable, not a dead proposal.

    Refusing the write is only correct if the customer can try again once the
    ledger is back - otherwise fail-closed would burn the token and turn an
    outage into lost work (R-0005).
    """
    tenant, user = two_tenants["alpha"], two_tenants["user"]
    ver = _proposal(conn, tenant, user)
    conn.commit()

    set_tenant_context(conn, tenant, role="steward")
    confirm_proposal_version(
        conn, tenant_id=tenant, idempotency_token=ver["idempotency_token"]
    )
    conn.rollback()

    set_tenant_context(conn, tenant, role="steward")
    again = confirm_proposal_version(
        conn, tenant_id=tenant, idempotency_token=ver["idempotency_token"]
    )
    assert again["status"] == "applied"
    conn.commit()


def test_second_confirm_of_a_committed_version_is_a_conflict(
    conn: psycopg.Connection, two_tenants: dict
) -> None:
    """Apply-once still holds once the receipt succeeded."""
    tenant, user = two_tenants["alpha"], two_tenants["user"]
    ver = _proposal(conn, tenant, user)
    conn.commit()

    set_tenant_context(conn, tenant, role="steward")
    confirm_proposal_version(
        conn, tenant_id=tenant, idempotency_token=ver["idempotency_token"]
    )
    conn.commit()

    set_tenant_context(conn, tenant, role="steward")
    with pytest.raises(ConflictError):
        confirm_proposal_version(
            conn, tenant_id=tenant, idempotency_token=ver["idempotency_token"]
        )
