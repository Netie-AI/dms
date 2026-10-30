"""INS-PROOF-03 - steward pins for proven insights, behind ``DMS_INSIGHT_PROOF`` (default off).

A steward previews one proven insight (re-proved server side, never from client
SQL or rows), confirms it with a single-use token, and the pin lands in the
DMS-owned register ``main._proven_insights`` after one Cortex ledger pointer.
Replay re-executes the pinned SQL twice through Cortex submit and says whether
the figures are unchanged (L0), changed (L1 with a drift block), or whether the
approved definition moved (ABSTAIN, zero submits). Nothing is auto-pinned,
auto-replayed or auto-applied.

``FakeCortex`` (from the INS-PROOF-02 tests) stands in for Cortex: submit runs
on a tmp warehouse, ledger appends are recorded. Nothing here claims a Cortex
run. Assertions are on the rendered text, the returned rows and the receipt
(hard rules 10 and 10a).
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlglot
from cortex_client.models import LedgerAppendRequest, LedgerAppendResponse
from dms_api.app import create_app
from dms_core.control_plane import onto_data
from dms_core.control_plane.ask_audit import InMemoryAskAuditStore
from dms_executor import Executor
from dms_executor import bronze as bronze_mod
from dms_executor import demo_warehouse as dw
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import SessionAcl
from fastapi.testclient import TestClient
from sqlglot import exp

from tests.test_insight_proof_01 import _items, _seed_sales
from tests.test_insight_proof_02_route import FakeCortex, _clear_settings, _GateSpy, _minter

_FLAG = "DMS_INSIGHT_PROOF"
_B_HEADLINE = "2 of 4 Territory.name values (North, South) carry 86.4 pct of order_amount"
_A_PART = "carry 73.2 pct of stock_value_myr"
_B_GRANTS = {"Sales.Orders": "TRUE", "Sales.Territory": "TRUE"}
#: The configured steward (DR-0004 Option A). The shipped default actor may not
#: confirm a pin (``steward_identity_unset``), so every pin test configures one.
_ACTOR = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_DEFAULT_ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"  # settings.dms_actor_user_id default
_STAMP_A = "2026-10-01T00:00:00Z"
_STAMP_B = "2026-10-02T00:00:00Z"


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class _Cortex(FakeCortex):
    """FakeCortex with one fresh entry id per append, so 'the new entry' is checkable."""

    def __init__(self, warehouse: Path) -> None:
        super().__init__(warehouse)
        self._n = 100
        #: When set, every append returns this receipt (a replayed or stale one).
        self.fixed: LedgerAppendResponse | None = None

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.ledger.append(req)
        if self.ledger_error is not None:
            raise self.ledger_error
        if self.fixed is not None:
            return self.fixed
        self._n += 1
        return LedgerAppendResponse(
            entry_id=f"led_{self._n:06d}", hash=f"{self._n:064x}", seq=self._n
        )

    def events(self, event_type: str) -> list[LedgerAppendRequest]:
        return [r for r in self.ledger if r.event_type == event_type]


class _Marks:
    """Ingest watermarks the test controls. Steady unless the test bumps them."""

    def __init__(self) -> None:
        self.stamp: str | None = _STAMP_A

    def __call__(self, *, path: Path | None = None) -> dict[str, dict[str, Any]]:
        if self.stamp is None:
            return {}
        return {t: {"extracted_at": self.stamp} for t in ("orders", "territory")}


class _Onto:
    """The approved store the route reads with ``DMS_ONTO_FROM_DATA=1``."""

    def __init__(self) -> None:
        self.approved: list[dict[str, Any]] = _items()
        self.revoked: list[dict[str, Any]] = []


class _World:
    def __init__(self, client: TestClient, cortex: _Cortex, gate: _GateSpy,
                 audit: InMemoryAskAuditStore, wh: Path, marks: _Marks, onto: _Onto) -> None:
        self.client = client
        self.cortex = cortex
        self.gate = gate
        self.audit = audit
        self.wh = wh
        self.marks = marks
        self.onto = onto

    def preview(self, insight_id: str, **extra: Any) -> Any:
        return self.client.post(
            "/v1/insight-proof/pins/preview",
            json={"space_id": None, "insight_id": insight_id, **extra},
        )

    def confirm(self, token: str) -> Any:
        return self.client.post("/v1/insight-proof/pins/confirm", json={"token": token})

    def replay(self, asset_id: str) -> Any:
        return self.client.post(f"/v1/insight-proof/pins/{asset_id}/replay")

    def revoke(self, asset_id: str) -> Any:
        return self.client.post(f"/v1/insight-proof/pins/{asset_id}/revoke")

    def pins(self) -> Any:
        return self.client.get("/v1/insight-proof/pins")

    def run(self) -> Any:
        return self.client.post("/v1/insight-proof/run", json={})

    def records(self) -> list[Any]:
        return self.audit.list_between(since=None, until=None, limit=10_000)

    def register_rows(self) -> int:
        con = dw.connect_serving(self.wh)
        try:
            try:
                return int(con.execute("SELECT COUNT(*) FROM main._proven_insights").fetchone()[0])
            except Exception:  # noqa: BLE001 - no table yet is no rows
                return 0
        finally:
            con.close()


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _clear_settings()
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_ACTOR_USER_ID", _ACTOR)
    _clear_settings()
    yield
    _clear_settings()


@pytest.fixture(autouse=True)
def _fresh_store() -> Iterator[None]:
    onto_data.reset_ontology_data_store()
    yield
    onto_data.reset_ontology_data_store()


def _build(
    monkeypatch: pytest.MonkeyPatch,
    wh: Path,
    *,
    approved_store: bool,
    gate: _GateSpy | None = None,
    grants: dict[str, str] | None = None,
) -> _World:
    cortex = _Cortex(wh)
    spy = gate or _GateSpy()
    marks = _Marks()
    onto = _Onto()
    monkeypatch.setattr("dms_api.routes.insight_proof.compliance_gate", spy)
    monkeypatch.setattr(bronze_mod, "lookup_ingest_watermarks", marks)
    if approved_store:
        monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
        store = onto_data.ontology_data_store()
        monkeypatch.setattr(store, "approved_items", lambda: copy.deepcopy(onto.approved))
        monkeypatch.setattr(store, "revoked_items", lambda: copy.deepcopy(onto.revoked))
    else:
        monkeypatch.delenv("DMS_ONTO_FROM_DATA", raising=False)
    app = create_app()
    app.state.cortex = cortex
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=wh)  # type: ignore[arg-type]
    if grants is not None:
        real = exe.demo_acl

        def _acl(**kw: Any) -> SessionAcl:
            acl = real(**kw)
            return SessionAcl(**{**acl.__dict__, "row_predicates": dict(grants)})

        exe.demo_acl = _acl  # type: ignore[method-assign]
    app.state.ask_service = exe
    audit = InMemoryAskAuditStore()
    app.state.ask_audit_store = audit
    return _World(TestClient(app), cortex, spy, audit, wh, marks, onto)


@pytest.fixture
def world(flag_on: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    wh = _seed_sales(tmp_path / "lake_b.duckdb")
    return _build(monkeypatch, wh, approved_store=True, grants=_B_GRANTS)


def _insight_id(w: _World, headline: str = _B_HEADLINE) -> str:
    body = w.run().json()
    hits = [i for i in body["insights"] if i["envelope"]["text"] == headline]
    assert len(hits) == 1, [i["envelope"]["text"] for i in body["insights"]]
    return str(hits[0]["proof"]["insight_id"])


def _pin(w: _World) -> tuple[str, dict[str, Any], dict[str, Any]]:
    iid = _insight_id(w)
    pre = w.preview(iid)
    assert pre.status_code == 200, pre.text
    got = w.confirm(pre.json()["token"])
    assert got.status_code == 200, got.text
    return str(got.json()["asset_id"]), pre.json(), got.json()


def _update(wh: Path, sql: str) -> None:
    con = dw.connect_file(wh, write=True)
    try:
        con.execute(sql)
    finally:
        con.close()


def _counts(wh: Path) -> dict[tuple[str, str], int]:
    con = dw.connect_serving(wh)
    try:
        names = con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' ORDER BY 1, 2"
        ).fetchall()
        return {
            (s, t): int(con.execute(f'SELECT COUNT(*) FROM "{s}"."{t}"').fetchone()[0])
            for s, t in names
        }
    finally:
        con.close()


def _snapshot(wh: Path) -> dict[tuple[str, str], str]:
    """Every customer relation's full content, to show no write reached one."""
    con = dw.connect_serving(wh)
    try:
        names = con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' ORDER BY 1, 2"
        ).fetchall()
        out: dict[tuple[str, str], str] = {}
        for s, t in names:
            if t in ("_proven_insights", "_proven_insight_receipts"):
                continue
            rows = con.execute(f'SELECT * FROM "{s}"."{t}"').fetchall()
            out[(s, t)] = json.dumps(sorted(map(repr, rows)))
        return out
    finally:
        con.close()


