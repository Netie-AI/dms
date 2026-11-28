"""DMS_AI_FLOOR is off unless the value is the digit 1.

Off, a pack phrase still serves L1 and the envelope matches an unset flag.
On, the fixed lanes do not run. The model SQL is executed when grant,
read-only, EXPLAIN, and shape pass. Ungranted and destructive SQL abstain
on the first call. A checker miss is fed back once, then role escalate.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.ai_floor import _context, enabled, offline_label
from dms_executor.demo_warehouse import DEMO_TABLES, SERVING_DIALECT, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import grade52  # noqa: E402

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SKU_Q = "How many SKUs do we have in inventory?"
GOOD_SQL = "SELECT COUNT(*) AS n FROM inventory"
BAD_SQL = "SELECT nope FROM inventory"
DROP_SQL = "DROP TABLE inventory"
UNGRADED_SQL = "SELECT COUNT(*) AS n FROM not_a_real_table"
_STABLE_DROP = {"as_of", "timings", "ai_floor"}


@dataclass
class _Spoof:
    bodies: list[dict[str, Any]] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    kwargs: list[dict[str, Any]] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        self.insights.append(question)
        self.kwargs.append(dict(kwargs))
        if not self.bodies:
            return {"ontology": {"metrics": []}}
        body = self.bodies[min(len(self.insights) - 1, len(self.bodies) - 1)]
        return dict(body)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(
            ok=True,
            status="ok",
            run_id="run_sql",
            output={"rows": [{"n": 4}]},
        )

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        _ = req
        return LedgerAppendResponse(entry_id="led_ok", hash="hash_ok_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Spoof total 999999.",
            badge="certified",
            sql_used="SELECT 999999 AS spoof",
            rows=[{"spoof": 999999}],
            audit_id="aud_spoof",
            route="certified_metric",
        )


def _minter() -> ManifestMinter:
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return minter


def _ask(tmp_path: Path, cortex: _Spoof, question: str = SKU_Q) -> dict[str, Any]:
    db = tmp_path / "ai_floor.duckdb"
    ensure_demo_warehouse(db)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    try:
        return exe.live_ask(question, space_id=FINANCE, session_id="ses_ai_floor")
    finally:
        exe.close()


def _model_body(sql: str) -> dict[str, Any]:
    return {
        "query_sql": sql,
        "served_model": "ov-routed",
        "served_provider": "openvault",
    }


def _stable(env: dict[str, Any]) -> str:
    kept = {key: value for key, value in env.items() if key not in _STABLE_DROP}
    return json.dumps(kept, sort_keys=True, default=str)


def test_flag_accepts_only_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_AI_FLOOR", raising=False)
    assert enabled() is False
    for token in ("", "0", "true", "yes", "on", "2"):
        monkeypatch.setenv("DMS_AI_FLOOR", token)
        assert enabled() is False
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    assert enabled() is True


def test_flag_off_matches_unset_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_AI_FLOOR", raising=False)
    unset = _ask(tmp_path, _Spoof())
    assert unset["badge"] == "L1_GOVERNED_METRIC"
    assert "ai_floor" not in unset
    for token in ("0", "true", "yes", "on"):
        monkeypatch.setenv("DMS_AI_FLOOR", token)
        env = _ask(tmp_path, _Spoof())
        assert_envelope_valid(env)
        assert env["badge"] == "L1_GOVERNED_METRIC"
        assert "ai_floor" not in env
        assert _stable(env) == _stable(unset)


def test_floor_serves_checked_model_sql_in_one_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    cortex = _Spoof(bodies=[_model_body(GOOD_SQL)])
    env = _ask(tmp_path, cortex)
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env["sql_used"] == GOOD_SQL
    assert env["rows"]
    assert env["served_model"] == "ov-routed"
    assert env["plan_origin"] == "generate_sql"
    assert env["ladder_rung"] == "generate_sql"
    assert grade52.serve_path(env, GOOD_SQL) == "ai"
    assert env["ai_floor"]["calls"] == 1
    assert env["ai_floor"]["executes"] == 1
    assert env["ai_floor"]["checks"] == ["grant", "read_only", "explain", "shape"]
    assert len(cortex.insights) == 1
    assert SKU_Q in cortex.insights[0]
    assert "inventory" in cortex.insights[0].lower()
    assert "MEASURES" in cortex.insights[0]
    assert "JOINS" in cortex.insights[0]
    assert cortex.asks == []
    assert "route_hint" not in cortex.kwargs[0]
    assert "model" not in cortex.kwargs[0]


def test_ungranted_sql_abstains_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    cortex = _Spoof(bodies=[_model_body(UNGRADED_SQL)])
    env = _ask(tmp_path, cortex, "List rows from a table this Space does not have")
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["sql_used"] is None
    assert len(cortex.insights) == 1
    assumptions = " ".join(str(item) for item in env.get("assumptions") or [])
    assert "ungranted" in assumptions or "sql_relation_not_granted" in assumptions


def test_destructive_sql_abstains_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    cortex = _Spoof(bodies=[_model_body(DROP_SQL)])
    env = _ask(tmp_path, cortex, "Remove the inventory table")
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert len(cortex.insights) == 1
    assumptions = " ".join(str(item) for item in env.get("assumptions") or [])
    assert "statement_not_allowed" in assumptions or "hostile_sql" in assumptions


def test_explain_miss_is_fed_back_then_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    cortex = _Spoof(bodies=[_model_body(BAD_SQL), _model_body(GOOD_SQL)])
    env = _ask(tmp_path, cortex, "How many inventory rows are there")
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["sql_used"] == GOOD_SQL
    assert env["ai_floor"]["calls"] == 2
    assert env["ai_floor"]["stage"] == "repair"
    assert "sql_feedback" in cortex.kwargs[1]
    assert "route_hint" not in cortex.kwargs[1]
    assert "nope" in str(cortex.kwargs[1]["sql_feedback"])


def test_third_call_sends_escalate_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_core.ask_stream import bind_ask, clear_asks, finish_ask, wait_events

    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    clear_asks()
    token = bind_ask("floorescalate1")
    cortex = _Spoof(
        bodies=[
            {"ontology": {"metrics": []}},
            _model_body(BAD_SQL),
            _model_body(GOOD_SQL),
        ]
    )
    try:
        env = _ask(tmp_path, cortex, "How many inventory rows are there")
    finally:
        finish_ask(token)
    batch, _done = wait_events("floorescalate1", 0, 0.01)
    clear_asks()
    assert "escalate" in [str(event["stage"]) for event in batch]
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["sql_used"] == GOOD_SQL
    assert env["ai_floor"]["calls"] == 3
    assert env["ai_floor"]["stage"] == "escalate"
    assert cortex.kwargs[2].get("route_hint") == "escalate"
    assert "model" not in cortex.kwargs[2]
    routed = json.dumps(cortex.kwargs)
    assert "claude" not in routed.lower()
    assert "gpt" not in routed.lower()


def test_offline_labels_need_no_network(tmp_path: Path) -> None:
    db = tmp_path / "offline.duckdb"
    ensure_demo_warehouse(db)
    grants = set(DEMO_TABLES)
    gold = GOOD_SQL
    assert (
        offline_label(db, gold, GOOD_SQL, grants, dialect=SERVING_DIALECT) == "correct"
    )
    assert (
        offline_label(
            db,
            gold,
            "SELECT COUNT(*) + 1 AS n FROM inventory",
            grants,
            dialect=SERVING_DIALECT,
        )
        == "wrong"
    )
    assert offline_label(db, gold, DROP_SQL, grants, dialect=SERVING_DIALECT) == "abstain"


def test_floor_streams_the_steps_it_actually_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Events are the harness steps. The question text is not a stage."""
    from dms_core.ask_stream import bind_ask, clear_asks, finish_ask, note_fetch, wait_events

    monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    clear_asks()
    ask_id = "floorstream1"
    token = bind_ask(ask_id)
    assert token is not None
    note_fetch("not a table")
    try:
        env = _ask(tmp_path, _Spoof(bodies=[_model_body(GOOD_SQL)]))
    finally:
        finish_ask(token)
    batch, done = wait_events(ask_id, 0, 0.01)
    clear_asks()
    assert done is True
    assert env["sql_used"] == GOOD_SQL
    stages = [str(event["stage"]) for event in batch]
    assert stages[:2] == ["understand", "understand"]
    assert "find" in stages
    assert "write" in stages
    assert "run" in stages
    assert "check" in stages
    assert "escalate" not in stages
    fetched = [str(event.get("table")) for event in batch if event["stage"] == "fetch"]
    assert "inventory" in fetched
    assert set(fetched) <= set(DEMO_TABLES)
    blob = json.dumps(batch)
    assert SKU_Q not in blob
    assert "not a table" not in blob


