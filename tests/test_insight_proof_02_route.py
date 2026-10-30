"""INS-PROOF-02 - POST /v1/insight-proof/run, behind ``DMS_INSIGHT_PROOF`` (default off).

The route proves insights through the Executor's Cortex submit path
(``_submit_verified_sql``: serve_gap, a minted manifest, Cortex submit), appends
one Cortex ledger pointer per proven insight, and writes one ask-audit row per
candidate. It is fail-closed on F5: an unreachable gate is a 503, never a
soft proceed, because this route appends to the ledger.

``FakeCortex`` stands in for Cortex: ``submit`` executes on a tmp copy of the
demo warehouse through ``connect_serving`` and records every statement, and
``ledger_append`` returns a fixed receipt and records every request. Nothing
here claims a Cortex run. Assertions are on the rendered text, the returned
rows and the receipt (hard rules 10 and 10a).
"""

from __future__ import annotations

import ast
import copy
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.models import LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_core.control_plane.ask_audit import InMemoryAskAuditStore
from dms_executor import Executor
from dms_executor import demo_warehouse as dw
from dms_executor import insight_proof as ip
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.skills_quarantine import item_content_hash
from fastapi.testclient import TestClient

_FLAG = "DMS_INSIGHT_PROOF"
_KEY = "ov_fakecortex_secret_0123456789"
_ENTRY = "led_000123"
_HEADLINE_PART = "carry 73.2 pct of stock_value_myr"
_ROW0 = {
    "label": "WH-B",
    "value": 15015.0,
    "share_pct": 50.3,
    "cum_share_pct": 50.3,
    "grouped_total": 29840.0,
}
_WAREHOUSE_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"  # settings.dms_actor_user_id default

# The original, before any test spies on it: FakeCortex is the only executor of figures.
_CONNECT_SERVING = dw.connect_serving


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeCortex:
    """Cortex stand-in: submit runs on the tmp warehouse, ledger_append records."""

    base_url = "http://cortex.test"
    api_key = _KEY

    def __init__(self, warehouse: Path) -> None:
        self.warehouse = warehouse
        self.submits: list[str] = []
        self.binds = 0
        self.ledger: list[LedgerAppendRequest] = []
        self.ledger_error: Exception | None = None
        #: Set: every append returns this one receipt (a replayed or stale ledger).
        #: Unset: each append gets its own chain position, led_000123/seq 7 first.
        self.receipt: LedgerAppendResponse | None = None
        #: insight_id -> the receipt its append returned.
        self.receipts: dict[str, LedgerAppendResponse] = {}
        self.submit_error: Exception | None = None

    def submit(self, req: Any) -> QueryResult:
        if (req.plan or {}).get("kind") == "session_bind":
            self.binds += 1
            return QueryResult(ok=True, status="bound", run_id="run-bind")
        sql = str(req.body["sql"])
        self.submits.append(sql)
        if self.submit_error is not None:
            raise self.submit_error
        con = _CONNECT_SERVING(self.warehouse)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        # What crosses HTTP is JSON.
        rows = json.loads(json.dumps(
            rows, default=lambda o: float(o) if isinstance(o, Decimal) else str(o)
        ))
        return QueryResult(ok=True, status="ok", run_id="run-sql", output={"rows": rows})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.ledger.append(req)
        if self.ledger_error is not None:
            raise self.ledger_error
        n = len(self.ledger) - 1
        got = self.receipt or LedgerAppendResponse(
            entry_id=f"led_{123 + n:06d}", hash=f"{n + 1:064x}", seq=7 + n
        )
        self.receipts[str(req.payload.get("insight_id"))] = got
        return got


class _RaisingStore(InMemoryAskAuditStore):
    def record(self, rec: Any) -> None:  # noqa: ARG002
        raise RuntimeError("audit database down")


def _minter() -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-30T00:00:00+00:00",
            expires_at="2026-10-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return m


