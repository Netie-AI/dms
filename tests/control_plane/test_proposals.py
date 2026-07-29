"""Versioned proposals: concurrent confirm, stale tokens, advisory locks."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import psycopg
import pytest
from dms_core.control_plane.advisory_lock import AdvisoryLockTimeout, advisory_lock
from dms_core.control_plane.proposals import (
    ConflictError,
    StaleTokenError,
    confirm_proposal_version,
    create_proposal_version,
)
from dms_core.control_plane.session import set_tenant_context

pytestmark = pytest.mark.usefixtures("migrated_db")


def _new_proposal(conn: psycopg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    set_tenant_context(conn, tenant_id, role="steward")
    pid = uuid.uuid4()
    conn.execute(
        "INSERT INTO dms.proposals (id, tenant_id) VALUES (%s, %s)",
        (pid, tenant_id),
    )
    return pid


def test_stale_token_after_revision(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    pid = _new_proposal(conn, tid)
    v1 = create_proposal_version(conn, proposal_id=pid, tenant_id=tid, diff={"a": 1})
    stale = v1["idempotency_token"]
    v2 = create_proposal_version(conn, proposal_id=pid, tenant_id=tid, diff={"a": 2})
    conn.commit()

    assert v1["idempotency_token"] != v2["idempotency_token"]
    set_tenant_context(conn, tid, role="steward")
    with pytest.raises(StaleTokenError):
        confirm_proposal_version(conn, tenant_id=tid, idempotency_token=stale)

    applied = confirm_proposal_version(
        conn, tenant_id=tid, idempotency_token=v2["idempotency_token"]
    )
    assert applied["status"] == "applied"
    conn.commit()


def test_concurrent_confirms_one_apply_one_409(
    migrated_db: str, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    with psycopg.connect(migrated_db) as setup:
        pid = _new_proposal(setup, tid)
        ver = create_proposal_version(
            setup, proposal_id=pid, tenant_id=tid, diff={"op": "set", "v": 1}
        )
        setup.commit()
        token = ver["idempotency_token"]

    results: list[Any] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        with psycopg.connect(migrated_db) as conn:
            set_tenant_context(conn, tid, role="steward")
            barrier.wait()
            try:
                out = confirm_proposal_version(
                    conn, tenant_id=tid, idempotency_token=token, lock_timeout_seconds=5.0
                )
                conn.commit()
                results.append(("ok", out))
            except ConflictError as exc:
                conn.rollback()
                results.append(("409", exc.status_code))
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                results.append(("err", type(exc).__name__, str(exc)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    kinds = sorted(r[0] for r in results)
    assert kinds == ["409", "ok"], results
    assert any(r[0] == "409" and r[1] == 409 for r in results)


def test_advisory_lock_second_writer_times_out(
    migrated_db: str, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    hold = threading.Event()
    held = threading.Event()
    timed_out = threading.Event()

    def holder() -> None:
        with psycopg.connect(migrated_db) as conn:
            with advisory_lock(conn, tid, "serving", timeout_seconds=2.0):
                held.set()
                hold.wait(timeout=5.0)

    def waiter() -> None:
        held.wait(timeout=5.0)
        with psycopg.connect(migrated_db) as conn:
            try:
                with advisory_lock(conn, tid, "serving", timeout_seconds=0.3):
                    pass
            except AdvisoryLockTimeout:
                timed_out.set()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=waiter)
    t1.start()
    t2.start()
    t2.join(timeout=10)
    hold.set()
    t1.join(timeout=10)
    assert timed_out.is_set()


def test_partial_unique_one_active_version(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    pid = _new_proposal(conn, tid)
    create_proposal_version(conn, proposal_id=pid, tenant_id=tid, diff={"x": 1})
    # Direct insert of a second active version must fail (partial unique index)
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            """
            INSERT INTO dms.proposal_versions (
              id, proposal_id, tenant_id, version_num, diff, idempotency_token, status
            ) VALUES (%s, %s, %s, 99, '{}'::jsonb, %s, 'pending_confirm')
            """,
            (uuid.uuid4(), pid, tid, uuid.uuid4().hex),
        )
    conn.rollback()