# --------------------------------------------------------------------------
# A1. preview then confirm
# --------------------------------------------------------------------------


def test_preview_then_confirm_pins_one_insight_with_a_pointer_only_ledger_entry(
    world: _World,
) -> None:
    iid = _insight_id(world)
    pre = world.preview(iid)
    assert pre.status_code == 200, pre.text
    body = pre.json()
    assert set(body) == {"envelope", "proof", "token", "expires_at"}
    env, proof = body["envelope"], body["proof"]
    assert env["text"] == _B_HEADLINE
    assert env["rows"][0] == {"label": "North", "value": 900.0, "share_pct": 81.8,
                              "cum_share_pct": 81.8, "grouped_total": 1100.0}
    assert env["rows"][1]["cum_share_pct"] == 86.4
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert_envelope_valid(env)
    assert proof["insight_id"] == iid
    assert proof["definition"]["source"] == "approved_store"
    assert body["token"].startswith("ipc")
    assert world.cortex.events("insight.pinned") == []
    assert world.register_rows() == 0

    got = world.confirm(body["token"])
    assert got.status_code == 200, got.text
    asset_id = got.json()["asset_id"]
    assert asset_id.startswith("pin_") and len(asset_id) == 20

    listed = world.pins()
    assert listed.status_code == 200, listed.text
    pins = listed.json()["pins"]
    assert len(pins) == 1
    pin = pins[0]
    assert pin["asset_id"] == asset_id
    assert pin["expected_result_hash"] == proof["result_hash"]
    assert pin["approved_by"] == _ACTOR
    assert pin["insight_id"] == iid

    pinned = world.cortex.events("insight.pinned")
    assert len(pinned) == 1
    payload = pinned[0].payload
    assert "sql" not in payload and "rows" not in payload
    assert payload["asset_id"] == asset_id
    assert payload["expected_result_hash"] == proof["result_hash"]
    assert payload["approved_by"] == _ACTOR
    assert pinned[0].actor == _ACTOR
    blob = json.dumps(payload)
    assert env["sql_used"] not in blob
    assert "North" not in blob and "86.4" not in blob
    assert pin["ledger_entry_id"] == got.json()["ledger_entry_id"]
    # Confirm writes one audit row for the re-proved insight, pointing at the entry.
    rec = [r for r in world.records() if r.question == "insight:" + iid]
    assert rec and rec[-1].cortex_entry_id == pin["ledger_entry_id"]


# --------------------------------------------------------------------------
# A2. single use, expiry, already pinned, concurrency
# --------------------------------------------------------------------------


def test_a_token_is_single_use_expires_and_a_second_pin_is_refused(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import insight_register as ir

    iid = _insight_id(world)
    pre = world.preview(iid).json()
    first = world.confirm(pre["token"])
    assert first.status_code == 200, first.text
    asset_id = first.json()["asset_id"]

    again = world.confirm(pre["token"])
    assert again.status_code == 409
    assert again.json()["code"] == "confirm_token_used"
    assert world.register_rows() == 1
    assert len(world.cortex.events("insight.pinned")) == 1

    unknown = world.confirm("ipc" + "z" * 26)
    assert unknown.status_code == 409 and unknown.json()["code"] == "confirm_token_unknown"

    late = world.preview(iid).json()
    real = ir._clock
    monkeypatch.setattr(ir, "_clock", lambda: real() + timedelta(seconds=601))
    expired = world.confirm(late["token"])
    assert expired.status_code == 410
    assert expired.json()["code"] == "confirm_token_expired"
    monkeypatch.setattr(ir, "_clock", real)

    second = world.preview(iid).json()
    dup = world.confirm(second["token"])
    assert dup.status_code == 409
    assert dup.json()["code"] == "already_pinned"
    assert dup.json()["asset_id"] == asset_id
    assert world.register_rows() == 1
    assert len(world.cortex.events("insight.pinned")) == 1


def test_two_threads_confirming_two_tokens_pin_exactly_once(world: _World) -> None:
    iid = _insight_id(world)
    tokens = [world.preview(iid).json()["token"] for _ in range(2)]
    barrier = threading.Barrier(2)
    results: list[Any] = [None, None]

    def go(i: int) -> None:
        barrier.wait()
        results[i] = world.confirm(tokens[i])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 409)
    winner = next(r for r in results if r.status_code == 200)
    assert loser.json()["code"] == "already_pinned"
    assert loser.json()["asset_id"] == winner.json()["asset_id"]
    assert world.register_rows() == 1
    assert len(world.cortex.events("insight.pinned")) == 1


# --------------------------------------------------------------------------
# A3. stale preview
# --------------------------------------------------------------------------


def test_rows_that_change_between_preview_and_confirm_are_a_stale_preview(
    world: _World,
) -> None:
    iid = _insight_id(world)
    pre = world.preview(iid).json()
    _update(world.wh, "UPDATE Sales.Orders SET amount = 35 WHERE order_id = 4")
    got = world.confirm(pre["token"])
    assert got.status_code == 409, got.text
    body = got.json()
    assert body["code"] == "stale_preview"
    assert body["shown_result_hash"] == pre["proof"]["result_hash"]
    assert body["current_result_hash"] and body["current_result_hash"] != body["shown_result_hash"]
    assert world.cortex.events("insight.pinned") == []
    assert world.register_rows() == 0


# --------------------------------------------------------------------------
# A4. a demo-ontology insight is never pinnable
# --------------------------------------------------------------------------