class _GateSpy:
    def __init__(self, allowed: bool = True, reason: str = "ok") -> None:
        self.allowed = allowed
        self.reason = reason
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> ComplianceDecision:
        self.calls.append(kwargs)
        return ComplianceDecision(
            allowed=self.allowed, reason=self.reason, action=str(kwargs.get("action"))
        )


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_wh(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return dw.ensure_demo_warehouse(tmp_path_factory.mktemp("insproof02") / "demo.duckdb")


def _clear_settings() -> None:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _clear_settings()
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    _clear_settings()
    yield
    _clear_settings()


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _clear_settings()
    monkeypatch.delenv(_FLAG, raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    _clear_settings()
    yield
    _clear_settings()


class _World:
    def __init__(
        self,
        client: TestClient,
        cortex: FakeCortex,
        gate: _GateSpy,
        audit: InMemoryAskAuditStore,
    ) -> None:
        self.client = client
        self.cortex = cortex
        self.gate = gate
        self.audit = audit

    def run(self, **body: Any) -> Any:
        return self.client.post("/v1/insight-proof/run", json=body)

    def records(self) -> list[Any]:
        return self.audit.list_between(since=None, until=None, limit=10_000)


def _world(
    demo_wh: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    gate: _GateSpy | None = None,
    audit: InMemoryAskAuditStore | None = None,
) -> _World:
    cortex = FakeCortex(demo_wh)
    spy = gate or _GateSpy()
    monkeypatch.setattr("dms_api.routes.insight_proof.compliance_gate", spy)
    monkeypatch.setattr(
        ip.bronze, "lookup_ingest_watermarks", lambda *, path=None: {}, raising=True
    )
    app = create_app()
    app.state.cortex = cortex
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=_minter(),
        warehouse_path=demo_wh,
    )
    store = audit or InMemoryAskAuditStore()
    app.state.ask_audit_store = store
    return _World(TestClient(app), cortex, spy, store)


@pytest.fixture
def world(flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    return _world(demo_wh, monkeypatch)


def _proven_by_text(body: dict[str, Any], part: str) -> dict[str, Any]:
    hits = [i for i in body["insights"] if part in i["envelope"]["text"]]
    assert len(hits) == 1, [i["envelope"]["text"] for i in body["insights"]]
    return hits[0]


def _leaves(obj: Any) -> Iterator[Any]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _leaves(v)
    else:
        yield obj


# --------------------------------------------------------------------------
# 1. the served insight
# --------------------------------------------------------------------------


def test_flag_on_run_serves_the_proven_insight_with_its_ledger_receipt(world: _World) -> None:
    res = world.run()
    assert res.status_code == 200, res.text
    body = res.json()
    got = _proven_by_text(body, _HEADLINE_PART)
    env = got["envelope"]
    assert env["text"] == (
        "2 of 5 location_code values (WH-B, WH-E) carry 73.2 pct of stock_value_myr"
    )
    assert env["rows"][0] == _ROW0
    assert [r["label"] for r in env["rows"]] == ["WH-B", "WH-E", "WH-A", "WH-C", "WH-D"]
    assert env["audit_id"] == world.cortex.receipts[got["proof"]["insight_id"]].entry_id
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"  # demo ontology: never above L2
    # No Python-derived figure: whatever values[] carries is a returned cell.
    cells = {float(v) for r in env["rows"] for k, v in r.items() if k != "label"}
    assert all(float(v["value"]) in cells for v in env["values"])
    for item in body["insights"]:
        assert_envelope_valid(item["envelope"])
        iid = item["proof"]["insight_id"]
        assert item["envelope"]["audit_id"] == world.cortex.receipts[iid].entry_id
    # One chain position per served insight: no two share an entry.
    audit_ids = [i["envelope"]["audit_id"] for i in body["insights"]]
    assert len(set(audit_ids)) == len(audit_ids) == len(world.cortex.ledger)
    assert body["unconfirmed_ledger_appends"] == 0
    assert body["counts"] == {"proven": len(body["insights"]), "refused": len(body["refused"])}
    assert body["counts"]["proven"] >= 1
    assert body["orphan_ledger_entries"] == []
    assert body["scope"] == ip.PROOF_SCOPE
    assert "complete" not in body
    for r in body["refused"]:
        assert set(r) == {"candidate", "code", "text"}
        assert r["text"] == ip.REFUSALS[r["code"].split(":", 1)[0]]


# --------------------------------------------------------------------------
# 2. two Cortex executions, no local figure
# --------------------------------------------------------------------------


def test_every_figure_comes_from_two_cortex_submits_and_none_runs_locally(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local: list[str] = []

    class _SpyCon:
        def __init__(self, con: Any) -> None:
            self._con = con

        def execute(self, sql: str, *a: Any, **k: Any) -> Any:
            local.append(str(sql))
            return self._con.execute(sql, *a, **k)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._con, name)

    monkeypatch.setattr(dw, "connect_serving", lambda *a, **k: _SpyCon(_CONNECT_SERVING(*a, **k)))
    w = _world(demo_wh, monkeypatch)
    body = w.run().json()
    assert body["insights"]
    for item in body["insights"]:
        env, proof = item["envelope"], item["proof"]
        execs = proof["executions"]
        assert len(execs) == 2
        assert execs[0]["result_hash"] == execs[1]["result_hash"] == proof["result_hash"]
        assert all(e["engine"] == "submit" for e in execs)
        assert w.cortex.submits.count(env["sql_used"]) == 2
        assert proof["raw_total_sql"] in w.cortex.submits
        led = w.cortex.receipts[proof["insight_id"]]
        assert proof["ledger"] == {
            "event_type": "insight.proven", "entry_id": led.entry_id, "seq": led.seq
        }
        assert env["audit_id"] == led.entry_id
        ip.assert_proof_shape(proof)
    # Dimension discovery reads the local catalog; no figure statement runs locally.
    assert not [s for s in local if s.lstrip().upper().startswith("WITH G AS")]
    assert not [s for s in local if "AS raw_total" in s or "negative_parts" in s]
    assert w.cortex.binds >= 1


# --------------------------------------------------------------------------
# 3. the ledger payload is pointers only
# --------------------------------------------------------------------------


def test_ledger_payload_is_pointers_only(world: _World) -> None:
    body = world.run().json()
    by_id = {req.payload["insight_id"]: req for req in world.cortex.ledger}
    assert len(world.cortex.ledger) == len(body["insights"])
    for item in body["insights"]:
        env, proof = item["envelope"], item["proof"]
        req = by_id[proof["insight_id"]]
        payload = req.payload
        assert req.event_type == "insight.proven"
        assert req.actor == _ACTOR  # DR-0004 Option A: the configured deployment actor
        assert "session_id" not in payload
        assert payload["session_hash"] == item_content_hash("insights")
        assert payload["result_hash"] == proof["result_hash"]
        assert payload["sql_hash"] == item_content_hash(env["sql_used"])
        assert payload["raw_total_sql_hash"] == proof["raw_total_sql_hash"]
        assert payload["conserves"] is True
        assert payload["definition_source"] == "demo_ontology"
        assert "sql" not in payload and "rows" not in payload
        blob = json.dumps(payload)
        assert env["sql_used"] not in blob
        cells = [v for v in _leaves(env["rows"])]
        leaves = list(_leaves(payload))
        for cell in cells:
            if isinstance(cell, str):
                assert cell not in blob, f"row label {cell!r} reached the ledger"
            elif isinstance(cell, (int, float)) and not isinstance(cell, bool):
                assert not any(
                    isinstance(x, (int, float)) and not isinstance(x, bool)
                    and abs(float(x) - float(cell)) < 1e-9
                    for x in leaves
                ), f"row cell {cell!r} reached the ledger"


# --------------------------------------------------------------------------
# 4. one audit row per candidate
# --------------------------------------------------------------------------


def test_one_audit_row_per_candidate_and_the_proven_row_points_at_the_entry(
    world: _World,
) -> None:
    body = world.run().json()
    recs = world.records()
    assert len(recs) == body["counts"]["proven"] + body["counts"]["refused"]
    got = _proven_by_text(body, _HEADLINE_PART)
    env, proof = got["envelope"], got["proof"]
    rec = next(r for r in recs if r.question == "insight:" + proof["insight_id"])
    assert rec.executed_sql == env["sql_used"]
    assert {"inventory"} <= {t.casefold() for t in rec.tables_read}
    assert rec.row_count == len(env["rows"])
    led = world.cortex.receipts[proof["insight_id"]]
    assert rec.cortex_entry_id == led.entry_id == env["audit_id"]
    assert rec.ledger_seq == led.seq
    assert rec.badge == "validated"
    supplier = next(i for i in body["insights"] if "supplier.country" in i["envelope"]["text"])
    srec = next(r for r in recs if r.question == "insight:" + supplier["proof"]["insight_id"])
    assert {"inventory", "suppliers"} <= {t.casefold() for t in srec.tables_read}
    refused = [r for r in recs if r.badge != "validated"]
    assert len(refused) == body["counts"]["refused"]
    assert all(r.badge == "abstain" and r.cortex_entry_id == "" for r in refused)


# --------------------------------------------------------------------------
# 5. ledger down / receipt invalid
# --------------------------------------------------------------------------


def test_ledger_down_refuses_every_proven_candidate(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    up = _world(demo_wh, monkeypatch).run().json()
    w = _world(demo_wh, monkeypatch)
    w.cortex.ledger_error = RuntimeError("ledger down")
    res = w.run()
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["insights"] == []
    assert body["counts"]["proven"] == 0
    failed = [r for r in body["refused"] if r["code"] == "ledger_append_failed"]
    assert len(failed) == up["counts"]["proven"] >= 1
    # Every other refusal is the one the ledger-up run made: nothing else changed.
    others = sorted((r["candidate"], r["code"]) for r in body["refused"]
                    if r["code"] != "ledger_append_failed")
    assert others == sorted((r["candidate"], r["code"]) for r in up["refused"])
    for r in failed:
        assert r["text"] == ip.REFUSALS["ledger_append_failed"]
    assert body["unconfirmed_ledger_appends"] == len(failed)
    assert not any(
        str(e.get("badge")) in {"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED"}
        for e in _all_envelopes(res.text)
    )
    recs = w.records()
    assert len(recs) == len(body["refused"])
    assert all(r.badge == "abstain" and r.cortex_entry_id == "" for r in recs)
    assert body["orphan_ledger_entries"] == []


def _all_envelopes(text: str) -> list[dict[str, Any]]:
    body = json.loads(text)
    return [i["envelope"] for i in body["insights"]]


def test_a_receipt_whose_hash_is_its_entry_id_is_refused(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _world(demo_wh, monkeypatch)
    w.cortex.receipt = LedgerAppendResponse(entry_id=_ENTRY, hash=_ENTRY, seq=7)
    body = w.run().json()
    assert body["insights"] == []
    codes = {r["code"] for r in body["refused"]}
    assert "ledger_receipt_invalid" in codes
    assert "ledger_append_failed" not in codes
    assert all(r.badge == "abstain" for r in w.records())


# --------------------------------------------------------------------------
# 5b. verify round 2: receipts across a run, the session id, the actor
# --------------------------------------------------------------------------


def test_a_replayed_receipt_is_refused_for_every_insight_it_names(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One valid-looking receipt for N different appends is not N chain positions."""
    up = _world(demo_wh, monkeypatch).run().json()
    assert up["counts"]["proven"] >= 2
    w = _world(demo_wh, monkeypatch)
    w.cortex.receipt = LedgerAppendResponse(entry_id=_ENTRY, hash="a" * 64, seq=7)
    res = w.run()
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["insights"] == []
    assert body["counts"]["proven"] == 0
    repeated = [r for r in body["refused"] if r["code"] == "ledger_receipt_invalid:repeated"]
    assert len(repeated) == len(w.cortex.ledger) == up["counts"]["proven"]
    for r in repeated:
        assert r["text"] == ip.REFUSALS["ledger_receipt_invalid"]
    assert _HEADLINE_PART not in res.text and "73.2" not in res.text
    for item in up["insights"]:
        assert item["envelope"]["text"] not in res.text
    assert body["unconfirmed_ledger_appends"] == len(repeated)
    assert body["orphan_ledger_entries"] == []
    recs = w.records()
    assert len(recs) == len(body["refused"])
    assert all(r.badge == "abstain" and r.cortex_entry_id == "" for r in recs)


def test_a_repeated_entry_id_refuses_both_insights_and_serves_the_rest(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _world(demo_wh, monkeypatch)
    fresh = w.cortex.ledger_append

    def _second_repeats_first(req: LedgerAppendRequest) -> LedgerAppendResponse:
        got = fresh(req)
        if len(w.cortex.ledger) == 2:
            got = LedgerAppendResponse(entry_id=_ENTRY, hash="f" * 64, seq=8)
            w.cortex.receipts[str(req.payload["insight_id"])] = got
        return got

    w.cortex.ledger_append = _second_repeats_first  # type: ignore[method-assign]
    body = w.run().json()
    first_two = {str(r.payload["insight_id"]) for r in w.cortex.ledger[:2]}
    served = {i["proof"]["insight_id"] for i in body["insights"]}
    assert not served & first_two
    assert len(served) == len(w.cortex.ledger) - 2 >= 1
    repeated = [r for r in body["refused"] if r["code"] == "ledger_receipt_invalid:repeated"]
    assert len(repeated) == 2
    audit_ids = [i["envelope"]["audit_id"] for i in body["insights"]]
    assert _ENTRY not in audit_ids and len(set(audit_ids)) == len(audit_ids)
    for item in body["insights"]:
        assert_envelope_valid(item["envelope"])
    recs = {r.question: r for r in w.records()}
    for iid in first_two:
        assert recs["insight:" + iid].badge == "abstain"
        assert recs["insight:" + iid].cortex_entry_id == ""


def test_a_repeated_seq_under_distinct_entry_ids_is_refused(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _world(demo_wh, monkeypatch)
    fresh = w.cortex.ledger_append

    def _same_seq(req: LedgerAppendRequest) -> LedgerAppendResponse:
        got = fresh(req)
        return LedgerAppendResponse(entry_id=got.entry_id, hash=got.hash, seq=7)

    w.cortex.ledger_append = _same_seq  # type: ignore[method-assign]
    body = w.run().json()
    assert body["insights"] == []
    codes = [r["code"] for r in body["refused"]]
    assert codes.count("ledger_receipt_invalid:repeated") == len(w.cortex.ledger) >= 2


def test_a_lost_append_response_is_not_reported_as_unwritten(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cortex commits, the response is lost: DMS says it does not know, and counts it."""
    w = _world(demo_wh, monkeypatch)
    w.cortex.ledger_error = TimeoutError("read timeout after the append committed")
    body = w.run().json()
    assert body["insights"] == []
    failed = [r for r in body["refused"] if r["code"] == "ledger_append_failed"]
    assert len(failed) == len(w.cortex.ledger) >= 1
    for r in failed:
        assert r["text"] == (
            "The ledger did not confirm the proof was written, so the insight was not shown. "
            "No figure was returned."
        )
        assert "could not be written" not in r["text"]
    assert body["unconfirmed_ledger_appends"] == len(w.cortex.ledger)


def test_session_id_free_text_is_422_and_never_reaches_the_ledger(world: _World) -> None:
    res = world.run(session_id="jane.doe@example.com ov_live_secret_abcdef0123456789")
    assert res.status_code == 422
    assert world.gate.calls == [] and world.cortex.ledger == [] and world.cortex.submits == []
    assert world.records() == []


def test_the_ledger_carries_a_hash_of_the_session_id_never_the_string(world: _World) -> None:
    secretish = "ov_live_secret_abcdef0123456789"
    body = world.run(session_id=secretish).json()
    assert body["insights"]
    assert len(world.cortex.ledger) == len(body["insights"])
    for req in world.cortex.ledger:
        assert secretish not in json.dumps(req.payload)
        assert req.payload["session_hash"] == item_content_hash(secretish)
    # The customer still reads the proven headline.
    got = _proven_by_text(body, _HEADLINE_PART)
    assert got["envelope"]["text"] == (
        "2 of 5 location_code values (WH-B, WH-E) carry 73.2 pct of stock_value_myr"
    )


def test_gate_ledger_and_audit_name_the_one_configured_actor(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steward = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    monkeypatch.setenv("DMS_ACTOR_USER_ID", steward)
    _clear_settings()
    from dms_api.settings import get_settings

    assert get_settings().dms_actor_user_id == steward
    w = _world(demo_wh, monkeypatch)
    res = w.run(space_id=_WAREHOUSE_OPS)
    assert res.status_code == 200, res.text
    assert [c["actor"] for c in w.gate.calls] == [steward]
    assert w.gate.calls[0]["metadata"]["filled_template"] == {"space_id": _WAREHOUSE_OPS}
    assert w.cortex.ledger, res.text
    assert {req.actor for req in w.cortex.ledger} == {steward}
    assert w.records() and {r.actor for r in w.records()} == {steward}


def test_the_real_gate_puts_the_space_and_the_actor_on_the_wire(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``compliance_gate`` forwards only filled_template and actor: both must carry them."""
    import httpx
    from cortex_client import gate as gate_mod

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

    w = _world(demo_wh, monkeypatch, gate=_Real())
    # After create_app: only the gate's own client is routed to the mock.
    monkeypatch.setattr(gate_mod.httpx, "Client", _client)
    res = w.run()
    assert res.status_code == 200, res.text
    assert len(wire) == 1
    assert wire[0]["task_id"] == "insight_proof.run"
    assert wire[0]["filled_template"] == {"space_id": None}
    assert wire[0]["actor"] == _ACTOR
    assert _proven_by_text(res.json(), _HEADLINE_PART)


# --------------------------------------------------------------------------
# 6. audit write fails
# --------------------------------------------------------------------------


def test_an_unrecorded_audit_row_demotes_and_names_the_orphan_entry(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _RaisingStore()
    w = _world(demo_wh, monkeypatch, audit=store)
    res = w.run()
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["insights"] == []
    demoted = [r for r in body["refused"] if r["code"] == "audit_unrecorded"]
    assert demoted and len(demoted) == len(w.cortex.ledger)
    assert body["orphan_ledger_entries"] == [r.entry_id for r in w.cortex.receipts.values()]
    assert store.dropped >= len(body["refused"])
    # The demoted envelopes are abstains; the route's JSON carries only text for them.
    assert all(r["text"] == ip.REFUSALS["audit_unrecorded"] for r in demoted)


def test_demote_insight_builds_an_abstain_through_the_builder(world: _World) -> None:
    from dms_api import wiring

    world.run()
    ask = world.client.app.state.ask_service  # type: ignore[attr-defined]
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(_FLAG, "1")
        run = wiring.prove_insights(
            ask, world.cortex, space_id=None, session_id="insights_demote", top=6
        )
    p = run.proven[0]
    before = copy.deepcopy(p.envelope)
    entry = wiring.demote_insight(p, "audit_unrecorded")
    env = entry["envelope"]
    assert p.envelope == before  # the proven envelope is not mutated
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["rows"] == [] and env["values"] == [] and env["sql_used"] is None
    assert env["text"] == ip.REFUSALS["audit_unrecorded"]
    assert entry["code"] == "audit_unrecorded" and entry["insight_id"] == p.insight_id
    assert_envelope_valid(env)


# --------------------------------------------------------------------------
# 7. the gate fails closed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["gate_unavailable", "gate_task_unknown"])
def test_no_gate_decision_is_503_with_nothing_run(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    w = _world(demo_wh, monkeypatch, gate=_GateSpy(allowed=False, reason=reason))
    res = w.run()
    assert res.status_code == 503
    assert res.json()["code"] == "cortex_unavailable"
    assert res.json()["values"] == []
    assert w.cortex.submits == [] and w.cortex.ledger == [] and w.cortex.binds == 0
    assert w.records() == []
    assert w.gate.calls[0]["action"] == "insight_proof.run"
    assert w.gate.calls[0]["metadata"] == {
        "task_id": "insight_proof.run", "space_id": None, "filled_template": {"space_id": None}
    }
    assert w.gate.calls[0]["actor"] == _ACTOR


def test_a_denied_gate_is_403_with_nothing_run(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _world(demo_wh, monkeypatch, gate=_GateSpy(allowed=False, reason="denied"))
    res = w.run()
    assert res.status_code == 403
    assert res.json() == {"detail": "denied"}
    assert w.cortex.submits == [] and w.cortex.ledger == [] and w.cortex.binds == 0
    assert w.records() == []


def test_contrast_post_insights_still_proceeds_on_gate_unavailable(
    flag_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged on main: the read route soft-proceeds; this one does not."""
    from tests.test_insights_host import FakeInsights

    def _down(**kwargs: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=False, reason="gate_unavailable", action="insights.ask")

    monkeypatch.setattr("dms_api.routes.insights.compliance_gate", _down)
    app = create_app()
    fake = FakeInsights()
    app.state.cortex = fake
    res = TestClient(app).post("/v1/insights", json={"intent": "how many skus"})
    assert res.status_code == 200, res.text
    assert len(fake.asks) == 1


# --------------------------------------------------------------------------
# 8. Spaces and grants
# --------------------------------------------------------------------------


def test_unknown_space_is_404_and_the_gate_is_never_called(world: _World) -> None:
    res = world.run(space_id="no-such-space")
    assert res.status_code == 404
    assert res.json() == {"detail": "space_not_found"}
    assert world.gate.calls == []
    assert world.cortex.submits == [] and world.cortex.ledger == []


def test_a_space_without_the_dimension_grant_never_reads_or_names_it(world: _World) -> None:
    res = world.run(space_id=_WAREHOUSE_OPS)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["insights"], "Warehouse Ops grants inventory and locations"
    for item in body["insights"]:
        assert "supplier" not in item["envelope"]["text"].casefold()
        assert_envelope_valid(item["envelope"])
    codes = {r["candidate"]: r["code"] for r in body["refused"]}
    assert codes["stock_value_myr by supplier"] == "ungranted"
    assert not [s for s in world.cortex.submits if "suppliers" in s.casefold()]
    assert world.gate.calls[0]["metadata"]["space_id"] == _WAREHOUSE_OPS


def test_a_row_policy_relation_is_refused_without_a_submit(
    flag_on: None, demo_wh: Path
) -> None:
    """A predicate other than TRUE: the proof hash could not describe what Cortex filters."""
    cortex = FakeCortex(demo_wh)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=demo_wh)  # type: ignore[arg-type]
    real = exe.demo_acl

    def _acl(**kw: Any) -> SessionAcl:
        acl = real(**kw)
        preds = dict(acl.row_predicates)
        preds["suppliers"] = "country = 'MY'"
        return SessionAcl(**{**acl.__dict__, "row_predicates": preds})

    exe.demo_acl = _acl  # type: ignore[method-assign]
    run = ip.prove_for_space(
        exe, space_id=None, session_id="insights_rp", top=6,
        ledger_append=lambda **_k: cortex.ledger_append(LedgerAppendRequest(**_k)),
        watermarks=lambda *, path=None: {},
    )
    codes = {r["candidate"]: r["code"] for r in run.refused}
    assert codes["stock_value_myr by supplier"] == "ungranted:row_policy"
    assert not [p for p in run.proven if "supplier" in p.headline.casefold()]
    assert not [s for s in cortex.submits if "suppliers" in s.casefold()]
    assert run.proven, "the relations without a policy still prove"


# --------------------------------------------------------------------------
# 9. Cortex absent
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fallback", ["0", "1"])
def test_cortex_absent_is_503_whatever_the_demo_fallback_says(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch, fallback: str
) -> None:
    monkeypatch.setenv("DMS_DEMO_FALLBACK", fallback)
    _clear_settings()
    w = _world(demo_wh, monkeypatch)
    w.client.app.state.cortex = None  # type: ignore[attr-defined]
    res = w.run()
    assert res.status_code == 503
    assert res.json()["code"] == "cortex_unavailable"
    assert res.json()["values"] == []
    assert w.gate.calls == []
    assert w.cortex.submits == [] and w.records() == []


# --------------------------------------------------------------------------
# 10. secrets
# --------------------------------------------------------------------------


def test_the_cortex_key_never_reaches_the_response(
    flag_on: None, demo_wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _world(demo_wh, monkeypatch)
    ok = w.run()
    assert ok.status_code == 200 and _KEY not in ok.text
    w2 = _world(demo_wh, monkeypatch)
    w2.cortex.submit_error = RuntimeError(f"engine rejected key {_KEY}")
    bad = w2.run()
    assert bad.status_code == 200, bad.text
    assert _KEY not in bad.text
    assert bad.json()["insights"] == []


# --------------------------------------------------------------------------
# 11. flag off
# --------------------------------------------------------------------------


def test_flag_off_the_router_is_not_mounted(flag_off: None) -> None:
    client = TestClient(create_app())
    assert client.get("/v1/insight-proof/status").status_code == 404
    res = client.post("/v1/insight-proof/run", json={})
    assert res.status_code == 404
    assert res.json() == {"detail": "Not Found"}


def test_flag_on_status_says_enabled(flag_on: None) -> None:
    assert TestClient(create_app()).get("/v1/insight-proof/status").json() == {"enabled": True}


def test_flag_off_openapi_is_flag_on_minus_the_new_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings()
    monkeypatch.setenv(_FLAG, "1")
    on = create_app().openapi()
    monkeypatch.delenv(_FLAG)
    _clear_settings()
    off = create_app().openapi()
    _clear_settings()

    new_paths = {p for p in on["paths"] if p.startswith("/v1/insight-proof")}
    # INS-PROOF-03 adds the steward pin paths under the same flag.
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


def _refs(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "$ref" and isinstance(v, str):
                yield v
            else:
                yield from _refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _refs(v)


def test_post_v1_insights_is_byte_identical_with_the_flag_on_and_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_insights_host import FakeInsights, _allow

    monkeypatch.setattr("dms_api.routes.insights.compliance_gate", _allow)

    def _post() -> bytes:
        _clear_settings()
        app = create_app()
        app.state.cortex = FakeInsights()
        return TestClient(app).post("/v1/insights", json={"intent": "how many skus"}).content

    monkeypatch.setenv(_FLAG, "1")
    on = _post()
    monkeypatch.delenv(_FLAG)
    off = _post()
    _clear_settings()
    assert on == off


def test_flag_off_prove_for_space_does_nothing(flag_off: None, demo_wh: Path) -> None:
    cortex = FakeCortex(demo_wh)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=demo_wh)  # type: ignore[arg-type]
    calls: list[Any] = []
    run = ip.prove_for_space(exe, space_id=None, session_id="s", top=6,
                             ledger_append=lambda **k: calls.append(k))
    assert run.status == "off"
    assert cortex.submits == [] and cortex.binds == 0 and calls == []


# --------------------------------------------------------------------------
# 12. boundaries
# --------------------------------------------------------------------------


def test_the_route_module_never_imports_the_executor() -> None:
    import dms_api.routes.insight_proof as mod

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert not [n for n in names if n.startswith("dms_executor")]
    assert "dms_api.wiring" in names or "dms_api" in names


def test_the_body_forbids_extra_keys_and_bounds_top(world: _World) -> None:
    assert world.run(sql="SELECT 1").status_code == 422
    assert world.run(top=0).status_code == 422
    assert world.run(top=21).status_code == 422
    assert world.gate.calls == []