def test_floor_off_does_not_open_a_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.ask_stream import (
        bind_ask,
        clear_asks,
        finish_ask,
        note_fetch,
        note_stage,
        wait_events,
    )

    monkeypatch.delenv("DMS_AI_FLOOR", raising=False)
    monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    clear_asks()
    token = bind_ask("flooroffstream1")
    assert token is None
    note_stage("understand", "started")
    note_fetch("inventory")
    finish_ask(token)
    batch, done = wait_events("flooroffstream1", 0, 0.01)
    assert batch == []
    assert done is False


def test_default_wiring_seeds_a_warehouse_before_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Executor._warehouse is None until a lane seeds it. The floor must seed too.

    Passing None abstains warehouse_missing before any SQL. This fails if the
    floor is handed self._warehouse raw.
    """
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "live.duckdb"))
    cortex = _Spoof(bodies=[_model_body(GOOD_SQL)])
    exe = Executor(cortex=cortex, minter=_minter())  # type: ignore[arg-type]
    assert exe._warehouse is None
    try:
        env = exe.live_ask(SKU_Q, space_id=FINANCE, session_id="ses_ai_floor")
    finally:
        exe.close()
    assert_envelope_valid(env)
    blob = " ".join(str(item) for item in (env.get("assumptions") or []))
    assert "warehouse_missing" not in blob
    assert env["abstained"] is False
    assert env["sql_used"] == GOOD_SQL
    assert (tmp_path / "live.duckdb").is_file()


def test_schema_prompt_keeps_join_and_measure_columns(tmp_path: Path) -> None:
    """A 480-token pack drops the join path. Ranking and trends then get no SQL."""
    db = tmp_path / "schema.duckdb"
    ensure_demo_warehouse(db)
    grants = set(DEMO_TABLES)
    ranking, _tables = _context(
        "Who are the top suppliers by number of shipments?",
        warehouse=db,
        grantable=grants,
        dialect=SERVING_DIALECT,
        space_id=FINANCE,
    )
    assert "shipments.sku = inventory.sku" in ranking
    assert "inventory.supplier_id = suppliers.supplier_id" in ranking
    assert "cost_myr" in ranking
    monthly, _names = _context(
        "Show monthly transaction totals",
        warehouse=db,
        grantable=grants,
        dialect=SERVING_DIALECT,
        space_id=FINANCE,
    )
    assert "quantity_kg" in monthly
    assert "ts " in monthly or "ts TIMESTAMP" in monthly


def test_grade52_ai_floor_switch_is_main_only() -> None:
    with pytest.raises(SystemExit, match="--ai-floor is the stub switch for --main"):
        grade52.main(["--ai-floor"])
    with pytest.raises(SystemExit, match="does not combine"):
        grade52.main(["--main", "--ai-first", "--ai-floor"])