def test_a_demo_ontology_insight_is_not_pinnable(
    flag_on: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wh = dw.ensure_demo_warehouse(tmp_path / "demo.duckdb")
    w = _build(monkeypatch, wh, approved_store=False)
    iid = _insight_id(w, "2 of 5 location_code values (WH-B, WH-E) " + _A_PART)
    pre = w.preview(iid)
    assert pre.status_code == 200, pre.text
    assert pre.json()["proof"]["definition"]["source"] == "demo_ontology"
    submits = len(w.cortex.submits)
    got = w.confirm(pre.json()["token"])
    assert got.status_code == 409
    assert got.json()["code"] == "measure_not_approved"
    assert len(w.cortex.submits) == submits  # refused before any re-prove
    assert w.pins().json()["pins"] == []
    assert w.cortex.events("insight.pinned") == []


# --------------------------------------------------------------------------
# A5. replay, unchanged
# --------------------------------------------------------------------------


def test_replay_on_unchanged_data_is_certified(world: _World) -> None:
    asset_id, pre, _ = _pin(world)
    res = world.replay(asset_id)
    assert res.status_code == 200, res.text
    body = res.json()
    env, proof = body["envelope"], body["proof"]
    assert env["badge"] == "L0_CERTIFIED"
    assert env["text"].startswith("Unchanged since steward approval on ")
    assert "carry 86.4 pct of order_amount" in env["text"]
    assert env["rows"] == pre["envelope"]["rows"]
    assert env["served_route"] == "verified_query"
    assert env["abstained"] is False
    assert_envelope_valid(env)
    replayed = world.cortex.events("insight.replayed")
    assert len(replayed) == 1
    entry_id = world.cortex._n  # the last append
    assert env["audit_id"] == f"led_{entry_id:06d}"
    assert replayed[0].payload["verdict"] == "unchanged"
    assert set(replayed[0].payload) == {"asset_id", "sql_hash", "result_hash", "verdict"}
    assert proof["result_hash"] == proof["reexec_result_hash"] == pre["proof"]["result_hash"]
    assert proof["drift"] is None
    rec = [r for r in world.records() if r.question == "insight:" + pre["proof"]["insight_id"]]
    assert rec[-1].cortex_entry_id == env["audit_id"]
    assert rec[-1].badge == "validated" and rec[-1].badge_level == "L0_CERTIFIED"
    assert rec[-1].executed_sql == env["sql_used"]
    assert rec[-1].row_count == len(env["rows"])


# --------------------------------------------------------------------------
# A6. replay after the data changed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bump", [False, True])
def test_replay_after_an_update_reports_drift_by_label(world: _World, bump: bool) -> None:
    asset_id, pre, _ = _pin(world)
    _update(world.wh, "UPDATE Sales.Orders SET amount = 300 WHERE order_id = 2")
    if bump:
        world.marks.stamp = _STAMP_B
    res = world.replay(asset_id)
    assert res.status_code == 200, res.text
    env, proof = res.json()["envelope"], res.json()["proof"]
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["text"] == (
        "2 of 4 Territory.name values (North, South) carry 87.5 pct of order_amount"
    )
    assert "86.4" not in env["text"]
    assert env["rows"][1]["cum_share_pct"] == 87.5
    assert env["rows"][0] == {"label": "North", "value": 1000.0, "share_pct": 83.3,
                              "cum_share_pct": 83.3, "grouped_total": 1200.0}
    assert any(a.startswith("Figures changed since steward approval on ")
               for a in env["assumptions"])
    drift = proof["drift"]
    assert drift["labels_changed"] == ["North"]
    assert drift["labels_added"] == [] and drift["labels_removed"] == []
    assert drift["approved_result_hash"] == pre["proof"]["result_hash"]
    assert drift["approved_result_hash"] != drift["current_result_hash"]
    assert drift["current_result_hash"] == proof["result_hash"]
    assert drift["reason"] == ("source_refreshed" if bump else "drift_without_source_change")
    assert_envelope_valid(env)
    assert world.cortex.events("insight.replayed")[-1].payload["verdict"] == "changed"


def test_a_replay_proof_cannot_widen_its_own_tolerance(world: _World) -> None:
    """INS-PROOF-01 verify round 4: tolerance is conservation_tolerance(n_groups)."""
    from dms_executor import insight_proof as ip
    from dms_executor.insight_register import check_replay_proof

    asset_id, _pre, _ = _pin(world)
    res = world.replay(asset_id)
    assert res.status_code == 200, res.text
    proof = res.json()["proof"]
    assert proof["verdict"] == "unchanged"
    assert proof["tolerance"] == ip.conservation_tolerance(proof["n_groups"])
    check_replay_proof(proof)
    widened = {**proof, "tolerance": proof["tolerance"] + 1000.0,
               "grouped_total": proof["raw_total"] + 500.0}
    with pytest.raises(ValueError, match="tolerance not bound"):
        check_replay_proof(widened)


def test_drift_with_unknown_watermarks_does_not_claim_no_source_change(world: _World) -> None:
    world.marks.stamp = None
    asset_id, _pre, _ = _pin(world)
    _update(world.wh, "UPDATE Sales.Orders SET amount = 300 WHERE order_id = 2")
    proof = world.replay(asset_id).json()["proof"]
    assert proof["drift"]["reason"] == "source_change_unknown"


# --------------------------------------------------------------------------
# A7. the approved definition changed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["expression", "revoked"])
def test_replay_abstains_when_the_definition_changed(world: _World, how: str) -> None:
    asset_id, _pre, _ = _pin(world)
    measure = next(i for i in world.onto.approved if i["kind"] == "measure"
                   and i["payload"]["name"] == "order_amount")
    if how == "expression":
        measure["payload"]["expression"] = "ROUND(SUM(f.amount) * 2, 2)"
    else:
        world.onto.approved.remove(measure)
        world.onto.revoked.append({**measure, "status": "revoked"})
    before = list(world.cortex.submits)
    res = world.replay(asset_id)
    assert res.status_code == 200, res.text
    env = res.json()["envelope"]
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["values"] == [] and env["rows"] == []
    assert "order_amount" in env["text"] and "reconfirm" in env["text"]
    assert world.cortex.submits == before
    assert_envelope_valid(env)
    assert world.cortex.events("insight.replayed")[-1].payload["verdict"] == "definition_changed"
    assert env["audit_id"] == f"led_{world.cortex._n:06d}"
    rec = [r for r in world.records() if r.question == "insight:" + _pre["proof"]["insight_id"]]
    assert rec[-1].badge == "abstain" and rec[-1].cortex_entry_id == env["audit_id"]
    assert rec[-1].executed_sql == "" and rec[-1].row_count == 0


def test_replay_under_a_same_bare_name_grant_of_another_schema_never_submits(
    flag_on: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INS-PROOF-01 round 5 (HIGH) on the replay path: a grant of Secret.Orders /
    Secret.Territory matched the pinned Sales.* tables by bare name. Replay now
    runs main's statement check on every statement before any submit."""
    wh = _seed_sales(tmp_path / "lake_b.duckdb")
    _update(wh, "CREATE SCHEMA IF NOT EXISTS Secret")
    _update(wh, "CREATE TABLE Secret.Orders AS SELECT * FROM Sales.Orders")
    _update(wh, "CREATE TABLE Secret.Territory AS SELECT * FROM Sales.Territory")
    grants = dict(_B_GRANTS)
    w = _build(monkeypatch, wh, approved_store=True, grants=grants)
    asset_id, pre, _ = _pin(w)
    assert pre["envelope"]["text"] == _B_HEADLINE
    grants.clear()
    grants.update({"Secret.Orders": "TRUE", "Secret.Territory": "TRUE"})
    before = list(w.cortex.submits)
    exe = w.client.app.state.ask_service
    tried: list[str] = []
    real_submit = exe._submit_verified_sql

    def _spy(sql: str, **kw: Any) -> Any:
        tried.append(sql)
        return real_submit(sql, **kw)

    monkeypatch.setattr(exe, "_submit_verified_sql", _spy)
    res = w.replay(asset_id)
    assert tried == []  # refused before the submit path is entered, not inside it
    assert res.status_code == 200, res.text
    env = res.json()["envelope"]
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["values"] == [] and env["rows"] == []
    assert env["text"] != _B_HEADLINE
    for token in ("North", "South", "86.4"):
        assert token not in env["text"]
    assert_envelope_valid(env)
    assert w.cortex.submits == before


# --------------------------------------------------------------------------
# A8. refusals with no side effects
# --------------------------------------------------------------------------


def _quiet(w: _World, submits: int = 0, ledger: int = 0, rows: int = 0) -> None:
    assert len(w.cortex.submits) == submits
    assert len(w.cortex.ledger) == ledger
    assert w.register_rows() == rows


def test_a_preview_body_carrying_sql_or_rows_is_422(world: _World) -> None:
    iid = "ins_" + "0" * 16
    assert world.preview(iid, sql="SELECT 1").status_code == 422
    assert world.preview(iid, rows=[{"label": "x"}]).status_code == 422
    assert world.client.post(
        "/v1/insight-proof/pins/confirm", json={"token": "ipcx", "sql": "SELECT 1"}
    ).status_code == 422
    assert world.gate.calls == []
    _quiet(world)


@pytest.mark.parametrize(
    ("allowed", "reason", "status"),
    [(False, "denied", 403), (False, "gate_unavailable", 503)],
)
def test_a_gate_without_an_allow_stops_every_pin_route(
    flag_on: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    allowed: bool, reason: str, status: int,
) -> None:
    wh = _seed_sales(tmp_path / "lake_b.duckdb")
    gate = _GateSpy()
    w = _build(monkeypatch, wh, approved_store=True, gate=gate, grants=_B_GRANTS)
    asset_id, pre, _ = _pin(w)
    token = w.preview(pre["proof"]["insight_id"]).json()["token"]
    submits, ledger, rows = len(w.cortex.submits), len(w.cortex.ledger), w.register_rows()
    audit = len(w.records())
    gate.allowed, gate.reason = allowed, reason
    calls = [
        ("insight_proof.pin.preview", lambda: w.preview(pre["proof"]["insight_id"])),
        ("insight_proof.pin.confirm", lambda: w.confirm(token)),
        ("insight_proof.replay", lambda: w.replay(asset_id)),
        ("insight_proof.revoke", lambda: w.revoke(asset_id)),
        ("insight_proof.pins.read", w.pins),
    ]
    for action, call in calls:
        res = call()
        assert res.status_code == status, (action, res.text)
        if status == 503:
            assert res.json()["code"] == "cortex_unavailable"
        assert w.gate.calls[-1]["action"] == action
        assert w.gate.calls[-1]["metadata"]["task_id"] == action
    _quiet(w, submits, ledger, rows)
    assert len(w.records()) == audit
    # The token was not spent by a refused gate.
    gate.allowed, gate.reason = True, "ok"
    assert w.confirm(token).json()["code"] == "already_pinned"


def test_ledger_down_at_confirm_is_503_with_no_row(world: _World) -> None:
    iid = _insight_id(world)
    pre = world.preview(iid).json()
    world.cortex.ledger_error = RuntimeError("ledger down")
    got = world.confirm(pre["token"])
    assert got.status_code == 503
    assert got.json()["code"] == "ledger_append_failed"
    assert world.register_rows() == 0


def test_an_insert_that_raises_after_the_append_names_the_orphan_entry(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import insight_register as ir

    iid = _insight_id(world)
    pre = world.preview(iid).json()

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(ir, "_insert_pin", boom)
    got = world.confirm(pre["token"])
    assert got.status_code == 500
    body = got.json()
    assert body["code"] == "pin_commit_failed"
    pinned = world.cortex.events("insight.pinned")
    assert len(pinned) == 1
    assert body["orphan_ledger_entry"] == f"led_{world.cortex._n:06d}"
    assert world.register_rows() == 0


# --------------------------------------------------------------------------
# A9. revoke
# --------------------------------------------------------------------------


def test_revoke_unlists_the_pin_and_replay_is_404(world: _World) -> None:
    asset_id, _pre, _ = _pin(world)
    res = world.revoke(asset_id)
    assert res.status_code == 200, res.text
    assert res.json()["asset_id"] == asset_id
    revoked = world.cortex.events("insight.pin_revoked")
    assert len(revoked) == 1 and revoked[0].payload["asset_id"] == asset_id
    assert "sql" not in revoked[0].payload and "rows" not in revoked[0].payload
    assert world.pins().json()["pins"] == []
    before = list(world.cortex.submits)
    gone = world.replay(asset_id)
    assert gone.status_code == 404
    assert gone.json()["code"] == "pin_not_found"
    assert world.cortex.submits == before
    again = world.revoke(asset_id)
    assert again.status_code == 409
    assert again.json()["code"] == "not_pinned"
    assert world.revoke("pin_" + "0" * 16).status_code == 404


# --------------------------------------------------------------------------
# A10. write targets
# --------------------------------------------------------------------------


def test_the_only_table_written_is_the_register(world: _World, tmp_path: Path) -> None:
    iid = _insight_id(world)
    counts_before = _counts(world.wh)
    content_before = _snapshot(world.wh)
    pre = world.preview(iid).json()
    asset_id = world.confirm(pre["token"]).json()["asset_id"]
    assert world.replay(asset_id).status_code == 200
    assert world.revoke(asset_id).status_code == 200
    counts_after = _counts(world.wh)
    changed = {k for k in set(counts_before) | set(counts_after)
               if counts_before.get(k) != counts_after.get(k)}
    assert changed == {("main", "_proven_insights"), ("main", "_proven_insight_receipts")}
    assert _snapshot(world.wh) == content_before
    assert not list(tmp_path.rglob("*.xlsx"))
    assert world.cortex.submits
    for sql in world.cortex.submits:
        trees = sqlglot.parse(sql, read="duckdb")
        assert len(trees) == 1, sql
        tree = trees[0]
        assert isinstance(tree, exp.Select), sql
        assert not list(tree.find_all(exp.Insert, exp.Update, exp.Delete, exp.Create,
                                      exp.Drop, exp.Alter, exp.Merge)), sql


# --------------------------------------------------------------------------
# A12. flag off
# --------------------------------------------------------------------------


def test_flag_off_every_pin_path_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings()
    monkeypatch.delenv(_FLAG, raising=False)
    client = TestClient(create_app())
    assert client.post("/v1/insight-proof/pins/preview",
                       json={"insight_id": "ins_" + "0" * 16}).status_code == 404
    assert client.post("/v1/insight-proof/pins/confirm", json={"token": "x"}).status_code == 404
    assert client.post("/v1/insight-proof/pins/pin_x/replay").status_code == 404
    assert client.post("/v1/insight-proof/pins/pin_x/revoke").status_code == 404
    assert client.get("/v1/insight-proof/pins").status_code == 404
    _clear_settings()


def test_flag_off_openapi_is_flag_on_minus_the_insight_proof_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_insight_proof_02_route import _refs

    _clear_settings()
    monkeypatch.setenv(_FLAG, "1")
    on = create_app().openapi()
    monkeypatch.delenv(_FLAG)
    _clear_settings()
    off = create_app().openapi()
    _clear_settings()
    new_paths = {p for p in on["paths"] if p.startswith("/v1/insight-proof")}
    assert new_paths == {
        "/v1/insight-proof/status",
        "/v1/insight-proof/run",
        "/v1/insight-proof/pins",
        "/v1/insight-proof/pins/preview",
        "/v1/insight-proof/pins/confirm",
        "/v1/insight-proof/pins/{asset_id}/replay",
        "/v1/insight-proof/pins/{asset_id}/revoke",
    }
    refs = set(_refs({p: on["paths"][p] for p in new_paths}))
    rest = set(_refs({p: v for p, v in on["paths"].items() if p not in new_paths}))
    only_new = {r.rsplit("/", 1)[-1] for r in refs - rest}
    assert only_new == {"InsightProofRunIn", "PinPreviewIn", "PinConfirmIn"}
    stripped = copy.deepcopy(on)
    for p in new_paths:
        del stripped["paths"][p]
    for name in only_new:
        del stripped["components"]["schemas"][name]
    assert json.dumps(stripped, sort_keys=True) == json.dumps(off, sort_keys=True)


def test_flag_off_the_register_functions_touch_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import insight_register as ir

    monkeypatch.delenv(_FLAG, raising=False)
    wh = _seed_sales(tmp_path / "lake_b.duckdb")
    cortex = _Cortex(wh)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=wh)  # type: ignore[arg-type]
    before = _counts(wh)
    calls: list[Any] = []
    assert ir.preview_pin(exe, space_id=None, insight_id="ins_" + "0" * 16)["code"] == "off"
    assert ir.confirm_pin(exe, token="ipcx", actor=_ACTOR,
                          ledger_append=lambda **k: calls.append(k))["code"] == "off"
    assert ir.replay_pin(exe, "pin_x", ledger_append=lambda **k: calls.append(k))["code"] == "off"
    assert ir.revoke_pin(exe, "pin_x", actor=_ACTOR,
                         ledger_append=lambda **k: calls.append(k))["code"] == "off"
    assert ir.list_pinned(exe, space_id=None) == []
    assert cortex.submits == [] and cortex.ledger == [] and calls == []
    assert _counts(wh) == before


# --------------------------------------------------------------------------
# Verify round 1 - governance is re-checked at replay, not only the measure
# --------------------------------------------------------------------------

_PII_TEXT = (
    "A column this approved insight groups by is now marked as personal data, so "
    "nothing of it is shown. Revoke the pin."
)
_AUDIT_TEXT = (
    "The audit record for this insight could not be written, so it was not shown. "
    "No figure was returned."
)


def _pii_item() -> dict[str, Any]:
    return next(i for i in _items(pii_on_name=True) if i["kind"] == "pii")


def _register_row(wh: Path, asset_id: str) -> dict[str, Any]:
    con = dw.connect_serving(wh)
    try:
        cur = con.execute("SELECT * FROM main._proven_insights WHERE asset_id = ?", [asset_id])
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, cur.fetchone(), strict=True))
    finally:
        con.close()


