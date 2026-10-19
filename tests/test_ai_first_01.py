"""DMS_AI_FIRST stays off unless a test sets it.

Off, a pack phrase still serves L1. On, that SQL is an example in the model
question and the ask does not serve the pack, a compile, or the contract
stub. Pin 279cbd85 is still the scan sha, and example rows are not an
Insights body field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.compute import _insights_body, question_with_examples
from cortex_client.insights import VERIFIED_EXAMPLES_FIELD, without_route_fields
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.ai_first import NO_MODEL_SQL, ai_first_enabled
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import _submit_validated
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import NO_SILENT_PAD, Coverage
from dms_executor.route_stamp import ROUTE_COMPILE

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SKU_Q = "How many SKUs do we have in inventory?"
ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Spoof:
    insights: list[str] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)
    submits: list[str] = field(default_factory=list)

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.insights.append(question)
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        self.submits.append(sql)
        return QueryResult(
            ok=True,
            status="ok",
            run_id="run_sql",
            output={"rows": [{"n": 1}]},
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


def _ask(tmp_path: Path, cortex: _Spoof) -> dict[str, Any]:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    db = tmp_path / "ai_first.duckdb"
    ensure_demo_warehouse(db)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    try:
        return exe.live_ask(SKU_Q, space_id=FINANCE, session_id="ses_ai_first")
    finally:
        exe.close()


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    assert ai_first_enabled() is False


def test_pack_phrase_still_serves_l1_when_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    cortex = _Spoof()
    env = _ask(tmp_path, cortex)
    assert_envelope_valid(env)
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert cortex.insights == []
    assert cortex.asks == []


def test_pack_sql_is_an_example_and_not_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FIRST", "1")
    cortex = _Spoof()
    env = _ask(tmp_path, cortex)
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "999999" not in str(env.get("text") or "")
    assert cortex.asks == []
    assert cortex.insights
    assert "example_sql:" in cortex.insights[0]
    assert "verified_examples:" in cortex.insights[0]
    stamp = env.get("ai_first")
    assert isinstance(stamp, dict)
    assert stamp["examples"] >= 1
    assert "l1" in stamp["sources"]


def test_compile_route_does_not_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_AI_FIRST", "1")
    from dms_executor.ai_first import begin_example_turn, end_example_turn

    token = begin_example_turn()

    def _submit(sql: str) -> None:
        raise AssertionError(sql)

    try:
        env = _submit_validated(
            "SELECT 1 AS n",
            question="how many",
            space_id=FINANCE,
            session_id="ses_ai_first",
            submit=_submit,
            ledger_append=lambda _payload: None,
            notes=(),
            plan_source="ontology_plan",
            coverage=Coverage(
                include=("n",),
                exclude=(NO_SILENT_PAD,),
                unsure=("none",),
            ),
            served_route=ROUTE_COMPILE,
        )
    finally:
        end_example_turn(token)
    assert env["abstained"] is True
    assert any(NO_MODEL_SQL in str(item) for item in env.get("assumptions") or [])


def test_examples_are_not_an_insights_body_field() -> None:
    body = _insights_body(
        "How many SKUs?",
        session_id="ses",
        space_id=FINANCE,
        ontology={
            VERIFIED_EXAMPLES_FIELD: [
                {"question": "sku count", "sql": "SELECT 1 AS n", "revoked": False},
                {"question": "old", "sql": "SELECT 2 AS n", "revoked": True},
            ],
            "model": "should-not-matter",
            "strict": True,
        },
    )
    assert VERIFIED_EXAMPLES_FIELD not in body
    assert "model" not in body
    assert "strict" not in body
    assert "SELECT 1 AS n" in body["question"]
    assert "SELECT 2 AS n" not in body["question"]
    cleaned = without_route_fields({"question": "q", "model": "x", "strict": True})
    assert "model" not in cleaned and "strict" not in cleaned


def test_revoked_example_is_not_prompted() -> None:
    text = question_with_examples(
        "how many",
        [{"question": "q", "sql": "SELECT 9", "revoked": True}],
    )
    assert text == "how many"


def test_pin_279cbd85_constants_unchanged() -> None:
    score = (ROOT / "scripts" / "score_curated.py").read_text(encoding="utf-8")
    pin = (ROOT / "tests" / "test_pin_nomodel_01.py").read_text(encoding="utf-8")
    assert '_CORTEX_SCAN_SHA = "279cbd85"' in score
    assert '_SCAN_SHA = "279cbd85"' in pin
