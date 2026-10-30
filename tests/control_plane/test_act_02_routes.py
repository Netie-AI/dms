"""ACT-02. No skip, no xfail. CI runs this file inside pytest tests/.

The governed action routes end to end over HTTP, against real Postgres:
``TestClient(create_app())`` with ``DMS_ACTIONS=1``, an allow-gate recorder in
place of the Cortex F5 call, a fake Cortex ledger, and a non-default steward
actor. Rule 10/10a: every test asserts what the caller receives (status, body,
receipt) AND the rows that exist afterwards, not an intermediate artifact.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.models import LedgerAppendResponse, LedgerVerifyResponse
from dms_core.control_plane.action_types import state_hash
from dms_core.control_plane.advisory_lock import _lock_keys
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("migrated_db")

KEY = {"sku": "SKU-BETA", "location_id": "WH-B"}
RESOURCE = "inventory_item:SKU-BETA@WH-B"
A = "/v1/amend/actions"
QUESTION = "Which SKUs are below reorder level?"


class Recorder:
    """Stands in for ``compliance_gate``. Refuses the tasks named in ``refuse``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.refuse: dict[str, str] = {}

    def __call__(
        self,
        *,
        action: str,
        actor: str | None = None,
        resource: str | None = None,
        metadata: dict[str, Any] | None = None,
        client: Any = None,
    ) -> ComplianceDecision:
        self.calls.append(
            {"action": action, "actor": actor, "resource": resource, "metadata": metadata}
        )
        reason = self.refuse.get(action)
        if reason is None:
            return ComplianceDecision(allowed=True, reason="ok", action=action)
        return ComplianceDecision(allowed=False, reason=reason, action=action)


class FakeCortex:
    """In-process ledger. ``events`` holds the appends that succeeded."""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.append_attempts = 0
        self.fail_appends = 0
        self.chain_ok = True
        self.verify_calls = 0

    def ledger_append(self, req: Any) -> LedgerAppendResponse:
        self.append_attempts += 1
        if self.fail_appends:
            self.fail_appends -= 1
            raise RuntimeError("ledger down")
        self.events.append(req)
        n = len(self.events)
        return LedgerAppendResponse(entry_id=f"e{n}", hash=f"h{n}", seq=6 + n)

    def verify_ledger(self) -> LedgerVerifyResponse:
        self.verify_calls += 1
        return LedgerVerifyResponse(ok=self.chain_ok, first_break=None if self.chain_ok else "3")