def test_a_pii_mark_after_the_pin_stops_replay_and_withholds_the_listed_headline(
    world: _World,
) -> None:
    asset_id, pre, _ = _pin(world)
    assert world.pins().json()["pins"][0]["headline"] == _B_HEADLINE
    world.onto.approved.append(_pii_item())  # a steward marks Territory.name as PII
    assert world.run().json()["insights"] == []  # a fresh run proves nothing over it
    before = list(world.cortex.submits)
    res = world.replay(asset_id)
    assert res.status_code == 200, res.text
    body = res.json()
    env = body["envelope"]
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["text"] == _PII_TEXT
    assert env["values"] == [] and env["rows"] == [] and env["sql_used"] is None
    assert_envelope_valid(env)
    assert body["verdict"] == "definition_changed"
    assert body["proof"]["code"] == "definition_changed:pii_dimension"
    assert world.cortex.submits == before  # zero submits
    assert world.cortex.events("insight.replayed")[-1].payload["verdict"] == "definition_changed"
    for label in ("North", "South", "86.4"):
        assert label not in res.text
    listed = world.pins()
    pins = listed.json()["pins"]
    assert len(pins) == 1 and pins[0]["asset_id"] == asset_id
    assert pins[0]["headline"] is None and pins[0]["headline_withheld"] == "pii_dimension"
    for label in ("North", "South", "86.4"):
        assert label not in listed.text
    rec = [r for r in world.records() if r.question == "insight:" + pre["proof"]["insight_id"]]
    assert rec[-1].badge == "abstain" and rec[-1].row_count == 0


