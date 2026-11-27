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
from dms_core.control_plane.action_types import ActionSpecError, state_hash, validate_spec
from dms_core.control_plane.actions import (
    ActionsDisabled,
    ActorRequired,
    ApprovalRefInvalid,
    ApprovalRefRequired,
    LedgerEntryReused,
    PreconditionFailed,
    ReceiptMismatch,
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
    ActionVersionFixed,
    ConflictError,
    StaleTokenError,
    _token_for,
    confirm_proposal_version,
    create_proposal_version,
    decline_proposal_version,
)
from dms_core.control_plane.seed import USER_ID as SEED_USER_ID
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
        actor_id="steward-1",
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
              rows_changed, verified, approval_ref, actor_id, cortex_entry_id, ledger_seq
            ) VALUES (
              %s, %s, %s, %s, 'applied', 'inventory.set_reorder_level',
              'inventory_item', %s::jsonb, 'reorder_level_kg', 'set', '{}'::jsonb,
              '{}'::jsonb, 'x', 'y', 1, false, 'CAB-1', 'steward-1', 'led_7', %s
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
        "Clear reorder_level_kg for inventory_item SKU-BETA @ WH-B: 450 (override) -> 400 (source)"
    )
    out = _apply(conn, tid, clear["idempotency_token"])
    conn.commit()
    assert out["after"]["value"] is None and out["before"]["value"] == "450"
    assert _rows(conn, tid) == [(None, 2)]
    assert list_overrides(conn, tenant_id=tid, object_type="inventory_item") == []
    conn.rollback()


def _cell(value: str | None, row_version: int) -> dict[str, Any]:
    return {
        "object_type": "inventory_item",
        "object_key": dict(KEY),
        "field": "reorder_level_kg",
        "value": value,
        "row_version": row_version,
    }


def _decisions(conn: psycopg.Connection, tid: uuid.UUID) -> list[tuple[Any, ...]]:
    set_tenant_context(conn, tid, role="viewer")
    rows = conn.execute(
        """
        SELECT decision, after->>'value', after_hash, verified, rows_changed, approval_ref,
               actor_id, cortex_entry_id
          FROM dms.action_decisions WHERE tenant_id = %s ORDER BY decided_at
        """,
        (tid,),
    ).fetchall()
    conn.rollback()
    return rows


