"""BANK-02 (dms#269): ``dms.ask_audit`` is append-only and tenant-isolated.

The route tests run on the in-memory store. This is the Postgres half: a row
round-trips through ``PostgresAskAuditStore``, one tenant cannot read another's
asks, and no app role can edit or delete a recorded ask. The last one is the
point of the table: an audit record the application can rewrite is not one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from dms_core.control_plane.ask_audit import (
    AskAuditRecord,
    PostgresAskAuditStore,
)
from dms_core.control_plane.session import set_tenant_context

pytestmark = pytest.mark.usefixtures("migrated_db")


def _rec(**over: object) -> AskAuditRecord:
    base: dict[str, object] = {
        "ask_id": str(uuid.uuid4()),
        "asked_at": datetime(2026, 10, 5, 9, 30, 15, 123456, tzinfo=UTC),
        "actor": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "actor_kind": "deployment",
        "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "question": "Where is SKU-00397 stored?",
        "executed_sql": "SELECT * FROM locations",
        "tables_read": ("locations",),
        "badge": "validated",
        "badge_level": "L0_CERTIFIED",
        "abstain_reason": "",
        "row_count": 2,
        "cortex_entry_id": "led_1",
        "ask_mode": "live",
    }
    base.update(over)
    return AskAuditRecord(**base)  # type: ignore[arg-type]


def test_a_row_round_trips_through_postgres(migrated_db: str, two_tenants: dict) -> None:
    store = PostgresAskAuditStore(migrated_db, tenant_id=two_tenants["alpha"])
    first = _rec()
    second = _rec(
        asked_at=datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
        badge="abstain",
        badge_level="ABSTAIN",
        abstain_reason="GEN-01: ledger_entry_missing; could not plan",
        executed_sql="",
        tables_read=(),
        row_count=0,
        cortex_entry_id="",
    )
    store.record(second)
    store.record(first)

    got = store.list_between(since=None, until=None, limit=10)

    assert got == [first, second], "ordered by asked_at, every field intact"
    window = store.list_between(
        since=datetime(2026, 10, 5, 10, 0, tzinfo=UTC), until=None, limit=10
    )
    assert window == [second]


def test_recording_the_same_ask_twice_keeps_one_row(migrated_db: str, two_tenants: dict) -> None:
    store = PostgresAskAuditStore(migrated_db, tenant_id=two_tenants["alpha"])
    rec = _rec()
    store.record(rec)
    store.record(rec)

    assert store.list_between(since=None, until=None, limit=10) == [rec]


def test_one_tenant_cannot_read_anothers_asks(migrated_db: str, two_tenants: dict) -> None:
    alpha = PostgresAskAuditStore(migrated_db, tenant_id=two_tenants["alpha"])
    beta = PostgresAskAuditStore(migrated_db, tenant_id=two_tenants["beta"])
    alpha.record(_rec(question="alpha only"))

    assert [r.question for r in alpha.list_between(since=None, until=None, limit=10)] == [
        "alpha only"
    ]
    assert beta.list_between(since=None, until=None, limit=10) == []


@pytest.mark.parametrize("role", ["steward", "admin"])
def test_no_app_role_can_edit_or_delete_a_recorded_ask(
    conn: psycopg.Connection, two_tenants: dict, role: str
) -> None:
    tenant = two_tenants["alpha"]
    ask_id = uuid.uuid4()
    set_tenant_context(conn, tenant, role=role)  # type: ignore[arg-type]
    conn.execute(
        """
        INSERT INTO dms.ask_audit
              (ask_id, tenant_id, asked_at, actor, actor_kind, question, badge)
        VALUES (%s, %s, now(), 'deployment-x', 'deployment', 'original question', 'validated')
        """,
        (ask_id, tenant),
    )
    conn.commit()

    for statement in (
        "UPDATE dms.ask_audit SET question = 'rewritten' WHERE ask_id = %s",
        "DELETE FROM dms.ask_audit WHERE ask_id = %s",
    ):
        set_tenant_context(conn, tenant, role=role)  # type: ignore[arg-type]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement, (ask_id,))
        conn.rollback()

    set_tenant_context(conn, tenant, role="viewer")
    row = conn.execute("SELECT question FROM dms.ask_audit WHERE ask_id = %s", (ask_id,)).fetchone()
    assert row == ("original question",)


def test_a_viewer_can_read_but_not_record(conn: psycopg.Connection, two_tenants: dict) -> None:
    tenant = two_tenants["alpha"]
    set_tenant_context(conn, tenant, role="viewer")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            """
            INSERT INTO dms.ask_audit
              (ask_id, tenant_id, asked_at, actor, actor_kind, question, badge)
            VALUES (%s, %s, now(), 'x', 'deployment', 'q', 'validated')
            """,
            (uuid.uuid4(), tenant),
        )
    conn.rollback()


def test_a_row_for_another_tenant_cannot_be_written(
    conn: psycopg.Connection, two_tenants: dict
) -> None:
    set_tenant_context(conn, two_tenants["alpha"], role="steward")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            """
            INSERT INTO dms.ask_audit
              (ask_id, tenant_id, asked_at, actor, actor_kind, question, badge)
            VALUES (%s, %s, now(), 'x', 'deployment', 'q', 'validated')
            """,
            (uuid.uuid4(), two_tenants["beta"]),
        )
    conn.rollback()
