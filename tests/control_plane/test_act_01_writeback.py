"""ACT-01. No skip, no xfail. CI runs this file inside pytest tests/.

Governed action core against Postgres: one typed action type applied to the
DMS-owned writeback table, re-read verified, before/after state digests, and a
decline. No route calls this. ``DMS_ACTIONS`` defaults off.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

import psycopg
import pytest
from dms_core.control_plane.action_types import state_hash, validate_spec
from dms_core.control_plane.actions import (
    ActionsDisabled,
    ApprovalRefRequired,
    PreconditionFailed,
    TokenDiffMismatch,
    VerifyFailed,
    apply_action_version,
    decline_action_version,
    get_decision,
    is_action_version,
    list_overrides,
    propose_action,
    record_decision,
)
from dms_core.control_plane.advisory_lock import AdvisoryLockTimeout
from dms_core.control_plane.proposals import (
    ActionRequiresApply,
    ConflictError,
    StaleTokenError,
    confirm_proposal_version,
    create_proposal_version,
    decline_proposal_version,
)
from dms_core.control_plane.session import set_tenant_context

pytestmark = pytest.mark.usefixtures("migrated_db")

KEY = {"sku": "SKU-BETA", "location_id": "WH-B"}


@pytest.fixture
def actions_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ACTIONS", "1")


def _propose(
    conn: psycopg.Connection,
    tenants: dict[str, uuid.UUID],
    *,
    value: str | None = "450.00",
    source_value: str | None = "400",
    op: str = "set",
    tenant: str = "alpha",
) -> dict[str, Any]:
    tid = tenants[tenant]
    set_tenant_context(conn, tid, role="steward")
    spec = validate_spec(
        {
            "action_type": "inventory.set_reorder_level",
            "object_key": dict(KEY),
            "value": value,
            "op": op,
            "reason": "lead time doubled",
        }
    )
    out = propose_action(
        conn,
        tenant_id=tid,
        space_id=None,
        actor_id=tenants["user"],
        spec=spec,
        source_value=source_value,
        evidence={"audit_id": "ask_0001", "question": "reorder levels below cover"},
    )
    conn.commit()
    return out


def _apply(
    conn: psycopg.Connection, tid: uuid.UUID, token: str, ref: str = "CAB-2026-10-29"
) -> dict[str, Any]:
    set_tenant_context(conn, tid, role="steward")
    return dict(
        apply_action_version(
            conn,
            tenant_id=tid,
            idempotency_token=token,
            actor_id="steward-1",
            approval_ref=ref,
        )
    )


def _rows(conn: psycopg.Connection, tid: uuid.UUID) -> list[tuple[Any, ...]]:
    set_tenant_context(conn, tid, role="steward")
    return conn.execute(
        "SELECT value, row_version FROM dms.action_writeback WHERE tenant_id = %s",
        (tid,),
    ).fetchall()


def _status(conn: psycopg.Connection, tid: uuid.UUID, token: str) -> str:
    set_tenant_context(conn, tid, role="steward")
    row = conn.execute(
        "SELECT status FROM dms.proposal_versions WHERE idempotency_token = %s", (token,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def _count(conn: psycopg.Connection, tid: uuid.UUID, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM dms.{table} WHERE tenant_id = %s", (tid,)).fetchone()
    assert row is not None
    return int(row[0])


def test_flag_defaults_off(
    conn: psycopg.Connection,
    two_tenants: dict[str, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_ACTIONS", "1")
    prop = _propose(conn, two_tenants)
    tid = two_tenants["alpha"]
    for off in (None, "true", "yes", " 0"):
        if off is None:
            monkeypatch.delenv("DMS_ACTIONS", raising=False)
        else:
            monkeypatch.setenv("DMS_ACTIONS", off)
        from dms_core.control_plane.action_types import actions_enabled

        assert actions_enabled() is False
        with pytest.raises(ActionsDisabled):
            _apply(conn, tid, prop["idempotency_token"])
        conn.rollback()
    assert _rows(conn, tid) == []
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    monkeypatch.setenv("DMS_ACTIONS", "1")
    assert _apply(conn, tid, prop["idempotency_token"])["verified"] is True
    conn.rollback()


def test_flag_off_equals_main(
    conn: psycopg.Connection,
    two_tenants: dict[str, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off: no action can be proposed, and a plain amend confirms as on main."""
    monkeypatch.delenv("DMS_ACTIONS", raising=False)
    tid = two_tenants["alpha"]
    set_tenant_context(conn, tid, role="steward")
    spec = validate_spec(
        {"action_type": "inventory.set_reorder_level", "object_key": KEY, "value": "450"}
    )
    with pytest.raises(ActionsDisabled):
        propose_action(
            conn,
            tenant_id=tid,
            space_id=None,
            actor_id=two_tenants["user"],
            spec=spec,
            source_value="400",
        )
    conn.rollback()
    set_tenant_context(conn, tid, role="steward")
    for table in ("action_proposals", "action_writeback", "action_decisions"):
        assert _count(conn, tid, table) == 0, table
    # The amend path (main's only proposals caller) is unchanged.
    pid = uuid.uuid4()
    conn.execute("INSERT INTO dms.proposals (id, tenant_id) VALUES (%s, %s)", (pid, tid))
    ver = create_proposal_version(
        conn, proposal_id=pid, tenant_id=tid, diff={"table": "t", "op": "set", "v": 1}
    )
    conn.commit()
    set_tenant_context(conn, tid, role="steward")
    out = confirm_proposal_version(conn, tenant_id=tid, idempotency_token=ver["idempotency_token"])
    assert out == {
        "id": ver["id"],
        "proposal_id": pid,
        "version_num": 1,
        "status": "applied",
    }
    conn.commit()
    set_tenant_context(conn, tid, role="steward")
    with pytest.raises(ConflictError):
        confirm_proposal_version(conn, tenant_id=tid, idempotency_token=ver["idempotency_token"])
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_propose_then_apply_rows(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    result = _apply(conn, tid, prop["idempotency_token"])
    conn.commit()

    assert _rows(conn, tid) == [("450", 1)]
    assert result["before"]["value"] is None
    assert result["before"]["source_value"] == "400"
    assert result["after"]["value"] == "450"
    assert result["verified"] is True
    assert result["rows_changed"] == 1
    assert result["approval_ref"] == "CAB-2026-10-29"
    assert result["evidence"]["audit_id"] == "ask_0001"

    row = conn.execute(
        """
        SELECT object_type, object_key, field, value, row_version
          FROM dms.action_writeback WHERE tenant_id = %s
        """,
        (tid,),
    ).fetchone()
    assert row is not None
    recomputed = state_hash(
        {
            "object_type": row[0],
            "object_key": row[1],
            "field": row[2],
            "value": row[3],
            "row_version": row[4],
        }
    )
    assert result["after_hash"] == recomputed
    assert result["before_hash"] != result["after_hash"]
    assert _status(conn, tid, prop["idempotency_token"]) == "applied"
    # What a later read sees: the override list.
    overrides = list_overrides(conn, tenant_id=tid, object_type="inventory_item")
    assert [(o["object_key"], o["field"], o["value"], o["row_version"]) for o in overrides] == [
        (KEY, "reorder_level_kg", "450", 1)
    ]
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_summary_is_plain_text(conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]) -> None:
    prop = _propose(conn, two_tenants)
    assert (
        prop["diff"]["summary"]
        == "Set reorder_level_kg for inventory_item SKU-BETA @ WH-B: 400 (source) -> 450"
    )
    diff = prop["diff"]
    assert diff["kind"] == "action"
    assert diff["before"] == {"value": None, "row_version": 0, "source_value": "400"}
    assert diff["value"] == "450"
    # The stored diff is what the proposer saw.
    tid = two_tenants["alpha"]
    set_tenant_context(conn, tid, role="steward")
    stored = conn.execute(
        "SELECT diff FROM dms.proposal_versions WHERE idempotency_token = %s",
        (prop["idempotency_token"],),
    ).fetchone()
    assert stored is not None and stored[0] == diff
    assert is_action_version(conn, tenant_id=tid, idempotency_token=prop["idempotency_token"])
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_value_unchanged_refused(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    from dms_core.control_plane.action_types import ActionSpecError

    with pytest.raises(ActionSpecError) as info:
        _propose(conn, two_tenants, value="400.0", source_value="400")
    assert info.value.code == "value_unchanged"
    conn.rollback()
    set_tenant_context(conn, two_tenants["alpha"], role="steward")
    assert _count(conn, two_tenants["alpha"], "action_proposals") == 0
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_replay_is_conflict(conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    result = _apply(conn, tid, prop["idempotency_token"])
    record_decision(conn, result, decision="applied", cortex_entry_id="led_replay", space_id=None)
    conn.commit()
    with pytest.raises(ConflictError):
        _apply(conn, tid, prop["idempotency_token"])
    conn.rollback()
    assert _rows(conn, tid) == [("450", 1)]
    assert _count(conn, tid, "action_decisions") == 1
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_concurrent_apply_one_wins(
    migrated_db: str, conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    token = prop["idempotency_token"]
    results: list[Any] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        with psycopg.connect(migrated_db) as wconn:
            set_tenant_context(wconn, tid, role="steward")
            barrier.wait()
            try:
                out = apply_action_version(
                    wconn,
                    tenant_id=tid,
                    idempotency_token=token,
                    actor_id="steward-1",
                    approval_ref="CAB-2026-10-29",
                    lock_timeout_seconds=5.0,
                )
                wconn.commit()
                results.append(("ok", out["verified"]))
            except (ConflictError, AdvisoryLockTimeout) as exc:
                wconn.rollback()
                results.append(("refused", type(exc).__name__))
            except Exception as exc:  # noqa: BLE001
                wconn.rollback()
                results.append(("err", type(exc).__name__, str(exc)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(r[0] for r in results) == ["ok", "refused"], results
    assert ("ok", True) in results
    assert _rows(conn, tid) == [("450", 1)]
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_stale_precondition(conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]) -> None:
    tid = two_tenants["alpha"]
    prop_a = _propose(conn, two_tenants, value="450")
    prop_b = _propose(conn, two_tenants, value="500")
    _apply(conn, tid, prop_a["idempotency_token"])
    conn.commit()
    with pytest.raises(PreconditionFailed) as info:
        _apply(conn, tid, prop_b["idempotency_token"])
    assert info.value.code == "object_changed"
    assert info.value.current_value == "450"
    assert info.value.current_row_version == 1
    conn.rollback()
    assert _rows(conn, tid) == [("450", 1)]
    assert _status(conn, tid, prop_b["idempotency_token"]) == "pending_confirm"
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_tamper_refused(conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    set_tenant_context(conn, tid, role="steward")
    conn.execute(
        """
        UPDATE dms.proposal_versions
           SET diff = jsonb_set(diff, '{value}', '"9999"')
         WHERE idempotency_token = %s
        """,
        (prop["idempotency_token"],),
    )
    conn.commit()
    with pytest.raises(TokenDiffMismatch):
        _apply(conn, tid, prop["idempotency_token"])
    conn.rollback()
    assert _rows(conn, tid) == []
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_blank_approval_ref(conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    for ref in ("  ", "", "ab", " a "):
        with pytest.raises(ApprovalRefRequired):
            _apply(conn, tid, prop["idempotency_token"], ref=ref)
        conn.rollback()
    assert _rows(conn, tid) == []
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_amend_confirm_cannot_flip_an_action(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    set_tenant_context(conn, tid, role="steward")
    with pytest.raises(ActionRequiresApply) as info:
        confirm_proposal_version(conn, tenant_id=tid, idempotency_token=prop["idempotency_token"])
    assert isinstance(info.value, ConflictError)
    conn.rollback()
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    assert _rows(conn, tid) == []
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_decline(conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    set_tenant_context(conn, tid, role="steward")
    out = decline_proposal_version(conn, tenant_id=tid, idempotency_token=prop["idempotency_token"])
    assert out["status"] == "rejected"
    conn.commit()
    assert _status(conn, tid, prop["idempotency_token"]) == "rejected"
    with pytest.raises(StaleTokenError):
        _apply(conn, tid, prop["idempotency_token"])
    conn.rollback()
    assert _rows(conn, tid) == []

    applied = _propose(conn, two_tenants)
    _apply(conn, tid, applied["idempotency_token"])
    conn.commit()
    set_tenant_context(conn, tid, role="steward")
    with pytest.raises(ConflictError):
        decline_proposal_version(
            conn, tenant_id=tid, idempotency_token=applied["idempotency_token"]
        )
    conn.rollback()

    # decline_action_version records a declined decision with no write.
    third = _propose(conn, two_tenants, value="475")
    set_tenant_context(conn, tid, role="steward")
    declined = decline_action_version(
        conn,
        tenant_id=tid,
        idempotency_token=third["idempotency_token"],
        approval_ref="CAB-DECLINE-1",
    )
    assert declined["rows_changed"] == 0 and declined["verified"] is False
    rec = record_decision(
        conn, declined, decision="declined", cortex_entry_id="led_decl", space_id=None
    )
    conn.commit()
    got = get_decision(conn, tenant_id=tid, decision_id=rec["decision_id"])
    assert got is not None
    assert (got["decision"], got["rows_changed"], got["after_hash"]) == ("declined", 0, None)
    assert _status(conn, tid, third["idempotency_token"]) == "rejected"
    assert _rows(conn, tid) == [("450", 1)]
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_rollback_leaves_nothing(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    result = _apply(conn, tid, prop["idempotency_token"])
    record_decision(conn, result, decision="applied", cortex_entry_id="led_lost", space_id=None)
    conn.rollback()  # the ledger append failed
    set_tenant_context(conn, tid, role="steward")
    assert _count(conn, tid, "action_writeback") == 0
    assert _count(conn, tid, "action_decisions") == 0
    assert _count(conn, tid, "ledger_ref") == 0
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    conn.rollback()
    again = _apply(conn, tid, prop["idempotency_token"])
    assert again["verified"] is True
    conn.commit()
    assert _rows(conn, tid) == [("450", 1)]
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_record_decision_links_ledger(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    result = _apply(conn, tid, prop["idempotency_token"])
    rec = record_decision(conn, result, decision="applied", cortex_entry_id="led_7", space_id=None)
    conn.commit()
    set_tenant_context(conn, tid, role="steward")
    rows = conn.execute(
        """
        SELECT d.decision, d.after->>'value', l.cortex_entry_id
          FROM dms.action_decisions d
          JOIN dms.ledger_ref l ON l.seq = d.ledger_seq
         WHERE d.tenant_id = %s
        """,
        (tid,),
    ).fetchall()
    assert rows == [("applied", "450", "led_7")]
    got = get_decision(conn, tenant_id=tid, decision_id=rec["decision_id"])
    assert got is not None and got["ledger_seq"] == rec["ledger_seq"]
    assert got["approval_ref"] == "CAB-2026-10-29"

    # An applied decision that did not verify cannot be stored.
    second = _propose(conn, two_tenants, value="500")
    set_tenant_context(conn, tid, role="steward")
    ver = conn.execute(
        "SELECT id, proposal_id FROM dms.proposal_versions WHERE idempotency_token = %s",
        (second["idempotency_token"],),
    ).fetchone()
    assert ver is not None
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """
            INSERT INTO dms.action_decisions (
              id, tenant_id, proposal_id, proposal_version_id, decision, action_type,
              object_type, object_key, field, op, before, after, before_hash, after_hash,
              rows_changed, verified, approval_ref, cortex_entry_id, ledger_seq
            ) VALUES (
              %s, %s, %s, %s, 'applied', 'inventory.set_reorder_level',
              'inventory_item', %s::jsonb, 'reorder_level_kg', 'set', '{}'::jsonb,
              '{}'::jsonb, 'x', 'y', 1, false, 'CAB-1', 'led_7', %s
            )
            """,
            (uuid.uuid4(), tid, ver[1], ver[0], json.dumps(KEY), rec["ledger_seq"]),
        )
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_append_only_and_rls(conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]) -> None:
    alpha, beta = two_tenants["alpha"], two_tenants["beta"]
    prop = _propose(conn, two_tenants)
    result = _apply(conn, alpha, prop["idempotency_token"])
    record_decision(conn, result, decision="applied", cortex_entry_id="led_rls", space_id=None)
    conn.commit()

    for stmt in (
        "UPDATE dms.action_decisions SET verified = true",
        "DELETE FROM dms.action_decisions",
        "DELETE FROM dms.action_writeback",
        "UPDATE dms.action_proposals SET spec_sha = 'x'",
    ):
        set_tenant_context(conn, alpha, role="steward")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(stmt)
        conn.rollback()

    tables = ("action_proposals", "action_writeback", "action_decisions")
    set_tenant_context(conn, alpha, role="viewer")
    assert [_count(conn, alpha, t) for t in tables] == [1, 1, 1]
    conn.rollback()
    for role in ("viewer", "steward", "admin"):
        set_tenant_context(conn, beta, role=role)  # type: ignore[arg-type]
        seen = [
            int(conn.execute(f"SELECT count(*) FROM dms.{t}").fetchone()[0])  # type: ignore[index]
            for t in tables
        ]
        assert seen == [0, 0, 0], (role, seen)
        assert list_overrides(conn, tenant_id=alpha, object_type="inventory_item") == []
        conn.rollback()
    # Beta cannot apply alpha's token either.
    set_tenant_context(conn, beta, role="steward")
    with pytest.raises(StaleTokenError):
        apply_action_version(
            conn,
            tenant_id=beta,
            idempotency_token=prop["idempotency_token"],
            actor_id="steward-b",
            approval_ref="CAB-B-1",
        )
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_clear_restores_source(conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    _apply(conn, tid, prop["idempotency_token"])
    conn.commit()
    clear = _propose(conn, two_tenants, op="clear", value=None)
    assert clear["diff"]["summary"] == (
        "Clear reorder_level_kg for inventory_item SKU-BETA @ WH-B: "
        "450 (override) -> 400 (source)"
    )
    out = _apply(conn, tid, clear["idempotency_token"])
    conn.commit()
    assert out["after"]["value"] is None and out["before"]["value"] == "450"
    assert _rows(conn, tid) == [(None, 2)]
    assert list_overrides(conn, tenant_id=tid, object_type="inventory_item") == []
    conn.rollback()


def test_verify_failed_is_an_error_type() -> None:
    assert issubclass(VerifyFailed, Exception)
