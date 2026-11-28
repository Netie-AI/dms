"""DMS_SELL_PROFILE is off unless the value is the digit 1.

Off, a pack phrase still serves L1. On, the fixed lanes do not run.
Model SQL is executed when the checks pass. A clarify payload asks back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_core.ask_stream import bind_ask, finish_ask, wait_events
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.sell_profile import enabled

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SKU_Q = "How many SKUs do we have in inventory?"
GOOD_SQL = "SELECT COUNT(*) AS n FROM inventory"
BAD_SQL = "SELECT nope FROM inventory"
DROP_SQL = "DROP TABLE inventory"
_STABLE_DROP = {"as_of", "timings", "sell_ms"}


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
    db = tmp_path / "sell.duckdb"
    ensure_demo_warehouse(db)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    try:
        return exe.live_ask(question, space_id=FINANCE, session_id="ses_sell")
    finally:
        exe.close()


def _model_body(sql: str, **extra: Any) -> dict[str, Any]:
    body = {
        "query_sql": sql,
        "served_model": "ov-routed",
        "served_provider": "openvault",
        "answer": "Here is what the warehouse returned.",
    }
    body.update(extra)
    return body


def _stable(env: dict[str, Any]) -> str:
    kept = {key: value for key, value in env.items() if key not in _STABLE_DROP}
    return json.dumps(kept, sort_keys=True, default=str)


def test_flag_accepts_only_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SELL_PROFILE", raising=False)
    assert enabled() is False
    for token in ("", "0", "true", "yes", "on", "2"):
        monkeypatch.setenv("DMS_SELL_PROFILE", token)
        assert enabled() is False
    monkeypatch.setenv("DMS_SELL_PROFILE", "1")
    assert enabled() is True


def test_flag_off_matches_unset_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SELL_PROFILE", raising=False)
    unset = _ask(tmp_path, _Spoof())
    assert unset["badge"] == "L1_GOVERNED_METRIC"
    assert "sell_profile" not in unset
    for token in ("0", "true", "yes", "on"):
        monkeypatch.setenv("DMS_SELL_PROFILE", token)
        env = _ask(tmp_path, _Spoof())
        assert_envelope_valid(env)
        assert env["badge"] == "L1_GOVERNED_METRIC"
        assert "sell_profile" not in env
        assert _stable(env) == _stable(unset)


def test_profile_serves_checked_model_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SELL_PROFILE", "1")
    token = bind_ask("ask-sell-01")
    cortex = _Spoof(
        bodies=[
            _model_body(
                GOOD_SQL,
                chart_spec={"kind": "bar", "x": "n", "y": ["n"], "series": None},
            )
        ]
    )
    try:
        env = _ask(tmp_path, cortex)
    finally:
        finish_ask(token)
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["sell_profile"] is True
    assert env["sql_used"] == GOOD_SQL
    assert env["rows"]
    assert env["sell_checks"] == ["grant", "read_only", "explain", "shape"]
    assert env["text"].startswith("Here is what the warehouse returned.")
    assert cortex.asks == []
    events, _done = wait_events("ask-sell-01", 0, 0.1)
    stages = [event["stage"] for event in events]
    for name in ("understand", "find", "write", "run", "check", "answer"):
        assert name in stages


def test_clarify_asks_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SELL_PROFILE", "1")
    cortex = _Spoof(
        bodies=[
            {
                "clarify": {
                    "question": "Which warehouse should I use?",
                    "options": ["Warehouse A", "Warehouse B"],
                }
            }
        ]
    )
    env = _ask(tmp_path, cortex)
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["sql_used"] in (None, "")
    assert env["clarify"]["question"] == "Which warehouse should I use?"
    assert [row["label"] for row in env["clarify"]["readings"]] == [
        "Warehouse A",
        "Warehouse B",
    ]
    assert cortex.asks == []


def test_destructive_sql_abstains_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SELL_PROFILE", "1")
    cortex = _Spoof(bodies=[_model_body(DROP_SQL)] * 3)
    env = _ask(tmp_path, cortex)
    assert env["abstained"] is True
    assert env.get("sql_used") in (None, "")
    assert len(cortex.insights) == 1


def test_repair_then_serves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SELL_PROFILE", "1")
    cortex = _Spoof(bodies=[_model_body(BAD_SQL), _model_body(GOOD_SQL)])
    env = _ask(tmp_path, cortex)
    assert env["abstained"] is False
    assert env["sql_used"] == GOOD_SQL
    assert len(cortex.insights) == 2


def test_escalate_after_two_misses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SELL_PROFILE", "1")
    cortex = _Spoof(bodies=[_model_body(BAD_SQL)] * 3)
    env = _ask(tmp_path, cortex)
    assert env["abstained"] is True
    assert len(cortex.insights) == 3
    assert cortex.kwargs[-1].get("route_hint") == "escalate"