@dataclass
class Harness:
    client: TestClient
    gate: Recorder
    cortex: FakeCortex
    conn: psycopg.Connection
    tenant: uuid.UUID
    user: uuid.UUID
    space: str
    monkeypatch: pytest.MonkeyPatch
    app: Any = None

    # --- calls ---
    def propose(self, value: Any = "450", **over: Any) -> Any:
        body = {
            "space_id": self.space,
            "action_type": "inventory.set_reorder_level",
            "object_key": dict(KEY),
            "value": value,
            **over,
        }
        return self.client.post(f"{A}/proposals", json=body)

    def apply(self, prop: dict[str, Any], ref: str = "CAB-1", **over: Any) -> Any:
        body = {"idempotency_token": prop["idempotency_token"], "approval_ref": ref, **over}
        return self.client.post(f"{A}/proposals/{prop['proposal_id']}/apply", json=body)

    def objects(self) -> list[dict[str, Any]]:
        resp = self.client.get(
            f"{A}/objects", params={"object_type": "inventory_item", "space_id": self.space}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    # --- rows (read as the session user, so RLS does not hide anything) ---
    def count(self, table: str) -> int:
        self.conn.rollback()
        row = self.conn.execute(
            f"SELECT count(*) FROM dms.{table} WHERE tenant_id = %s", (self.tenant,)
        ).fetchone()
        assert row is not None
        return int(row[0])

    def counts(self) -> tuple[int, int, int, int]:
        return (
            self.count("proposals"),
            self.count("action_writeback"),
            self.count("action_decisions"),
            self.count("ledger_ref"),
        )

    def writeback(self) -> list[tuple[Any, ...]]:
        self.conn.rollback()
        return self.conn.execute(
            "SELECT value, row_version FROM dms.action_writeback WHERE tenant_id = %s",
            (self.tenant,),
        ).fetchall()

    def status(self, prop: dict[str, Any]) -> str:
        self.conn.rollback()
        row = self.conn.execute(
            "SELECT status FROM dms.proposal_versions WHERE idempotency_token = %s",
            (prop["idempotency_token"],),
        ).fetchone()
        assert row is not None
        return str(row[0])

    def seed_ask(
        self,
        *,
        entry: str,
        badge: str = "validated",
        sql: str = "SELECT sku FROM inventory WHERE quantity_kg < reorder_level_kg",
        rows: int = 2,
        tables: tuple[str, ...] = ("inventory",),
        space: str | None = None,
    ) -> str:
        self.conn.rollback()
        self.conn.execute(
            """
            INSERT INTO dms.ask_audit (
              ask_id, tenant_id, asked_at, actor, actor_kind, space_id, question,
              executed_sql, tables_read, badge, row_count, cortex_entry_id
            ) VALUES (%s, %s, now(), %s, 'deployment', %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid.uuid4(),
                self.tenant,
                str(self.user),
                self.space if space is None else space,
                QUESTION,
                sql,
                list(tables),
                badge,
                rows,
                entry,
            ),
        )
        self.conn.commit()
        return entry

    def configure(self, **env: str) -> None:
        from dms_api.settings import get_settings

        for k, v in env.items():
            self.monkeypatch.setenv(k, v)
        get_settings.cache_clear()


@pytest.fixture
def h(
    conn: psycopg.Connection,
    two_tenants: dict[str, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    migrated_db: str,
) -> Iterator[Harness]:
    tenant, user = two_tenants["alpha"], two_tenants["user"]
    conn.rollback()
    space = conn.execute("SELECT id FROM dms.spaces WHERE tenant_id = %s", (tenant,)).fetchone()
    assert space is not None
    conn.rollback()

    monkeypatch.setenv("DMS_ACTIONS", "1")
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DMS_TENANT_ID", str(tenant))
    monkeypatch.setenv("DMS_ACTOR_USER_ID", str(user))
    monkeypatch.setenv("DMS_ACTOR_ROLE", "steward")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "act02_wh.duckdb"))
    monkeypatch.delenv("DMS_ONTO_PG", raising=False)
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    gate = Recorder()
    monkeypatch.setattr("dms_api.routes.actions.compliance_gate", gate)
    from dms_api.app import create_app

    app = create_app()
    cortex = FakeCortex()
    app.state.cortex = cortex
    yield Harness(
        client=TestClient(app),
        gate=gate,
        cortex=cortex,
        conn=conn,
        tenant=tenant,
        user=user,
        space=str(space[0]),
        monkeypatch=monkeypatch,
        app=app,
    )
    get_settings.cache_clear()


def _ok(resp: Any) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text
    return resp.json()


def _code(resp: Any) -> str:
    detail = resp.json()["detail"]
    return detail["code"] if isinstance(detail, dict) else detail


# --- the happy path ---------------------------------------------------------------


def test_happy_path_rows(h: Harness) -> None:
    prop = _ok(h.propose("450"))
    assert prop["diff"]["before"]["source_value"] == "400"
    assert prop["diff"]["before"]["value"] is None
    assert prop["mutation"]["executed"] is False
    assert prop["status"] == "pending_confirm"
    assert prop["diff"]["summary"] == (
        "Set reorder_level_kg for inventory_item SKU-BETA @ WH-B: 400 (source) -> 450"
    )
    assert h.writeback() == [], "propose must not write the cell"

    receipt = _ok(h.apply(prop, "CAB-1"))
    assert receipt["status"] == "applied"
    assert receipt["after"]["value"] == "450"
    assert receipt["after"]["row_version"] == 1
    assert receipt["before"]["value"] is None
    assert receipt["before"]["source_value"] == "400"
    assert receipt["verified"] is True
    assert receipt["rows_changed"] == 1
    assert receipt["mutation"]["executed"] is True
    assert receipt["mutation"]["source_system_changed"] is False
    assert receipt["mutation"]["target"] == "dms.action_writeback"
    assert receipt["ledger_entry_id"] == "e1"
    assert receipt["chain"] == {"ok": True, "first_break": None}
    assert receipt["separation_of_duties"]["proven"] is False
    assert receipt["identity"] == "config_actor (DR-0004 Option A)"
    assert receipt["approval_ref"] == "CAB-1"
    assert receipt["object"] == {"object_type": "inventory_item", "object_key": KEY}
    assert receipt["summary"] == prop["diff"]["summary"]
    # The after_hash is the digest of the row as it now reads, recomputed here.
    assert receipt["after_hash"] == state_hash(
        {
            "object_type": "inventory_item",
            "object_key": KEY,
            "field": "reorder_level_kg",
            "value": "450",
            "row_version": 1,
        }
    )

    objects = h.objects()
    assert len(objects) == 1
    assert {k: objects[0][k] for k in ("object_key", "field", "value", "row_version")} == {
        "object_key": KEY,
        "field": "reorder_level_kg",
        "value": "450",
        "row_version": 1,
    }
    assert objects[0]["last_decision_id"] == receipt["decision_id"]
    assert h.writeback() == [("450", 1)]
    assert h.status(prop) == "applied"

    applies = [e for e in h.cortex.events if e.event_type == "action.apply"]
    assert len(applies) == 1 and len(h.cortex.events) == 1
    payload = applies[0].payload
    assert isinstance(payload, dict)
    assert payload["schema"] == "dms.action.apply/1"
    assert payload["before_hash"] == receipt["before_hash"]
    assert payload["after_hash"] == receipt["after_hash"]
    assert payload["value"] == "450"
    assert payload["approval_ref"] == "CAB-1"
    assert payload["verified"] is True
    from dms_api.settings import get_settings

    assert applies[0].actor == get_settings().dms_actor_user_id == str(h.user)

    h.conn.rollback()
    joined = h.conn.execute(
        """
        SELECT d.decision, d.after->>'value', l.cortex_entry_id, d.ledger_seq
          FROM dms.action_decisions d
          JOIN dms.ledger_ref l ON l.seq = d.ledger_seq
         WHERE d.tenant_id = %s
        """,
        (h.tenant,),
    ).fetchall()
    assert [r[:3] for r in joined] == [("applied", "450", "e1")]
    assert joined[0][3] == receipt["ledger_seq"]

    listed = _ok(h.client.get(f"{A}/decisions", params={"space_id": h.space}))
    assert [d["id"] for d in listed] == [receipt["decision_id"]]
    one = _ok(h.client.get(f"{A}/decisions/{receipt['decision_id']}", params={"space_id": h.space}))
    assert one["after"]["value"] == "450" and one["cortex_entry_id"] == "e1"
    inbox = _ok(h.client.get(f"{A}/proposals", params={"space_id": h.space}))
    assert [(p["proposal_id"], p["status"]) for p in inbox] == [(prop["proposal_id"], "applied")]


def test_list_gets_require_space_id(h: Harness) -> None:
    for path in ("/objects?object_type=inventory_item", "/decisions", "/proposals"):
        resp = h.client.get(f"{A}{path}")
        assert resp.status_code == 422, f"{path} answered without a space_id"
    other = str(uuid.uuid4())
    resp = h.client.get(f"{A}/decisions", params={"space_id": other})
    assert resp.status_code == 404 and _code(resp) == "space_not_found"


# --- rule 12 -----------------------------------------------------------------------


def test_rule12_key_refused_with_candidate(h: Harness) -> None:
    before = h.counts()
    resp = h.propose("450", object_key={"sku": "BETA", "location_id": "WH-B"})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "object_key_not_found"
    assert {"sku": "SKU-BETA", "location_id": "WH-B"} in detail["candidates"]
    assert detail["object_key"] == {"sku": "BETA", "location_id": "WH-B"}

    lower = h.propose("450", object_key={"sku": "sku-beta", "location_id": "WH-B"})
    assert lower.status_code == 422 and _code(lower) == "object_key_not_found"
    assert h.counts() == before
    assert h.objects() == []


# --- evidence (E9) -------------------------------------------------------------------


def test_evidence_e9(h: Harness) -> None:
    before = h.counts()
    for audit_id in ("", "   ", "led_nope"):
        resp = h.propose("450", evidence_audit_id=audit_id)
        assert resp.status_code == 422 and _code(resp) == "evidence_unknown", resp.text

    h.seed_ask(entry="led_abstain", badge="abstain")
    h.seed_ask(entry="led_nosql", sql="")
    h.seed_ask(entry="led_norows", rows=0)
    for audit_id in ("led_abstain", "led_nosql", "led_norows"):
        resp = h.propose("450", evidence_audit_id=audit_id)
        assert resp.status_code == 422 and _code(resp) == "evidence_not_validated", audit_id

    h.seed_ask(entry="led_ship", tables=("shipments",))
    resp = h.propose("450", evidence_audit_id="led_ship")
    assert resp.status_code == 422 and _code(resp) == "evidence_wrong_table"
    assert h.counts() == before, "no refused propose may leave a row"

    h.seed_ask(entry="led_ok")
    prop = _ok(h.propose("450", evidence_audit_id="led_ok"))
    evidence = {"audit_id": "led_ok", "question": QUESTION, "badge": "validated", "row_count": 2}
    assert prop["diff"]["evidence"] == evidence
    receipt = _ok(h.apply(prop))
    assert receipt["evidence"] == evidence
    assert h.cortex.events[-1].payload["evidence_audit_id"] == "led_ok"

    # Delete the cited answer between propose and apply (as the table owner:
    # no app role can delete an ask_audit row).
    h.seed_ask(entry="led_gone")
    prop2 = _ok(h.propose("500", evidence_audit_id="led_gone"))
    h.conn.execute("DELETE FROM dms.ask_audit WHERE cortex_entry_id = 'led_gone'")
    h.conn.commit()
    resp = h.apply(prop2, "CAB-2")
    assert resp.status_code == 422 and _code(resp) == "evidence_not_validated", resp.text
    assert h.writeback() == [("450", 1)], "the refused apply wrote nothing"
    assert h.status(prop2) == "pending_confirm"


# --- ledger failures -------------------------------------------------------------------


def test_ledger_failures_roll_back(h: Harness) -> None:
    prop = _ok(h.propose("450"))

    h.cortex.fail_appends = 1
    resp = h.apply(prop)
    assert resp.status_code == 503 and _code(resp) == "ledger_append_failed"
    assert h.objects() == []
    assert h.status(prop) == "pending_confirm"
    assert h.count("action_decisions") == 0

    h.cortex.chain_ok = False
    attempts = h.cortex.append_attempts
    resp = h.apply(prop)
    assert resp.status_code == 503 and _code(resp) == "ledger_chain_broken"
    assert h.cortex.append_attempts == attempts, "a broken chain must stop before any append"
    h.cortex.chain_ok = True

    h.app.state.cortex = None
    resp = h.apply(prop)
    assert resp.status_code == 503 and _code(resp) == "ledger_unavailable"
    h.app.state.cortex = h.cortex

    fired = {"n": 0}

    def commit_once(conn: psycopg.Connection) -> None:
        if fired["n"] == 0:
            fired["n"] += 1
            raise psycopg.OperationalError("connection lost at commit")
        conn.commit()

    h.monkeypatch.setattr("dms_api.routes.actions._commit", commit_once)
    resp = h.apply(prop)
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "commit_failed_after_append"
    assert detail["cortex_entry_id"] == "e1"
    assert [e.event_type for e in h.cortex.events] == ["action.apply", "action.apply.void"]
    assert h.cortex.events[1].payload["entry_id"] == "e1"
    assert h.writeback() == []
    assert h.status(prop) == "pending_confirm"

    receipt = _ok(h.apply(prop))  # the fake has recovered: the same token applies
    assert receipt["after"]["value"] == "450" and receipt["ledger_entry_id"] == "e3"
    assert h.writeback() == [("450", 1)]


# --- gate ----------------------------------------------------------------------------


def test_gate_refusals(h: Harness) -> None:
    prop = _ok(h.propose("450"))
    before = h.counts()
    for reason in ("denied", "gate_task_unknown", "gate_unavailable"):
        h.gate.refuse["action.apply"] = reason
        resp = h.apply(prop)
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == reason
        assert h.counts() == before
        assert h.cortex.events == [] and h.cortex.append_attempts == 0
    assert h.status(prop) == "pending_confirm"

    h.gate.refuse["action.propose"] = "denied"
    resp = h.propose("500")
    assert resp.status_code == 403 and resp.json()["detail"] == "denied"
    assert h.counts() == before

    seen = []
    for call in h.gate.calls:
        if call["action"] not in seen:
            seen.append(call["action"])
    assert seen == ["action.propose", "action.apply"]
    assert {c["resource"] for c in h.gate.calls} == {RESOURCE}
    for call in h.gate.calls:
        meta = call["metadata"]
        assert meta["task_id"] == call["action"]
        template = meta["filled_template"]
        assert template["action_type"] == "inventory.set_reorder_level"
        assert template["field"] == "reorder_level_kg"
        assert len(template["spec_sha"]) == 64
        assert "450" not in json.dumps(meta) and "500" not in json.dumps(meta)
        assert call["actor"] == str(h.user)


# --- steward and identity ---------------------------------------------------------------


def test_steward_and_identity(h: Harness) -> None:
    from dms_core.control_plane.onto_data import ontology_data_store

    prop = _ok(h.propose("450"))
    before = h.counts()
    store = ontology_data_store()
    store.deny_steward(str(h.user))
    try:
        for resp in (h.propose("500"), h.apply(prop)):
            assert resp.status_code == 403 and _code(resp) == "steward_grant_required"
    finally:
        store._denied.discard(str(h.user))  # type: ignore[union-attr]
    assert h.counts() == before

    h.configure(DMS_ACTOR_USER_ID="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    for resp in (h.propose("500"), h.apply(prop)):
        assert resp.status_code == 403 and _code(resp) == "steward_identity_unset"
    h.configure(DMS_ACTOR_USER_ID=str(h.user))

    resp = h.apply(prop, "  ")
    assert resp.status_code == 400 and _code(resp) == "approval_ref_required"

    resp = h.apply(prop, "CAB-1", actor="someone-else")
    assert resp.status_code == 422
    resp = h.propose("500", actor="someone-else")
    assert resp.status_code == 422
    assert h.counts() == before
    assert h.cortex.events == []
    assert h.status(prop) == "pending_confirm"


# --- stale, conflict, lock --------------------------------------------------------------


def test_stale_conflict_lock(h: Harness) -> None:
    a = _ok(h.propose("450"))
    b = _ok(h.propose("500"))
    _ok(h.apply(a))

    again = h.apply(a)
    assert again.status_code == 409 and _code(again) == "conflict"

    stale = h.apply(b)
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail == {"code": "object_changed", "current_value": "450", "current_row_version": 1}
    assert [o["value"] for o in h.objects()] == ["450"]
    assert h.status(b) == "pending_confirm"
    assert [e.event_type for e in h.cortex.events] == ["action.apply"]

    unknown = h.client.post(
        f"{A}/proposals/{a['proposal_id']}/apply",
        json={"idempotency_token": "not-a-token", "approval_ref": "CAB-1"},
    )
    assert unknown.status_code == 409 and _code(unknown) == "stale_token"

    c = _ok(h.propose("600"))
    k1, k2 = _lock_keys(h.tenant, "proposal_versions")
    with psycopg.connect(_url(h), autocommit=True) as holder:
        assert holder.execute("SELECT pg_try_advisory_lock(%s, %s)", (k1, k2)).fetchone()[0]
        try:
            locked = h.apply(c)
        finally:
            holder.execute("SELECT pg_advisory_unlock(%s, %s)", (k1, k2))
    assert locked.status_code == 409 and _code(locked) == "lock_timeout"
    assert [o["value"] for o in h.objects()] == ["450"]


def _url(h: Harness) -> str:
    from dms_api.settings import get_settings

    url = get_settings().database_url
    assert url
    return url


def test_concurrent_apply_one_wins(h: Harness) -> None:
    prop = _ok(h.propose("450"))
    out: list[Any] = []

    def go() -> None:
        own = TestClient(h.app)
        body = {"idempotency_token": prop["idempotency_token"], "approval_ref": "CAB-1"}
        out.append(own.post(f"{A}/proposals/{prop['proposal_id']}/apply", json=body))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    codes = sorted(r.status_code for r in out)
    assert codes == [200, 409], [r.text for r in out]
    loser = next(r for r in out if r.status_code == 409)
    assert _code(loser) in {"conflict", "lock_timeout"}
    assert h.writeback() == [("450", 1)]
    assert h.count("action_decisions") == 1


# --- amend cannot bypass -------------------------------------------------------------------


def test_amend_cannot_bypass(h: Harness) -> None:
    prop = _ok(h.propose("450"))
    h.monkeypatch.setattr(
        "dms_api.routes.amend.compliance_gate",
        lambda **kw: ComplianceDecision(allowed=True, reason="ok", action=kw["action"]),
    )
    resp = h.client.post(
        f"/v1/amend/proposals/{prop['proposal_id']}/confirm",
        json={"idempotency_token": prop["idempotency_token"]},
    )
    assert resp.status_code == 409 and resp.json()["detail"] == "action_requires_apply"
    assert h.status(prop) == "pending_confirm"
    assert h.writeback() == []

    before = h.count("proposals")
    forged = h.client.post(
        "/v1/amend/proposals",
        json={"space_id": h.space, "diff": {"kind": "action", "value": "9999"}},
    )
    assert forged.status_code == 422 and forged.json()["detail"] == "action_kind_reserved"
    assert h.count("proposals") == before

    # Flag off: amend behaves as main does. The same forged diff is just a diff
    # (it names no action_proposals row), and it confirms as an amend always has.
    h.monkeypatch.delenv("DMS_ACTIONS")
    plain = h.client.post(
        "/v1/amend/proposals",
        json={"space_id": h.space, "diff": {"kind": "action", "value": "9999"}},
    )
    assert plain.status_code == 200, plain.text
    assert plain.json()["status"] == "pending_confirm"
    # Off, the action routes are gone, and the action's confirm is still refused.
    gone = h.apply(prop)
    assert (gone.status_code, gone.content) == (404, b'{"detail":"Not Found"}')
    still = h.client.post(
        f"/v1/amend/proposals/{prop['proposal_id']}/confirm",
        json={"idempotency_token": prop["idempotency_token"]},
    )
    assert still.status_code == 409 and still.json()["detail"] == "action_requires_apply"
    assert h.writeback() == []


# --- decline and revert ----------------------------------------------------------------------


def test_decline_and_revert(h: Harness) -> None:
    declined = _ok(h.propose("300"))
    out = _ok(
        h.client.post(
            f"{A}/proposals/{declined['proposal_id']}/decline",
            json={
                "idempotency_token": declined["idempotency_token"],
                "approval_ref": "CAB-D",
                "reason": "not this quarter",
            },
        )
    )
    assert out["status"] == "rejected"
    assert out["mutation"]["executed"] is False
    assert [e.event_type for e in h.cortex.events] == ["action.decline"]
    assert h.cortex.events[0].payload["reason"] == "not this quarter"
    assert out["ledger_entry_id"] == "e1"
    assert h.status(declined) == "rejected"
    assert h.writeback() == []
    late = h.apply(declined)
    assert late.status_code == 409 and _code(late) == "stale_token"

    prop = _ok(h.propose("450"))
    receipt = _ok(h.apply(prop))
    rev = h.client.post(f"{A}/decisions/{receipt['decision_id']}/revert", json={})
    rev_prop = _ok(rev)
    assert rev_prop["diff"]["op"] == "clear"
    assert rev_prop["diff"]["value"] is None
    assert rev_prop["diff"]["expected_before_hash"] == receipt["after_hash"]
    assert rev_prop["mutation"]["executed"] is False
    assert rev_prop["status"] == "pending_confirm"
    assert rev_prop["diff"]["summary"] == (
        "Clear reorder_level_kg for inventory_item SKU-BETA @ WH-B: 450 (override) -> 400 (source)"
    )
    assert h.writeback() == [("450", 1)], "a revert proposal changes nothing on its own"

    applied = _ok(h.apply(rev_prop, "CAB-R"))
    assert applied["after"]["value"] is None and applied["after"]["row_version"] == 2
    objects = h.objects()
    assert [(o["value"], o["row_version"]) for o in objects] == [(None, 2)]
    assert h.writeback() == [(None, 2)]

    h.conn.rollback()
    rows = h.conn.execute(
        """
        SELECT decision, rows_changed, verified, after->>'value'
          FROM dms.action_decisions WHERE tenant_id = %s ORDER BY decided_at
        """,
        (h.tenant,),
    ).fetchall()
    assert rows == [
        ("declined", 0, False, None),
        ("applied", 1, True, "450"),
        ("applied", 1, True, None),
    ]
    applied_rows = [r for r in rows if r[0] == "applied"]
    assert len(applied_rows) == 2
    assert [e.event_type for e in h.cortex.events] == [
        "action.decline",
        "action.apply",
        "action.apply",
    ]

    # The first decision's after-state is gone; reverting it again would undo
    # the later change too, so it is refused and leaves no proposal.
    before = h.count("proposals")
    again = h.client.post(f"{A}/decisions/{receipt['decision_id']}/revert", json={})
    assert again.status_code == 409, again.text
    assert again.json()["detail"] == {
        "code": "object_changed",
        "current_value": None,
        "current_row_version": 2,
    }
    assert h.count("proposals") == before
    no = h.client.post(f"{A}/decisions/{out['decision_id']}/revert", json={})
    assert no.status_code == 409 and _code(no) == "decision_not_applied"


# --- ACT-01 fix carried to the routes -------------------------------------------------------


def test_reused_ledger_entry_refused_and_voided(h: Harness) -> None:
    """A Cortex that hands back an entry id already used gets no second receipt."""
    first = _ok(h.apply(_ok(h.propose("450"))))
    assert first["ledger_entry_id"] == "e1"
    prop = _ok(h.propose("500"))
    real = h.cortex.ledger_append

    def reuse_e1(req: Any) -> LedgerAppendResponse:
        out = real(req)
        if req.event_type == "action.apply":
            return LedgerAppendResponse(entry_id="e1", hash=out.hash, seq=out.seq)
        return out

    h.monkeypatch.setattr(h.cortex, "ledger_append", reuse_e1)
    resp = h.apply(prop, "CAB-2")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == {"code": "ledger_entry_reused"}
    assert [e.event_type for e in h.cortex.events] == [
        "action.apply",
        "action.apply",
        "action.apply.void",
    ]
    assert h.cortex.events[2].payload["entry_id"] == "e1"
    assert h.writeback() == [("450", 1)], "the refused receipt rolled the write back"
    assert h.status(prop) == "pending_confirm"
    assert h.count("action_decisions") == 1

    h.monkeypatch.setattr(h.cortex, "ledger_append", real)
    receipt = _ok(h.apply(prop, "CAB-2"))
    assert receipt["after"]["value"] == "500" and receipt["ledger_entry_id"] == "e4"
    assert h.writeback() == [("500", 2)]


def test_decline_ledger_payload_and_receipt_are_masked(h: Harness) -> None:
    """approval_ref and reason reach Cortex and the append-only row masked."""
    prop = _ok(h.propose("300"))
    out = _ok(
        h.client.post(
            f"{A}/proposals/{prop['proposal_id']}/decline",
            json={
                "idempotency_token": prop["idempotency_token"],
                "approval_ref": "CAB jian@example.com",
                "reason": "owner 900101-14-5678 said no",
            },
        )
    )
    assert out["status"] == "rejected"
    payload = h.cortex.events[0].payload
    assert payload["approval_ref"] == "CAB DMSMASK_email_01"
    assert payload["reason"] == "owner DMSMASK_nric_01 said no"
    h.conn.rollback()
    row = h.conn.execute(
        "SELECT approval_ref, evidence->>'decline_reason', actor_id "
        "FROM dms.action_decisions WHERE id = %s",
        (out["decision_id"],),
    ).fetchone()
    assert row == ("CAB DMSMASK_email_01", "owner DMSMASK_nric_01 said no", str(h.user))


# --- round-3 verifier defects ---------------------------------------------------------


def _commit_then_raise(conn: psycopg.Connection) -> None:
    """The COMMIT lands, then the client loses the connection before it hears so."""
    conn.commit()
    raise psycopg.OperationalError("connection lost after COMMIT was sent")


def test_commit_landed_but_raised_is_applied_not_voided(h: Harness) -> None:
    """A commit that landed is a receipt, not a void: the ledger must not say 'void'."""
    prop = _ok(h.propose("450"))
    h.monkeypatch.setattr("dms_api.routes.actions._commit", _commit_then_raise)
    receipt = _ok(h.apply(prop))
    assert receipt["status"] == "applied"
    assert receipt["after"]["value"] == "450" and receipt["verified"] is True
    assert receipt["ledger_entry_id"] == "e1"
    assert [e.event_type for e in h.cortex.events] == ["action.apply"], "no false void"
    assert h.writeback() == [("450", 1)]
    assert h.status(prop) == "applied"
    h.conn.rollback()
    row = h.conn.execute(
        "SELECT d.id, d.ledger_seq, r.cortex_entry_id FROM dms.action_decisions d "
        "JOIN dms.ledger_ref r ON r.seq = d.ledger_seq WHERE d.tenant_id = %s",
        (h.tenant,),
    ).fetchall()
    assert row == [(uuid.UUID(receipt["decision_id"]), receipt["ledger_seq"], "e1")]
    assert [o["value"] for o in h.objects()] == ["450"]

    # The decline route has the same window (propose commits normally).
    h.monkeypatch.setattr("dms_api.routes.actions._commit", lambda conn: conn.commit())
    prop2 = _ok(h.propose("500"))
    h.monkeypatch.setattr("dms_api.routes.actions._commit", _commit_then_raise)
    out = _ok(
        h.client.post(
            f"{A}/proposals/{prop2['proposal_id']}/decline",
            json={"idempotency_token": prop2["idempotency_token"], "approval_ref": "CAB-2"},
        )
    )
    assert out["status"] == "rejected" and out["ledger_entry_id"] == "e2"
    assert [e.event_type for e in h.cortex.events] == ["action.apply", "action.decline"]
    assert h.status(prop2) == "rejected"
    assert h.count("action_decisions") == 2


def test_commit_outcome_unknown_appends_no_void(h: Harness) -> None:
    """When the database cannot say whether the commit landed, neither void nor claim."""
    prop = _ok(h.propose("450"))

    def commit_lost(conn: psycopg.Connection) -> None:
        raise psycopg.OperationalError("connection lost at commit")

    h.monkeypatch.setattr("dms_api.routes.actions._commit", commit_lost)
    h.monkeypatch.setattr(
        "dms_api.routes.actions._decision_by_entry", lambda settings, entry: ("unknown", None)
    )
    resp = h.apply(prop)
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == {"code": "commit_outcome_unknown", "cortex_entry_id": "e1"}
    assert [e.event_type for e in h.cortex.events] == ["action.apply"]
    assert h.writeback() == [], "the uncommitted write was not committed by the context exit"
    assert h.status(prop) == "pending_confirm"


def test_empty_entry_id_rolls_back_with_503(h: Harness) -> None:
    prop = _ok(h.propose("450"))
    before = h.counts()
    real = h.cortex.ledger_append

    def blank_id(req: Any) -> LedgerAppendResponse:
        out = real(req)
        return LedgerAppendResponse(entry_id="", hash=out.hash, seq=out.seq)

    h.monkeypatch.setattr(h.cortex, "ledger_append", blank_id)
    resp = h.apply(prop)
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == {"code": "ledger_append_failed", "reason": "empty_entry_id"}
    assert h.counts() == before
    assert h.writeback() == [] and h.objects() == []
    assert h.status(prop) == "pending_confirm"

    h.monkeypatch.setattr(h.cortex, "ledger_append", real)
    receipt = _ok(h.apply(prop))
    assert receipt["after"]["value"] == "450" and receipt["ledger_entry_id"] == "e2"


def test_evidence_from_another_space_refused(h: Harness) -> None:
    before = h.counts()
    other = str(uuid.uuid4())
    h.seed_ask(entry="led_other_space", space=other)
    resp = h.propose("450", evidence_audit_id="led_other_space")
    assert resp.status_code == 422, resp.text
    assert _code(resp) == "evidence_wrong_space"
    assert h.counts() == before

    # The same Space, in another spelling of its UUID, is the same Space.
    h.seed_ask(entry="led_same_space", space=h.space.upper())
    prop = _ok(h.propose("450", evidence_audit_id="led_same_space"))
    assert prop["diff"]["evidence"]["audit_id"] == "led_same_space"

    # An answer asked with no Space is tenant-wide.
    h.seed_ask(entry="led_no_space", space="")
    prop2 = _ok(h.propose("500", evidence_audit_id="led_no_space"))
    assert prop2["diff"]["evidence"]["audit_id"] == "led_no_space"


def test_long_approval_ref_and_decline_reason_refused(h: Harness) -> None:
    prop = _ok(h.propose("450"))
    before = h.counts()
    resp = h.apply(prop, "X" * 201)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == {"code": "approval_ref_too_long", "max": 200}

    resp = h.client.post(
        f"{A}/proposals/{prop['proposal_id']}/decline",
        json={
            "idempotency_token": prop["idempotency_token"],
            "approval_ref": "CAB-1",
            "reason": "r" * 501,
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == {"code": "reason_too_long", "max": 500}
    assert h.counts() == before
    assert h.cortex.events == []
    assert h.status(prop) == "pending_confirm"

    receipt = _ok(h.apply(prop, "X" * 200))
    assert receipt["approval_ref"] == "X" * 200


def test_actor_not_a_uuid_refused_not_500(h: Harness) -> None:
    prop = _ok(h.propose("450"))
    before = h.counts()
    h.configure(DMS_ACTOR_USER_ID="steward-jian")
    try:
        for resp in (h.propose("500"), h.apply(prop)):
            assert resp.status_code == 403, resp.text
            assert _code(resp) == "steward_identity_invalid"
    finally:
        h.configure(DMS_ACTOR_USER_ID=str(h.user))
    assert h.counts() == before
    assert h.cortex.events == []


def test_rule12_candidates_match_any_key_field(h: Harness) -> None:
    """A wrong location still shows the SKU where it does exist, best match first."""
    resp = h.propose("450", object_key={"sku": "SKU-BETA", "location_id": "WH-Z"})
    assert resp.status_code == 422 and _code(resp) == "object_key_not_found", resp.text
    candidates = resp.json()["detail"]["candidates"]
    assert {"sku": "SKU-BETA", "location_id": "WH-B"} in candidates
    assert candidates[0]["sku"] == "SKU-BETA", "a key matching on more fields ranks first"
    assert 0 < len(candidates) <= 5

    exact_both = h.propose("450", object_key={"sku": "BETA", "location_id": "WH-B"})
    first = exact_both.json()["detail"]["candidates"][0]
    assert first == {"sku": "SKU-BETA", "location_id": "WH-B"}
    assert h.objects() == []


def test_apply_payload_and_propose_reason_are_masked(h: Harness) -> None:
    """The apply's ledger payload and the propose diff carry no raw PII."""
    prop = _ok(h.propose("450", reason="owner 900101-14-5678 asked"))
    assert prop["diff"]["reason"] == "owner DMSMASK_nric_01 asked"
    receipt = _ok(h.apply(prop, "CAB jian@example.com"))
    assert receipt["approval_ref"] == "CAB DMSMASK_email_01"
    payload = h.cortex.events[0].payload
    assert payload["approval_ref"] == "CAB DMSMASK_email_01"
    assert "jian@example.com" not in json.dumps(payload)
    assert "900101-14-5678" not in json.dumps(payload)
