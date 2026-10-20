"""LIVE-HARNESS-01: ask clock, demo gate, suggestion budget, armed pacer."""

from __future__ import annotations

from typing import Any

import pytest
from cortex_client.client import SUGGESTION_MAX_TOKENS, CortexClient
from cortex_client.compute import (
    INSIGHTS_ASK_TIMEOUT_SECONDS,
    INSIGHTS_CALL_CAP_DEFAULT,
    begin_answer_model_calls,
    insights_call_cap,
    note_model_call,
)
from dms_executor.demo_pack import HOW_FULL_TRAP_Q
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.lake_registry import (
    DEFAULT_SERVING_LEASE_RESERVE_S,
    SERVING_DEADLINE_CEILING_S,
    default_serving_deadline_s,
    serving_deadline_s,
)
from dms_executor.reconfirm import apply_reconfirm


def test_serving_deadline_default_is_the_ladder_and_does_not_move_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_SERVING_DEADLINE_S", raising=False)
    expect = (
        (INSIGHTS_CALL_CAP_DEFAULT + 1) * INSIGHTS_ASK_TIMEOUT_SECONDS
        + DEFAULT_SERVING_LEASE_RESERVE_S
    )
    assert expect == pytest.approx(24.55)
    assert default_serving_deadline_s() == pytest.approx(expect)
    assert serving_deadline_s() == pytest.approx(expect)
    assert insights_call_cap() == INSIGHTS_CALL_CAP_DEFAULT == 2
    monkeypatch.setenv("DMS_SERVING_DEADLINE_S", "nope")
    assert serving_deadline_s() == pytest.approx(expect)
    monkeypatch.setenv("DMS_SERVING_DEADLINE_S", "0")
    assert serving_deadline_s() == pytest.approx(expect)
    monkeypatch.setenv("DMS_SERVING_DEADLINE_S", "999")
    assert serving_deadline_s() == SERVING_DEADLINE_CEILING_S


def test_product_client_ask_is_not_gated_by_demo_pack(tmp_path: Any) -> None:
    """A CortexClient ask falls through the planted refuse. A fake still stops."""
    from pathlib import Path

    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    db = ensure_demo_warehouse(Path(tmp_path) / "warehouse.duckdb")
    calls: list[str] = []

    class _Fake:
        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            calls.append(question)
            return {"status": "ABSTAIN", "insights_fail": "insights_unarmed"}

        def ask(self, _req: Any) -> Any:
            raise AssertionError("contract ask")

        def submit(self, _req: Any) -> Any:
            raise AssertionError("submit")

        def ledger_append(self, _req: Any) -> Any:
            raise AssertionError("ledger")

    class _Product(CortexClient):
        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            calls.append("product:" + question)
            return {"status": "ABSTAIN", "insights_fail": "insights_unarmed"}

        def ask(self, _req: Any) -> Any:
            raise AssertionError("contract ask")

        def submit(self, _req: Any) -> Any:
            raise AssertionError("submit")

        def ledger_append(self, _req: Any) -> Any:
            raise AssertionError("ledger")

    fake = Executor(cortex=_Fake(), warehouse_path=db)  # type: ignore[arg-type]
    gated = fake.live_ask(HOW_FULL_TRAP_Q, session_id="ses_fake")
    assert gated["abstained"] is True
    assert "uncertified paraphrase" in str(gated.get("assumptions"))
    assert calls == []

    product = _Product("http://127.0.0.1:9")
    try:
        armed = Executor(cortex=product, warehouse_path=db)
        opened = armed.live_ask(HOW_FULL_TRAP_Q, session_id="ses_armed")
    finally:
        product.close()
    assert "uncertified paraphrase" not in str(opened.get("assumptions"))
    assert calls


def test_demo_gate_off_does_not_abstain_the_planted_paraphrase() -> None:
    seen: list[str] = []

    def compute(_catalog: dict[str, Any]) -> dict[str, Any]:
        seen.append("compute")
        return {"status": "ABSTAIN", "insights_fail": "insights_unarmed"}

    gated = maybe_generative_ask(
        HOW_FULL_TRAP_Q,
        compute=compute,
        submit=lambda _sql: None,
        ledger_append=lambda _payload: None,
        dialect="duckdb",
    )
    assert seen == []
    assert gated is not None and gated["abstained"] is True
    assert any("uncertified paraphrase" in str(item) for item in gated["assumptions"])

    opened = maybe_generative_ask(
        HOW_FULL_TRAP_Q,
        compute=compute,
        submit=lambda _sql: None,
        ledger_append=lambda _payload: None,
        dialect="duckdb",
        demo_gate=False,
    )
    assert "uncertified paraphrase" not in str(opened)


def test_suggestion_sends_a_small_max_tokens_and_stops_at_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_post(*_args: Any, **kwargs: Any) -> dict[str, str]:
        seen["max_tokens"] = kwargs.get("max_tokens")
        return {"suggested_question": "how many skus are there"}

    monkeypatch.setattr("cortex_client.client.insights_post", fake_post)
    begin_answer_model_calls()
    client = CortexClient("http://127.0.0.1:9")
    try:
        assert client.complete("closest") == "how many skus are there"
    finally:
        client.close()
    assert seen["max_tokens"] == SUGGESTION_MAX_TOKENS
    assert SUGGESTION_MAX_TOKENS < 600
    begin_answer_model_calls()
    for _ in range(insights_call_cap()):
        note_model_call()
    assert client.complete("again") == ""
    assert seen["max_tokens"] == SUGGESTION_MAX_TOKENS


def _offer(env: dict[str, Any], complete: Any) -> dict[str, Any]:
    return apply_reconfirm(
        env,
        question="total outbound weight",
        space_id=None,
        session_id=None,
        tables=None,
        ask_path=None,
        ontology="outbound_kg",
        schema="shipments",
        complete=complete,
    )


def test_existing_closest_question_is_kept_without_another_call() -> None:
    calls: list[str] = []
    env = {
        "abstained": True,
        "badge": "ABSTAIN",
        "route": "abstain",
        "text": "no figure",
        "assumptions": ["GEN-01: unknown_measure"],
        "reconfirm": {
            "why": "unknown_measure",
            "closest_question": "How many skus are there?",
        },
    }
    out = _offer(env, lambda prompt: calls.append(prompt) or "other question")
    assert calls == []
    assert out["reconfirm"]["closest_question"] == "How many skus are there?"
    assert out["reconfirm"]["suggested_question"] == "How many skus are there?"


def test_suggestion_is_skipped_when_the_generate_cap_is_spent() -> None:
    begin_answer_model_calls()
    for _ in range(insights_call_cap()):
        note_model_call()
    calls: list[str] = []
    env = {
        "abstained": True,
        "badge": "ABSTAIN",
        "route": "abstain",
        "text": "no figure",
        "assumptions": ["GEN-01: unknown_measure"],
    }
    out = _offer(env, lambda prompt: calls.append(prompt) or "How many skus are there?")
    assert calls == []
    assert out["reconfirm"]["suggested_question"] == ""