@pytest.mark.usefixtures("actions_on")
def test_forged_applied_receipt_refused(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    """A caller record for a version that was never applied is not stored.

    Before the fix, record_decision stored decision='applied', verified, value
    9999 for a pending version, and the real receipt could then never be written.
    """
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    set_tenant_context(conn, tid, role="steward")
    ver = conn.execute(
        "SELECT id, proposal_id FROM dms.proposal_versions WHERE idempotency_token = %s",
        (prop["idempotency_token"],),
    ).fetchone()
    assert ver is not None
    forged = {
        "proposal_id": ver[1],
        "version_id": ver[0],
        "version_num": 1,
        "action_type": "inventory.set_reorder_level",
        "object_type": "inventory_item",
        "object_key": dict(KEY),
        "field": "reorder_level_kg",
        "op": "set",
        "before": {"value": None, "row_version": 0, "source_value": "400"},
        "after": {"value": "9999", "row_version": 1, "source_value": "400"},
        "before_hash": prop["diff"]["expected_before_hash"],
        "after_hash": "deadbeef",
        "verified": True,
        "rows_changed": 1,
        "approval_ref": "CAB-FORGED",
        "evidence": {"audit_id": "ask_0001"},
        "actor_id": "steward-1",
    }
    with pytest.raises(ReceiptMismatch) as info:
        record_decision(conn, forged, decision="applied", cortex_entry_id="led_forged")
    assert info.value.field == "decision"
    assert str(info.value) == "receipt_mismatch: decision (version is pending_confirm, not applied)"
    conn.rollback()
    assert _decisions(conn, tid) == []
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    conn.rollback()

    # The real apply's receipt is still writable, and it is the re-read row.
    result = _apply(conn, tid, prop["idempotency_token"])
    # A record that claims another value for a real apply is refused too.
    with pytest.raises(ReceiptMismatch) as info:
        record_decision(
            conn,
            {**result, "after": {**result["after"], "value": "9999"}},
            decision="applied",
            cortex_entry_id="led_forged_2",
        )
    assert info.value.field == "after"
    # The refusal rolled the apply back with it: nothing to commit by mistake.
    assert _rows(conn, tid) == []
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    result = _apply(conn, tid, prop["idempotency_token"])
    rec = record_decision(conn, result, decision="applied", cortex_entry_id="led_real")
    conn.commit()
    assert _decisions(conn, tid) == [
        (
            "applied",
            "450",
            state_hash(_cell("450", 1)),
            True,
            1,
            "CAB-2026-10-29",
            "steward-1",
            "led_real",
        )
    ]
    got = get_decision(conn, tenant_id=tid, decision_id=rec["decision_id"])
    assert got is not None and got["after"] == {
        "value": "450",
        "row_version": 1,
        "source_value": "400",
    }
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_applied_write_cannot_be_recorded_as_declined(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    result = _apply(conn, tid, prop["idempotency_token"])
    as_declined = {**result, "after": None, "after_hash": None, "verified": False}
    as_declined["rows_changed"] = 0
    with pytest.raises(ReceiptMismatch) as info:
        record_decision(conn, as_declined, decision="declined", cortex_entry_id="led_lie")
    assert info.value.field == "decision"
    assert "version is applied, not rejected" in str(info.value)
    conn.commit()  # a caller that commits after the refusal keeps no write
    assert _rows(conn, tid) == []
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    conn.rollback()
    assert _decisions(conn, tid) == []


@pytest.mark.usefixtures("actions_on")
def test_stale_action_cannot_be_rearmed_with_a_new_version(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    """Proposal B (made against an absent row) cannot be re-pointed at the applied 450."""
    tid = two_tenants["alpha"]
    prop_a = _propose(conn, two_tenants, value="450")
    prop_b = _propose(conn, two_tenants, value="500")
    _apply(conn, tid, prop_a["idempotency_token"])
    conn.commit()

    rearmed = dict(prop_b["diff"])
    rearmed["before"] = {"value": "450", "row_version": 1, "source_value": "400"}
    rearmed["expected_before_hash"] = state_hash(_cell("450", 1))
    rearmed["expected_after_hash"] = state_hash(_cell("500", 2))
    rearmed["evidence"] = {"audit_id": "ask_FORGED"}
    rearmed["summary"] = "Set reorder_level_kg for inventory_item SKU-BETA @ WH-B: -> 5"

    # Through the public core function: an action proposal takes no second version.
    set_tenant_context(conn, tid, role="steward")
    with pytest.raises(ActionVersionFixed) as info:
        create_proposal_version(
            conn, proposal_id=prop_b["proposal_id"], tenant_id=tid, diff=rearmed
        )
    assert str(info.value) == "an action proposal has one version; propose a new action"
    conn.rollback()

    # Written straight into the row with a recomputed token: the pinned digest refuses.
    set_tenant_context(conn, tid, role="steward")
    token = _token_for(prop_b["proposal_id"], 1, rearmed)
    conn.execute(
        """
        UPDATE dms.proposal_versions SET diff = %s::jsonb, idempotency_token = %s
         WHERE idempotency_token = %s
        """,
        (json.dumps(rearmed), token, prop_b["idempotency_token"]),
    )
    conn.commit()
    with pytest.raises(TokenDiffMismatch) as tdm:
        _apply(conn, tid, token)
    assert str(tdm.value) == "diff does not match the digest pinned at propose"
    conn.rollback()
    assert _rows(conn, tid) == [("450", 1)]
    assert _status(conn, tid, token) == "pending_confirm"
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_decision_space_is_the_proposals(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    set_tenant_context(conn, tid, role="admin")
    space_a = conn.execute(
        "SELECT id FROM dms.spaces WHERE tenant_id = %s ORDER BY name LIMIT 1", (tid,)
    ).fetchone()
    assert space_a is not None
    space_b = uuid.uuid4()
    conn.execute(
        "INSERT INTO dms.spaces (id, tenant_id, name, created_by) VALUES (%s, %s, %s, %s)",
        (space_b, tid, f"space-b2-{space_b.hex[:8]}", two_tenants["user"]),
    )
    spec = validate_spec(
        {"action_type": "inventory.set_reorder_level", "object_key": dict(KEY), "value": "450"}
    )
    set_tenant_context(conn, tid, role="steward")
    prop = propose_action(
        conn,
        tenant_id=tid,
        space_id=space_a[0],
        actor_id=two_tenants["user"],
        spec=spec,
        source_value="400",
    )
    conn.commit()
    result = _apply(conn, tid, prop["idempotency_token"])
    with pytest.raises(ReceiptMismatch) as info:
        record_decision(
            conn, result, decision="applied", cortex_entry_id="led_sp", space_id=space_b
        )
    assert info.value.field == "space_id"
    result = _apply(conn, tid, prop["idempotency_token"])
    rec = record_decision(conn, result, decision="applied", cortex_entry_id="led_sp")
    conn.commit()
    got = get_decision(conn, tenant_id=tid, decision_id=rec["decision_id"])
    assert got is not None and got["space_id"] == space_a[0]
    set_tenant_context(conn, tid, role="viewer")
    in_b = conn.execute(
        "SELECT count(*) FROM dms.action_decisions WHERE space_id = %s", (space_b,)
    ).fetchone()
    assert in_b is not None and in_b[0] == 0
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_receipt_holds_no_pii_or_secret(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    """Evidence, approval_ref and reason are scrubbed and masked before the append-only row."""
    tid = two_tenants["alpha"]
    set_tenant_context(conn, tid, role="steward")
    spec = validate_spec(
        {
            "action_type": "inventory.set_reorder_level",
            "object_key": dict(KEY),
            "value": "450",
            "reason": "per 900101-14-5678",
        }
    )
    prop = propose_action(
        conn,
        tenant_id=tid,
        space_id=None,
        actor_id=two_tenants["user"],
        spec=spec,
        source_value="400",
        evidence={
            "audit_id": "ask_0001",
            "question": "reorder for 900101-14-5678, mail jian@example.com, password=hunter2xyz",
        },
    )
    conn.commit()
    # An approval_ref is a ticket identifier kept verbatim, so one carrying an
    # email is refused, not masked (ACT-02 MEDIUM 2), and the version stays pending.
    with pytest.raises(ApprovalRefInvalid) as refused:
        _apply(conn, tid, prop["idempotency_token"], ref="CAB jian@example.com")
    assert refused.value.code == "approval_ref_invalid"
    conn.rollback()
    result = _apply(conn, tid, prop["idempotency_token"], ref="CHG-2026-1030-4417")
    assert result["approval_ref"] == "CHG-2026-1030-4417"
    assert result["evidence"]["question"] == (
        "reorder for DMSMASK_nric_01, mail DMSMASK_email_01, password=[redacted]"
    )
    rec = record_decision(conn, result, decision="applied", cortex_entry_id="led_pii")
    conn.commit()
    set_tenant_context(conn, tid, role="viewer")
    stored = conn.execute(
        """
        SELECT d.approval_ref, d.evidence->>'question', v.diff->>'reason', d.evidence::text,
               v.diff::text
          FROM dms.action_decisions d
          JOIN dms.proposal_versions v ON v.id = d.proposal_version_id
         WHERE d.id = %s
        """,
        (rec["decision_id"],),
    ).fetchone()
    conn.rollback()
    assert stored is not None
    assert stored[:3] == (
        "CHG-2026-1030-4417",
        "reorder for DMSMASK_nric_01, mail DMSMASK_email_01, password=[redacted]",
        "per DMSMASK_nric_01",
    )
    for text in stored:
        for leaked in ("900101-14-5678", "jian@example.com", "hunter2xyz"):
            assert leaked not in text


@pytest.mark.usefixtures("actions_on")
def test_actor_required_and_recorded(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    for actor in (None, "", "  ", "None", str(SEED_USER_ID), SEED_USER_ID, "​"):
        set_tenant_context(conn, tid, role="steward")
        with pytest.raises(ActorRequired) as info:
            apply_action_version(
                conn,
                tenant_id=tid,
                idempotency_token=prop["idempotency_token"],
                actor_id=actor,  # type: ignore[arg-type]
                approval_ref="CAB-2026-10-29",
            )
        assert info.value.code == "actor_required"
        conn.rollback()
        set_tenant_context(conn, tid, role="steward")
        with pytest.raises(ActorRequired):
            decline_action_version(
                conn,
                tenant_id=tid,
                idempotency_token=prop["idempotency_token"],
                approval_ref="CAB-2026-10-29",
                actor_id=actor,  # type: ignore[arg-type]
            )
        conn.rollback()
    assert _rows(conn, tid) == []
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    conn.rollback()
    result = _apply(conn, tid, prop["idempotency_token"])
    assert result["actor_id"] == "steward-1"
    with pytest.raises(ActorRequired):
        record_decision(
            conn, {**result, "actor_id": None}, decision="applied", cortex_entry_id="led_a0"
        )
    result = _apply(conn, tid, prop["idempotency_token"])
    record_decision(conn, result, decision="applied", cortex_entry_id="led_actor")
    conn.commit()
    assert [r[6] for r in _decisions(conn, tid)] == ["steward-1"]


@pytest.mark.usefixtures("actions_on")
def test_one_ledger_entry_backs_one_decision(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    first = _propose(conn, two_tenants)
    record_decision(
        conn,
        _apply(conn, tid, first["idempotency_token"]),
        decision="applied",
        cortex_entry_id="led_once",
    )
    conn.commit()
    second = _propose(conn, two_tenants, op="clear", value=None)
    result = _apply(conn, tid, second["idempotency_token"])
    with pytest.raises(LedgerEntryReused) as info:
        record_decision(conn, result, decision="applied", cortex_entry_id="led_once")
    assert str(info.value) == "cortex entry 'led_once' already backs a pointer"
    conn.rollback()
    assert [(r[0], r[7]) for r in _decisions(conn, tid)] == [("applied", "led_once")]
    assert _rows(conn, tid) == [("450", 1)]
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_decline_checks_the_ref_before_it_flips(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    for ref in ("", "  ", "​​​", "a​​b"):
        set_tenant_context(conn, tid, role="steward")
        with pytest.raises(ApprovalRefRequired) as info:
            decline_action_version(
                conn,
                tenant_id=tid,
                idempotency_token=prop["idempotency_token"],
                approval_ref=ref,
                actor_id="steward-1",
            )
        assert str(info.value) == "approval_ref must have at least 3 visible characters"
        conn.commit()  # a caller that commits after catching it changes nothing
        assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
        conn.rollback()
    assert _decisions(conn, tid) == []


@pytest.mark.usefixtures("actions_on")
def test_invisible_approval_ref_refused_in_code_and_table(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    for ref in ("​​​", "﻿⁠‍", " 　x"):
        with pytest.raises(ApprovalRefRequired):
            _apply(conn, tid, prop["idempotency_token"], ref=ref)
        conn.rollback()
    assert _rows(conn, tid) == []
    # The table refuses one written past the code, too.
    result = _apply(conn, tid, prop["idempotency_token"])
    rec = record_decision(conn, result, decision="applied", cortex_entry_id="led_inv")
    conn.commit()
    set_tenant_context(conn, tid, role="steward")
    got = get_decision(conn, tenant_id=tid, decision_id=rec["decision_id"])
    assert got is not None
    seq = conn.execute(
        "INSERT INTO dms.ledger_ref (tenant_id, cortex_entry_id) VALUES (%s, 'led_inv2') "
        "RETURNING seq",
        (tid,),
    ).fetchone()
    assert seq is not None
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """
            INSERT INTO dms.action_decisions (
              id, tenant_id, proposal_id, proposal_version_id, decision, action_type,
              object_type, object_key, field, op, rows_changed, verified, approval_ref,
              actor_id, cortex_entry_id, ledger_seq
            ) VALUES (
              %s, %s, %s, %s, 'declined', 'inventory.set_reorder_level', 'inventory_item',
              %s::jsonb, 'reorder_level_kg', 'set', 0, false, %s, 'steward-1', 'led_inv2', %s
            )
            """,
            (
                uuid.uuid4(),
                tid,
                got["proposal_id"],
                uuid.uuid4(),
                json.dumps(KEY),
                "​​​",
                seq[0],
            ),
        )
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_verify_failed_rolls_back(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    """A re-read that does not show the written value refuses, and nothing is kept."""
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    conn.execute(
        """
        CREATE FUNCTION dms.act01_test_corrupt() RETURNS trigger
          LANGUAGE plpgsql AS $$ BEGIN NEW.value := '1'; RETURN NEW; END $$;
        CREATE TRIGGER act01_test_corrupt BEFORE INSERT OR UPDATE ON dms.action_writeback
          FOR EACH ROW EXECUTE FUNCTION dms.act01_test_corrupt();
        """
    )
    conn.commit()
    try:
        with pytest.raises(VerifyFailed) as info:
            _apply(conn, tid, prop["idempotency_token"])
        assert str(info.value) == "re-read shows '1' at row_version 1, wrote '450'"
        assert info.value.code == "verify_failed"
        conn.rollback()
        assert _rows(conn, tid) == []
        assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
        conn.rollback()
    finally:
        conn.rollback()
        conn.execute("DROP TRIGGER IF EXISTS act01_test_corrupt ON dms.action_writeback")
        conn.execute("DROP FUNCTION IF EXISTS dms.act01_test_corrupt()")
        conn.commit()
    assert _apply(conn, tid, prop["idempotency_token"])["after"]["value"] == "450"
    conn.rollback()


def _decision_rows(conn: psycopg.Connection, tid: uuid.UUID) -> list[tuple[Any, ...]]:
    set_tenant_context(conn, tid, role="viewer")
    rows = conn.execute(
        """
        SELECT d.decision, d.approval_ref, d.actor_id, d.evidence, a.evidence_audit_id
          FROM dms.action_decisions d
          JOIN dms.action_proposals a ON a.proposal_id = d.proposal_id
         WHERE d.tenant_id = %s ORDER BY d.decided_at
        """,
        (tid,),
    ).fetchall()
    conn.rollback()
    return rows


@pytest.mark.usefixtures("actions_on")
def test_actor_and_ref_are_bound_at_apply(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    """The receipt names who applied it, not whoever calls record_decision.

    Before the fix, an apply by steward-1 under CAB-REAL-1 was stored as approved
    by someone-else under CAB-FORGED-9.
    """
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)
    for field, forged in (("approval_ref", "CAB-FORGED-9"), ("actor_id", "someone-else")):
        result = _apply(conn, tid, prop["idempotency_token"], ref="CAB-REAL-1")
        with pytest.raises(ReceiptMismatch) as info:
            record_decision(
                conn, {**result, field: forged}, decision="applied", cortex_entry_id="led_n2"
            )
        assert info.value.field == field
        assert info.value.code == "receipt_mismatch"
        conn.commit()  # committing after the refusal keeps nothing
        assert _rows(conn, tid) == []
        assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
        assert _count(conn, tid, "ledger_ref") == 0
        conn.rollback()
    assert _decision_rows(conn, tid) == []

    result = _apply(conn, tid, prop["idempotency_token"], ref="CAB-REAL-1")
    record_decision(conn, result, decision="applied", cortex_entry_id="led_n2")
    conn.commit()
    assert [r[:3] for r in _decision_rows(conn, tid)] == [("applied", "CAB-REAL-1", "steward-1")]
    set_tenant_context(conn, tid, role="viewer")
    bound = conn.execute(
        "SELECT decision, approval_ref, actor_id FROM dms.action_confirmations"
    ).fetchall()
    assert bound == [("applied", "CAB-REAL-1", "steward-1")]
    conn.rollback()
    # The binding is append-only for the app roles, like the decision row.
    for stmt in (
        "UPDATE dms.action_confirmations SET actor_id = 'someone-else'",
        "DELETE FROM dms.action_confirmations",
    ):
        set_tenant_context(conn, tid, role="steward")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(stmt)
        conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_evidence_not_pinned_at_propose_is_refused(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    """No evidence key may appear at record time, the cited answer least of all.

    Before the fix, a proposal with no evidence was stored with
    {"audit_id": "ask_FORGED", "approved_by": "the board"} on its receipt.
    """
    tid = two_tenants["alpha"]
    set_tenant_context(conn, tid, role="steward")
    spec = validate_spec(
        {"action_type": "inventory.set_reorder_level", "object_key": dict(KEY), "value": "450"}
    )
    prop = propose_action(
        conn,
        tenant_id=tid,
        space_id=None,
        actor_id=two_tenants["user"],
        spec=spec,
        source_value="400",
        evidence={},
    )
    conn.commit()
    for forged, key in (
        ({"audit_id": "ask_FORGED", "approved_by": "the board"}, "approved_by"),
        ({"audit_id": "ask_FORGED"}, "audit_id"),
        ({"decline_reason": "n/a"}, "decline_reason"),
    ):
        result = _apply(conn, tid, prop["idempotency_token"])
        with pytest.raises(ReceiptMismatch) as info:
            record_decision(
                conn, {**result, "evidence": forged}, decision="applied", cortex_entry_id="led_n1"
            )
        assert info.value.field == "evidence"
        assert str(info.value) == (
            f"receipt_mismatch: evidence (evidence {key!r} was not pinned at propose)"
        )
        conn.commit()
        assert _rows(conn, tid) == []
        conn.rollback()
    assert _decision_rows(conn, tid) == []

    result = _apply(conn, tid, prop["idempotency_token"])
    record_decision(conn, result, decision="applied", cortex_entry_id="led_n1")
    conn.commit()
    assert _decision_rows(conn, tid) == [("applied", "CAB-2026-10-29", "steward-1", {}, None)]

    # A pinned citation cannot be swapped or dropped either.
    pinned = _propose(conn, two_tenants, op="clear", value=None)
    for forged in ({"audit_id": "ask_FORGED", "question": "reorder levels below cover"}, {}):
        result = _apply(conn, tid, pinned["idempotency_token"])
        with pytest.raises(ReceiptMismatch) as info:
            record_decision(
                conn, {**result, "evidence": forged}, decision="applied", cortex_entry_id="led_n1b"
            )
        assert info.value.field == "evidence"
        assert "differs from the proposal" in str(info.value)
        conn.rollback()
    # A proposal cannot pin the key a decline adds.
    set_tenant_context(conn, tid, role="steward")
    with pytest.raises(ActionSpecError) as spec_info:
        propose_action(
            conn,
            tenant_id=tid,
            space_id=None,
            actor_id=two_tenants["user"],
            spec=validate_spec(
                {
                    "action_type": "inventory.set_reorder_level",
                    "object_key": dict(KEY),
                    "value": "475",
                }
            ),
            source_value="400",
            evidence={"decline_reason": "pre-filled"},
        )
    assert spec_info.value.code == "field_unknown"
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_decline_reason_is_bound_at_decline(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    prop = _propose(conn, two_tenants)

    def _decline() -> dict[str, Any]:
        set_tenant_context(conn, tid, role="steward")
        return dict(
            decline_action_version(
                conn,
                tenant_id=tid,
                idempotency_token=prop["idempotency_token"],
                approval_ref="CAB-DECLINE-2",
                actor_id="steward-1",
                reason="supplier says no, mail jian@example.com",
            )
        )

    declined = _decline()
    assert declined["evidence"] == {
        "audit_id": "ask_0001",
        "question": "reorder levels below cover",
        "decline_reason": "supplier says no, mail DMSMASK_email_01",
    }
    forged = {**declined["evidence"], "decline_reason": "approved by the board"}
    with pytest.raises(ReceiptMismatch) as info:
        record_decision(
            conn, {**declined, "evidence": forged}, decision="declined", cortex_entry_id="led_d1"
        )
    assert info.value.field == "evidence"
    conn.commit()
    assert _status(conn, tid, prop["idempotency_token"]) == "pending_confirm"
    conn.rollback()

    declined = _decline()
    record_decision(conn, declined, decision="declined", cortex_entry_id="led_d1")
    conn.commit()
    rows = _decision_rows(conn, tid)
    assert [(r[0], r[1], r[2], r[3]["decline_reason"]) for r in rows] == [
        ("declined", "CAB-DECLINE-2", "steward-1", "supplier says no, mail DMSMASK_email_01")
    ]
    assert _status(conn, tid, prop["idempotency_token"]) == "rejected"
    conn.rollback()

    # Too long is refused before the status flips.
    other = _propose(conn, two_tenants, value="480")
    set_tenant_context(conn, tid, role="steward")
    with pytest.raises(ActionSpecError) as too_long:
        decline_action_version(
            conn,
            tenant_id=tid,
            idempotency_token=other["idempotency_token"],
            approval_ref="CAB-DECLINE-3",
            actor_id="steward-1",
            reason="x" * 501,
        )
    assert too_long.value.code == "reason_too_long"
    conn.commit()
    assert _status(conn, tid, other["idempotency_token"]) == "pending_confirm"
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_refused_record_leaves_no_write_for_a_committing_caller(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    """Before the fix, a caller that caught LedgerEntryReused and committed kept the clear."""
    tid = two_tenants["alpha"]
    first = _propose(conn, two_tenants)
    record_decision(
        conn,
        _apply(conn, tid, first["idempotency_token"]),
        decision="applied",
        cortex_entry_id="led_once2",
    )
    conn.commit()
    second = _propose(conn, two_tenants, op="clear", value=None)
    result = _apply(conn, tid, second["idempotency_token"])
    assert _rows(conn, tid) == [(None, 2)]
    with pytest.raises(LedgerEntryReused):
        record_decision(conn, result, decision="applied", cortex_entry_id="led_once2")
    conn.commit()
    assert _rows(conn, tid) == [("450", 1)]
    assert _status(conn, tid, second["idempotency_token"]) == "pending_confirm"
    assert _count(conn, tid, "action_confirmations") == 1
    conn.rollback()
    # A forged version id cannot leave the real apply behind either.
    result = _apply(conn, tid, second["idempotency_token"])
    with pytest.raises(ReceiptMismatch) as info:
        record_decision(
            conn, {**result, "version_id": "nope"}, decision="applied", cortex_entry_id="led_x"
        )
    assert info.value.field == "version_id"
    conn.commit()
    assert _rows(conn, tid) == [("450", 1)]
    conn.rollback()
    # Earlier work in the caller's transaction is untouched by the rollback.
    set_tenant_context(conn, tid, role="steward")
    conn.execute(
        "INSERT INTO dms.ledger_ref (tenant_id, cortex_entry_id) VALUES (%s, 'led_caller')",
        (tid,),
    )
    result = _apply(conn, tid, second["idempotency_token"])
    with pytest.raises(ActorRequired):
        record_decision(
            conn, {**result, "actor_id": ""}, decision="applied", cortex_entry_id="led_y"
        )
    conn.commit()
    set_tenant_context(conn, tid, role="steward")
    kept = conn.execute(
        "SELECT cortex_entry_id FROM dms.ledger_ref WHERE tenant_id = %s ORDER BY seq", (tid,)
    ).fetchall()
    assert kept == [("led_once2",), ("led_caller",)]
    assert _rows(conn, tid) == [("450", 1)]
    conn.rollback()


@pytest.mark.usefixtures("actions_on")
def test_integers_keys_and_actor_never_reach_the_receipt_as_pii(
    conn: psycopg.Connection, two_tenants: dict[str, uuid.UUID]
) -> None:
    tid = two_tenants["alpha"]
    set_tenant_context(conn, tid, role="steward")
    spec = validate_spec(
        {"action_type": "inventory.set_reorder_level", "object_key": dict(KEY), "value": "450"}
    )
    prop = propose_action(
        conn,
        tenant_id=tid,
        space_id=None,
        actor_id=two_tenants["user"],
        spec=spec,
        source_value="400",
        evidence={"audit_id": "ask_0001", "nric_int": 900101145678, "rows": 2},
    )
    conn.commit()
    assert prop["diff"]["evidence"] == {
        "audit_id": "ask_0001",
        "nric_int": "DMSMASK_nric_01",
        "rows": 2,
    }
    set_tenant_context(conn, tid, role="steward")
    with pytest.raises(ActorRequired) as info:
        apply_action_version(
            conn,
            tenant_id=tid,
            idempotency_token=prop["idempotency_token"],
            actor_id="ali.steward@example.com",
            approval_ref="CAB-2026-10-29",
        )
    assert str(info.value) == "the actor reads as personal data; configure the steward's user id"
    conn.rollback()
    # A user id is kept as given, even one the masker would mangle as an account number.
    actor = "ce4c9504-8966-4851-8653-567a9ade70f6"
    set_tenant_context(conn, tid, role="steward")
    result = apply_action_version(
        conn,
        tenant_id=tid,
        idempotency_token=prop["idempotency_token"],
        actor_id=actor,
        approval_ref="CAB-2026-10-29",
    )
    record_decision(conn, result, decision="applied", cortex_entry_id="led_pii2")
    conn.commit()
    rows = _decision_rows(conn, tid)
    assert [(r[2], r[3]) for r in rows] == [
        (actor, {"audit_id": "ask_0001", "nric_int": "DMSMASK_nric_01", "rows": 2})
    ]
    for text in map(str, rows[0]):
        assert "900101145678" not in text
