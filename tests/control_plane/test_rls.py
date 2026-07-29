"""RLS: cross-tenant reads blocked under every app role."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from dms_core.control_plane.session import set_tenant_context

pytestmark = pytest.mark.usefixtures("migrated_db")


@pytest.mark.parametrize("role", ["viewer", "steward", "admin"])
def test_rls_blocks_cross_tenant_reads(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID], role: str
) -> None:
    alpha = two_tenants["alpha"]
    beta = two_tenants["beta"]

    set_tenant_context(conn, alpha, role=role)  # type: ignore[arg-type]
    rows = conn.execute("SELECT tenant_id FROM dms.spaces").fetchall()
    assert {r[0] for r in rows} == {alpha}

    set_tenant_context(conn, beta, role=role)  # type: ignore[arg-type]
    rows = conn.execute("SELECT tenant_id FROM dms.spaces").fetchall()
    assert {r[0] for r in rows} == {beta}

    # Empty GUC / wrong tenant → deny (no rows)
    set_tenant_context(conn, uuid.uuid4(), role=role)  # type: ignore[arg-type]
    assert conn.execute("SELECT count(*) FROM dms.spaces").fetchone()[0] == 0


def test_ledger_ref_is_pointer_only(conn: psycopg.Connection, two_tenants: dict) -> None:
    """ledger_ref has cortex_entry_id + local seq — no hash-chain columns."""
    cols = {
        r[0]
        for r in conn.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'dms' AND table_name = 'ledger_ref'
            """
        ).fetchall()
    }
    assert "cortex_entry_id" in cols
    assert "seq" in cols
    assert "entry_hash" not in cols
    assert "prev_hash" not in cols

    tid = two_tenants["alpha"]
    set_tenant_context(conn, tid, role="steward")
    conn.execute(
        """
        INSERT INTO dms.ledger_ref (tenant_id, cortex_entry_id)
        VALUES (%s, 'cortex-entry-1')
        """,
        (tid,),
    )
    seq = conn.execute(
        "SELECT seq FROM dms.ledger_ref WHERE cortex_entry_id = 'cortex-entry-1'"
    ).fetchone()[0]
    assert isinstance(seq, int)