def test_a_pii_mark_under_a_view_stops_replay_and_withholds_the_listed_headline(
    flag_on: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INS-PROOF-01 verify round 4: the pin gate reads views, as the run gate does."""
    wh = _seed_sales(tmp_path / "lake_view.duckdb")
    _update(wh, "CREATE VIEW Sales.TerritoryV AS SELECT territory_id, name AS region "
                "FROM Sales.Territory")
    w = _build(monkeypatch, wh, approved_store=True,
               grants={**_B_GRANTS, "Sales.TerritoryV": "TRUE"})
    for it in w.onto.approved:
        if it["id"] == "item_obj_territory":
            it["payload"]["relation"] = "Sales.TerritoryV"
            it["payload"]["attributes"] = ["territory_id", "region"]
    headline = "2 of 4 Territory.region values (North, South) carry 86.4 pct of order_amount"
    pre = w.preview(_insight_id(w, headline))
    assert pre.status_code == 200, pre.text
    got = w.confirm(pre.json()["token"])
    assert got.status_code == 200, got.text
    asset_id = str(got.json()["asset_id"])
    assert w.pins().json()["pins"][0]["headline"] == headline

    w.onto.approved.append(_pii_item())  # Sales.Territory.name, which the view renames
    assert w.run().json()["insights"] == []
    before = list(w.cortex.submits)
    res = w.replay(asset_id)
    assert res.status_code == 200, res.text
    env = res.json()["envelope"]
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["text"] == _PII_TEXT
    assert env["values"] == [] and env["rows"] == [] and env["sql_used"] is None
    assert_envelope_valid(env)
    assert res.json()["proof"]["code"] == "definition_changed:pii_dimension"
    assert w.cortex.submits == before
    listed = w.pins()
    pins = listed.json()["pins"]
    assert pins[0]["headline"] is None and pins[0]["headline_withheld"] == "pii_dimension"
    for label in ("North", "South", "86.4"):
        assert label not in res.text
        assert label not in listed.text


_GENERATED_REGION = [
    # (generated columns, how the region values are spelled in the headline)
    # Verify round 6: one level over the marked column.
    ("region VARCHAR GENERATED ALWAYS AS (upper(name)) VIRTUAL", ("NORTH", "SOUTH")),
    # Verify round 7: region reads g1, which reads the marked column.
    ("g1 VARCHAR GENERATED ALWAYS AS (upper(name)) VIRTUAL, "
     "region VARCHAR GENERATED ALWAYS AS (lower(g1)) VIRTUAL", ("north", "south")),
]


@pytest.mark.parametrize(("columns", "labels"), _GENERATED_REGION)
def test_a_pii_mark_under_a_generated_column_stops_replay_and_withholds_the_headline(
    flag_on: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    columns: str, labels: tuple[str, str],
) -> None:
    """INS-PROOF-01 verify rounds 6 and 7: the pin gate reads generated columns,
    chains included, as the run does."""
    wh = _seed_sales(tmp_path / "lake_gen.duckdb")
    _update(wh, "DROP TABLE Sales.Territory")
    _update(wh, "CREATE TABLE Sales.Territory (territory_id INTEGER, name VARCHAR, "
                f"{columns})")
    _update(wh, "INSERT INTO Sales.Territory (territory_id, name) VALUES "
                "(1,'North'),(2,'South'),(3,'East'),(4,'West')")
    w = _build(monkeypatch, wh, approved_store=True, grants=_B_GRANTS)
    for it in w.onto.approved:
        if it["id"] == "item_obj_territory":
            it["payload"]["attributes"] = ["territory_id", "region"]
    headline = (f"2 of 4 Territory.region values ({labels[0]}, {labels[1]}) carry 86.4 pct "
                "of order_amount")
    pre = w.preview(_insight_id(w, headline))
    assert pre.status_code == 200, pre.text
    got = w.confirm(pre.json()["token"])
    assert got.status_code == 200, got.text
    asset_id = str(got.json()["asset_id"])
    assert w.pins().json()["pins"][0]["headline"] == headline

    w.onto.approved.append(_pii_item())  # Sales.Territory.name, which region reads
    assert w.run().json()["insights"] == []
    before = list(w.cortex.submits)
    res = w.replay(asset_id)
    assert res.status_code == 200, res.text
    env = res.json()["envelope"]
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["text"] == _PII_TEXT
    assert env["values"] == [] and env["rows"] == [] and env["sql_used"] is None
    assert_envelope_valid(env)
    assert res.json()["proof"]["code"] == "definition_changed:pii_dimension"
    assert w.cortex.submits == before
    listed = w.pins()
    pins = listed.json()["pins"]
    assert pins[0]["headline"] is None and pins[0]["headline_withheld"] == "pii_dimension"
    for label in (*labels, "86.4"):
        assert label not in res.text
        assert label not in listed.text


@pytest.mark.parametrize("how", ["object_and_link", "link_only", "new_version"])
def test_a_revoked_or_reversioned_dimension_or_link_is_definition_changed(
    world: _World, how: str
) -> None:
    asset_id, _pre, _ = _pin(world)
    terr = next(i for i in world.onto.approved if i["id"] == "item_obj_territory")
    link = next(i for i in world.onto.approved if i["id"] == "item_link_order_territory")
    if how == "new_version":
        terr["version_id"] = "ver_reapproved_0002"
    else:
        moved = [terr, link] if how == "object_and_link" else [link]
        for item in moved:
            world.onto.approved.remove(item)
            world.onto.revoked.append({**item, "status": "revoked"})
    before = list(world.cortex.submits)
    res = world.replay(asset_id)
    assert res.status_code == 200, res.text
    body = res.json()
    env = body["envelope"]
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["values"] == [] and env["rows"] == []
    assert env["text"] == (
        "The approved definition of order_amount changed since this insight was approved; "
        "reconfirm it before it is shown."
    )
    assert "Unchanged since steward approval" not in res.text
    assert_envelope_valid(env)
    assert body["verdict"] == "definition_changed"
    assert body["proof"]["code"] == "definition_changed:dependency"
    assert world.cortex.submits == before


def test_the_pin_records_every_item_its_sql_rests_on(world: _World) -> None:
    asset_id, _pre, _ = _pin(world)
    d = json.loads(_register_row(world.wh, asset_id)["definition_json"])
    assert d["dim_object"] == "Territory" and d["dim_attribute"] == "name"
    assert d["via"] == []
    assert [(x["kind"], x["item_id"]) for x in d["deps"]] == [
        ("link", "item_link_order_territory"),
        ("measure", d["item_id"]),
        ("object", "item_obj_orders"),
        ("object", "item_obj_territory"),
    ]
    assert all(x["version_id"] for x in d["deps"])


def test_a_replay_racing_a_revoke_is_never_served(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import insight_register as ir

    asset_id, _pre, _ = _pin(world)
    real = ir._execute
    started, release = threading.Event(), threading.Event()

    def slow(executor: Any, pin: Any) -> Any:
        started.set()
        release.wait(30)
        return real(executor, pin)

    monkeypatch.setattr(ir, "_execute", slow)
    out: list[Any] = []
    t = threading.Thread(target=lambda: out.append(world.replay(asset_id)))
    t.start()
    assert started.wait(30)
    assert world.revoke(asset_id).status_code == 200
    release.set()
    t.join(60)
    res = out[0]
    assert res.status_code == 404, res.text
    assert res.json()["code"] == "pin_not_found"
    assert "envelope" not in res.json() and "North" not in res.text
    kinds = [r.event_type for r in world.cortex.ledger]
    assert kinds[-1] == "insight.pin_revoked"
    assert "insight.replayed" not in kinds


@pytest.mark.parametrize(
    "spelling",
    [
        "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}",
        "urn:uuid:bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "  bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb  ",
        "",
    ],
)
def test_no_spelling_of_the_shipped_default_can_confirm_a_pin(
    world: _World, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    """The ACT-02 MEDIUM class: a string compare let other UUID spellings through."""
    iid = _insight_id(world)
    pre = world.preview(iid).json()
    monkeypatch.setenv("DMS_ACTOR_USER_ID", spelling)
    _clear_settings()
    got = world.confirm(pre["token"])
    assert got.status_code == 403, got.text
    assert got.json()["code"] == "steward_identity_unset"
    assert "approved_by" not in got.json()
    assert world.cortex.events("insight.pinned") == [] and world.register_rows() == 0
    assert world.pins().json()["pins"] == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (lambda: httpx.ConnectError("connection refused"), "cortex_unavailable"),
        (lambda: RuntimeError("cortex down"), "cortex_submit_failed"),
    ],
)
def test_cortex_down_at_preview_and_confirm_is_503_and_the_token_survives(
    world: _World, error: Any, code: str
) -> None:
    iid = _insight_id(world)
    world.cortex.submit_error = error()
    down = world.preview(iid)
    assert down.status_code == 503, down.text
    assert down.json()["code"] == code
    world.cortex.submit_error = None
    pre = world.preview(iid).json()
    world.cortex.submit_error = error()
    got = world.confirm(pre["token"])
    assert got.status_code == 503, got.text
    assert got.json()["code"] == code
    assert "stale_preview" not in got.text
    assert world.cortex.events("insight.pinned") == [] and world.register_rows() == 0
    world.cortex.submit_error = None
    again = world.confirm(pre["token"])  # Cortex is back: the same token pins
    assert again.status_code == 200, again.text
    assert world.register_rows() == 1
    assert world.confirm(pre["token"]).json()["code"] == "confirm_token_used"


def test_a_replay_whose_audit_row_is_dropped_is_not_served(world: _World) -> None:
    from tests.test_insight_proof_02_route import _RaisingStore

    asset_id, _pre, _ = _pin(world)
    world.client.app.state.ask_audit_store = _RaisingStore()  # type: ignore[attr-defined]
    res = world.replay(asset_id)
    assert res.status_code == 200, res.text
    body = res.json()
    env = body["envelope"]
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["text"] == _AUDIT_TEXT
    assert env["rows"] == [] and env["values"] == [] and env["sql_used"] is None
    assert_envelope_valid(env)
    assert body["verdict"] == "refused" and body["proof"]["code"] == "audit_unrecorded"
    assert world.cortex.events("insight.replayed")
    assert body["orphan_ledger_entry"] == f"led_{world.cortex._n:06d}"
    assert "North" not in res.text and "Unchanged since" not in res.text


def test_a_confirm_whose_audit_row_is_dropped_says_so(world: _World) -> None:
    from tests.test_insight_proof_02_route import _RaisingStore

    iid = _insight_id(world)
    pre = world.preview(iid).json()
    world.client.app.state.ask_audit_store = _RaisingStore()  # type: ignore[attr-defined]
    got = world.confirm(pre["token"])
    assert got.status_code == 200, got.text
    assert got.json()["audit_recorded"] is False
    assert world.register_rows() == 1


def test_the_replay_receipt_names_the_deployment_actor(world: _World) -> None:
    asset_id, _pre, got = _pin(world)
    assert got["audit_recorded"] is True
    world.replay(asset_id)
    assert world.cortex.events("insight.replayed")[-1].actor == _ACTOR


def test_revoke_drops_the_stored_labels(world: _World) -> None:
    asset_id, _pre, _ = _pin(world)
    row = _register_row(world.wh, asset_id)
    assert "North" in row["headline"] and "North" in row["approved_rows_json"]
    assert world.revoke(asset_id).status_code == 200
    row = _register_row(world.wh, asset_id)
    assert row["headline"] == "" and row["approved_rows_json"] == "[]"
    assert row["active_key"] is None and row["revoked_at"] is not None
    assert row["expected_result_hash"] and row["ledger_entry_id"]


def test_one_active_pin_is_a_database_rule_not_only_a_lock(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import insight_register as ir

    first, pre, _ = _pin(world)
    real = ir._active_pin
    token = world.preview(pre["proof"]["insight_id"]).json()["token"]
    # A second process does not share _PIN_LOCK: let its check miss the first pin.
    monkeypatch.setattr(ir, "_active_pin", _MissOnce(real))
    got = world.confirm(token)
    assert got.status_code == 409, got.text
    body = got.json()
    assert body["code"] == "already_pinned" and body["asset_id"] == first
    assert body["orphan_ledger_entry"] == f"led_{world.cortex._n:06d}"
    assert world.register_rows() == 1
    monkeypatch.setattr(ir, "_active_pin", real)
    assert [p["asset_id"] for p in world.pins().json()["pins"]] == [first]
    # A revoke frees the key: the same SQL may be pinned again.
    assert world.revoke(first).status_code == 200
    token = world.preview(pre["proof"]["insight_id"]).json()["token"]
    assert world.confirm(token).status_code == 200
    assert world.register_rows() == 2


class _MissOnce:
    """The already-pinned check misses once (the race), then answers truthfully."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.calls = 0

    def __call__(self, *a: Any, **k: Any) -> Any:
        self.calls += 1
        return None if self.calls == 1 else self.real(*a, **k)


# --------------------------------------------------------------------------
# Verify round 2 - the gate identity, receipt reuse, commit-then-raise
# --------------------------------------------------------------------------

_REPEATED = "ledger_receipt_invalid:repeated"


def _receipt(entry_id: str, seq: int | None) -> LedgerAppendResponse:
    return LedgerAppendResponse(entry_id=entry_id, hash="e" * 64, seq=seq)


def test_every_pin_route_names_the_configured_steward_and_the_space_to_f5(
    world: _World,
) -> None:
    """The gate sees the identity the ledger and the register record, and the Space."""
    asset_id, _pre, got = _pin(world)
    assert world.replay(asset_id).status_code == 200
    assert world.pins().status_code == 200
    assert world.revoke(asset_id).status_code == 200
    pin_calls = [c for c in world.gate.calls if c["action"] != "insight_proof.run"]
    assert [c["action"] for c in pin_calls] == [
        "insight_proof.pin.preview", "insight_proof.pin.confirm", "insight_proof.replay",
        "insight_proof.pins.read", "insight_proof.revoke",
    ]
    for c in pin_calls:
        assert c["actor"] == _ACTOR, c
        assert c["metadata"]["filled_template"] == {"space_id": None}, c
        assert c["metadata"]["task_id"] == c["action"]
    pin_events = [r for r in world.cortex.ledger if r.event_type in (
        "insight.pinned", "insight.replayed", "insight.pin_revoked")]
    assert len(pin_events) == 3 and {r.actor for r in pin_events} == {_ACTOR}
    assert got["approved_by"] == _ACTOR
    assert world.cortex.events("insight.pinned")[0].payload["approved_by"] == _ACTOR


def test_the_real_gate_puts_the_steward_and_the_space_on_the_wire_for_pins(
    flag_on: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_client import gate as gate_mod
    from cortex_client.gate import ComplianceDecision

    wire: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/dms/tasks/gate/check"
        wire.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    real_client = httpx.Client

    def _client(*a: Any, **k: Any) -> httpx.Client:
        return real_client(*a, transport=httpx.MockTransport(_handler), **k)

    class _Real(_GateSpy):
        def __call__(self, **kwargs: Any) -> ComplianceDecision:
            self.calls.append(kwargs)
            return gate_mod.compliance_gate(**kwargs)

    wh = _seed_sales(tmp_path / "lake_b.duckdb")
    w = _build(monkeypatch, wh, approved_store=True, gate=_Real(), grants=_B_GRANTS)
    monkeypatch.setattr(gate_mod.httpx, "Client", _client)
    asset_id, _pre, got = _pin(w)
    assert w.replay(asset_id).status_code == 200
    assert w.pins().status_code == 200
    assert w.revoke(asset_id).status_code == 200
    by_task = {m["task_id"]: m for m in wire}
    for task in ("insight_proof.pin.preview", "insight_proof.pin.confirm",
                 "insight_proof.replay", "insight_proof.pins.read", "insight_proof.revoke"):
        assert by_task[task]["actor"] == _ACTOR, by_task[task]
        assert by_task[task]["filled_template"] == {"space_id": None}, by_task[task]
    assert got["approved_by"] == _ACTOR
    assert w.cortex.events("insight.pinned")[0].actor == _ACTOR


def test_the_space_a_token_or_pin_belongs_to_reaches_f5(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """confirm, replay and revoke read the Space from the token or the pin, and send it."""
    from dms_api import wiring

    asset_id, pre, _ = _pin(world)
    token = world.preview(pre["proof"]["insight_id"]).json()["token"]
    monkeypatch.setattr(wiring, "pin_owner_space", lambda *_a, **_k: "spc_owner")
    world.gate.allowed, world.gate.reason = False, "denied"
    ledger = len(world.cortex.ledger)
    for res in (world.confirm(token), world.replay(asset_id), world.revoke(asset_id)):
        assert res.status_code == 403, res.text
    for c in world.gate.calls[-3:]:
        assert c["actor"] == _ACTOR
        assert c["metadata"]["filled_template"] == {"space_id": "spc_owner"}
        assert c["metadata"]["space_id"] == "spc_owner"
    assert len(world.cortex.ledger) == ledger


def test_one_receipt_cannot_back_a_pin_a_replay_and_a_revoke(world: _World) -> None:
    asset_id, _pre, got = _pin(world)
    pin_entry = got["ledger_entry_id"]
    # (1) Replay gets the pin's own receipt back: not evidence of this append.
    world.cortex.fixed = _receipt(pin_entry, None)
    res = world.replay(asset_id)
    assert res.status_code == 503, res.text
    body = res.json()
    assert body["code"] == _REPEATED and body["ledger_entry_id"] == pin_entry
    assert "envelope" not in body and "North" not in res.text
    assert "Unchanged since" not in res.text and "L0_CERTIFIED" not in res.text
    # (2) Revoke gets the same receipt: refused, and the pin is still active.
    res = world.revoke(asset_id)
    assert res.status_code == 503, res.text
    assert res.json()["code"] == _REPEATED
    assert [p["asset_id"] for p in world.pins().json()["pins"]] == [asset_id]
    assert _register_row(world.wh, asset_id)["revoked_at"] is None
    # (3) A fresh receipt certifies. That replay's receipt cannot back a revoke,
    # nor can a different entry id at the same chain position (seq).
    world.cortex.fixed = None
    ok = world.replay(asset_id)
    assert ok.status_code == 200, ok.text
    replay_entry = ok.json()["envelope"]["audit_id"]
    assert ok.json()["envelope"]["text"].startswith("Unchanged since steward approval")
    assert ok.json()["envelope"]["badge"] == "L0_CERTIFIED"
    world.cortex.fixed = _receipt(replay_entry, None)
    assert world.revoke(asset_id).json()["code"] == _REPEATED
    world.cortex.fixed = _receipt("led_999999", world.cortex._n)
    again = world.replay(asset_id)
    assert again.status_code == 503 and again.json()["code"] == _REPEATED
    assert world.pins().json()["pins"][0]["asset_id"] == asset_id
    # (4) Fresh again: revoke lands with its own entry.
    world.cortex.fixed = None
    done = world.revoke(asset_id)
    assert done.status_code == 200, done.text
    assert done.json()["revoke_ledger_entry_id"] not in (pin_entry, replay_entry)
    assert world.pins().json()["pins"] == []


def test_a_confirm_receipt_already_used_by_another_pin_writes_no_row(world: _World) -> None:
    first, pre, got = _pin(world)
    assert world.revoke(first).status_code == 200
    token = world.preview(pre["proof"]["insight_id"]).json()["token"]
    world.cortex.fixed = _receipt(got["ledger_entry_id"], None)
    res = world.confirm(token)
    assert res.status_code == 503, res.text
    assert res.json()["code"] == _REPEATED
    assert "asset_id" not in res.json()
    assert world.register_rows() == 1
    assert world.pins().json()["pins"] == []


class _CloseRaises:
    """A write connection whose close() commits (the real close) and then raises."""

    def __init__(self, con: Any) -> None:
        self._con = con

    def __getattr__(self, name: str) -> Any:
        return getattr(self._con, name)

    def close(self) -> None:
        self._con.close()
        raise OSError("close failed after commit")


def _close_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    real = dw.connect_file

    def wrapped(path: Path, *, write: bool = True) -> Any:
        con = real(path, write=write)
        return _CloseRaises(con) if write else con

    monkeypatch.setattr(dw, "connect_file", wrapped)


def test_a_pin_write_that_committed_then_raised_reports_the_active_pin(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    iid = _insight_id(world)
    pre = world.preview(iid).json()
    _close_raises(monkeypatch)
    got = world.confirm(pre["token"])
    assert got.status_code == 200, got.text
    body = got.json()
    assert "orphan_ledger_entry" not in body and "code" not in body
    entry = world.cortex.events("insight.pinned")[-1]
    assert body["ledger_entry_id"] == f"led_{world.cortex._n:06d}"
    assert entry.payload["asset_id"] == body["asset_id"]
    pins = world.pins().json()["pins"]
    assert [(p["asset_id"], p["ledger_entry_id"]) for p in pins] == [
        (body["asset_id"], body["ledger_entry_id"])]
    assert body["approved_at"] == pins[0]["approved_at"]
    # Revoke under the same fault: the revoke landed, and the response says so.
    res = world.revoke(body["asset_id"])
    assert res.status_code == 200, res.text
    assert res.json()["revoke_ledger_entry_id"] == f"led_{world.cortex._n:06d}"
    assert "orphan_ledger_entry" not in res.json()
    assert world.pins().json()["pins"] == []
    assert world.revoke(body["asset_id"]).json()["code"] == "not_pinned"


def test_a_replay_receipt_that_committed_then_raised_is_served(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset_id, _pre, _ = _pin(world)
    _close_raises(monkeypatch)
    res = world.replay(asset_id)
    assert res.status_code == 200, res.text
    env = res.json()["envelope"]
    assert env["badge"] == "L0_CERTIFIED" and env["text"].startswith("Unchanged since")
    assert env["audit_id"] == f"led_{world.cortex._n:06d}"


def test_a_revoke_write_that_did_not_land_still_names_the_orphan(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import insight_register as ir

    asset_id, _pre, _ = _pin(world)

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(ir, "_mark_revoked", boom)
    res = world.revoke(asset_id)
    assert res.status_code == 500, res.text
    assert res.json()["code"] == "revoke_commit_failed"
    assert res.json()["orphan_ledger_entry"] == f"led_{world.cortex._n:06d}"
    assert [p["asset_id"] for p in world.pins().json()["pins"]] == [asset_id]


def test_the_shipped_default_actor_cannot_confirm_a_pin(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    iid = _insight_id(world)
    pre = world.preview(iid).json()
    monkeypatch.setenv("DMS_ACTOR_USER_ID", _DEFAULT_ACTOR)
    _clear_settings()
    gate_calls = len(world.gate.calls)
    got = world.confirm(pre["token"])
    assert got.status_code == 403, got.text
    assert got.json()["code"] == "steward_identity_unset"
    assert "No steward is configured" in got.json()["message"]
    assert world.cortex.events("insight.pinned") == [] and world.register_rows() == 0
    assert len(world.gate.calls) == gate_calls
    # The token was not spent: once a steward is configured, it pins.
    monkeypatch.setenv("DMS_ACTOR_USER_ID", _ACTOR)
    _clear_settings()
    ok = world.confirm(pre["token"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["approved_by"] == _ACTOR


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (lambda: httpx.ConnectError("connection refused"), "cortex_unavailable"),
        (lambda: RuntimeError("cortex down"), "cortex_submit_failed"),
    ],
)
def test_cortex_down_mid_replay_is_503_with_no_replay_entry(
    world: _World, error: Any, code: str
) -> None:
    asset_id, _pre, _ = _pin(world)
    world.cortex.submit_error = error()
    res = world.replay(asset_id)
    assert res.status_code == 503, res.text
    assert res.json()["code"] == code
    assert "envelope" not in res.json() and "verdict" not in res.json()
    assert world.cortex.events("insight.replayed") == []
    world.cortex.submit_error = None
    ok = world.replay(asset_id)
    assert ok.status_code == 200 and ok.json()["verdict"] == "unchanged"
    assert len(world.cortex.events("insight.replayed")) == 1
