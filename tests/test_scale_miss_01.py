"""SCALE-MISS-01. Flag default off. On, generation starts early.

No new question-text rule. The shape judge and the readings come back from
the model. A missing verdict keeps the compile SQL.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from dms_executor.scale_miss import (
    begin,
    bind_turn,
    calls_used,
    compile_override,
    enabled,
    explain_askback,
    ladder_reserves,
    remember_partial,
    reset_turn,
    stop_envelope,
)


def _compute_log(calls: list[dict[str, Any]]):
    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        kind = ctx.get("sql_vote_kind")
        if kind == "shape":
            return {"verdict": "misfit"}
        if kind == "readings":
            return {"readings": ["Lots on hand", "Lots by site", "Lots received"]}
        if kind == "plan":
            return {
                "query_plan": {
                    "tables": ["lot"],
                    "joins": [],
                    "measure": "quantity",
                    "filters": [],
                }
            }
        if kind == "plan_solve":
            return {"query_sql": "SELECT 1 AS quantity"}
        if ctx.get("route_hint") == "escalate":
            return {"query_sql": "SELECT 3 AS quantity"}
        if kind == "repair":
            return {"query_sql": "SELECT 2 AS quantity"}
        return {"query_sql": "SELECT 1 AS quantity"}

    return compute


def test_flag_defaults_off_and_only_the_digit_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SCALE_MISS", raising=False)
    assert enabled() is False
    assert ladder_reserves() is True
    monkeypatch.setenv("DMS_SCALE_MISS", "true")
    assert enabled() is False
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    assert enabled() is True
    assert ladder_reserves() is False


def test_vague_askback_has_two_or_three_readings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    calls: list[dict[str, Any]] = []
    token = bind_turn(None)
    try:
        env = begin(
            question="is this good",
            compute=_compute_log(calls),
            ontology=None,
            warehouse=None,
            grantable=set(),
            dialect="duckdb",
            space_id=None,
            session_id=None,
        )
    finally:
        reset_turn(token)
    assert isinstance(env, dict)
    readings = env["reconfirm"]["interpretations"]
    assert len(readings) in {2, 3}
    assert env["reconfirm"]["suggested_question"] == readings[0]
    assert env.get("abstain_reason") not in {
        "serving_deadline_exceeded",
        "serving_deadline_reserve",
    }
    assert calls and calls[0]["route_hint"] == "reason"
    assert calls[0]["sql_vote_kind"] == "readings"


def test_empty_sql_retries_plan_before_any_askback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    monkeypatch.setattr(
        "dms_executor.generative_ask.validate_compiled_sql",
        lambda *args, **kwargs: None,
    )
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        kind = ctx.get("sql_vote_kind")
        if kind == "plan":
            return {
                "query_plan": {
                    "tables": ["lot"],
                    "joins": [],
                    "measure": "quantity",
                    "filters": [],
                }
            }
        if kind == "plan_solve":
            return {"query_sql": "SELECT 1 AS quantity"}
        return {}

    token = bind_turn(None)
    try:
        env = begin(
            question="how many lots are on hand",
            compute=compute,
            ontology=None,
            warehouse=None,
            grantable=set(),
            dialect="duckdb",
            space_id=None,
            session_id=None,
        )
        from dms_executor.scale_miss import current

        turn = current()
        assert env is None
        assert turn is not None
        assert turn.steps[0] == "generate"
        assert "plan" in turn.steps
        assert "askback" not in turn.steps
        assert turn.checked_sql == "SELECT 1 AS quantity"
        assert calls_used() >= 2
    finally:
        reset_turn(token)
    kinds = [item.get("sql_vote_kind") for item in calls]
    assert kinds[0] == "direct"
    assert "plan" in kinds
    assert kinds.index("plan") < kinds.index("plan_solve")


def test_explain_failure_is_fed_back_then_escalated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    reasons = ["explain:BinderException", "explain:BinderException"]

    def validate(sql: str, **kwargs: Any) -> str | None:
        _ = sql, kwargs
        if reasons:
            return reasons.pop(0)
        return None

    monkeypatch.setattr("dms_executor.generative_ask.validate_compiled_sql", validate)
    monkeypatch.setattr(
        "dms_executor.ask_ladder.richer_context_block",
        lambda *args, **kwargs: "ontology_joins:\nlot.sku = product.sku",
    )
    calls: list[dict[str, Any]] = []
    token = bind_turn(None)
    try:
        begin(
            question="how many lots are on hand",
            compute=_compute_log(calls),
            ontology=None,
            warehouse=None,
            grantable=set(),
            dialect="duckdb",
            space_id=None,
            session_id=None,
        )
        from dms_executor.scale_miss import current

        turn = current()
        assert turn is not None
        assert turn.checked_sql == "SELECT 3 AS quantity"
    finally:
        reset_turn(token)
    repair = [item for item in calls if item.get("sql_vote_kind") == "repair"]
    escalate = [item for item in calls if item.get("route_hint") == "escalate"]
    assert repair
    assert "explain:BinderException" in repair[0]["sql_vote_prompt"]
    assert "ontology_joins:" in repair[0]["sql_vote_prompt"]
    assert escalate and escalate[0]["route_hint"] == "escalate"
    assert "model" not in escalate[0]


def test_compile_misfit_uses_checked_sql_and_unknown_keeps_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    monkeypatch.setattr(
        "dms_executor.generative_ask.validate_compiled_sql",
        lambda *args, **kwargs: None,
    )
    calls: list[dict[str, Any]] = []
    token = bind_turn(None)
    try:
        begin(
            question="how many lots are on hand",
            compute=_compute_log(calls),
            ontology=None,
            warehouse=None,
            grantable={"lot"},
            dialect="duckdb",
            space_id=None,
            session_id=None,
        )
        chosen = compile_override(
            "SELECT utilisation FROM location",
            "how many lots are on hand",
            grantable={"lot"},
            warehouse=None,
        )
        assert chosen == "SELECT 1 AS quantity"
        shape = [item for item in calls if item.get("sql_vote_kind") == "shape"]
        assert shape and shape[0]["route_hint"] == "reason"

        def unknown(ctx: dict[str, Any]) -> dict[str, Any]:
            if ctx.get("sql_vote_kind") == "shape":
                return {"ontology": {"metrics": []}}
            return {"query_sql": "SELECT 1 AS quantity"}

        from dms_executor.scale_miss import current

        turn = current()
        assert turn is not None
        turn.compute = unknown
        kept = compile_override(
            "SELECT utilisation FROM location",
            "how many lots are on hand",
            grantable={"lot"},
            warehouse=None,
        )
        assert kept is None
    finally:
        reset_turn(token)


def test_budget_stop_is_an_askback_not_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    from cortex_client.ask_clock import AskClock, _clock

    clock = AskClock()
    clock.deadline = time.monotonic() - 1
    clock_token = _clock.set(clock)
    token = bind_turn(None)
    try:
        env = stop_envelope("how many lots are on hand", space_id=None, session_id=None)
        assert env["reconfirm"]["interpretations"]
        assert len(env["reconfirm"]["interpretations"]) in {2, 3}
        assert env.get("abstain_reason") not in {
            "serving_deadline_exceeded",
            "serving_deadline_reserve",
        }
        assert "serving_deadline_s" not in env
        remember_partial({"answer_id": "ans_partial", "text": "rows already ran"})
        # remember_partial needs the turn begin/stop just created only if current() is set.
        # stop_envelope does not open a turn. Bind one.
    finally:
        reset_turn(token)
        _clock.reset(clock_token)

    from dms_executor.scale_miss import _TURN, Turn

    turn = Turn()
    held = _TURN.set(turn)
    try:
        remember_partial({"answer_id": "ans_partial", "text": "rows already ran"})
        got = stop_envelope("how many lots are on hand", space_id=None, session_id=None)
    finally:
        _TURN.reset(held)
    assert got["answer_id"] == "ans_partial"


def test_explain_askback_names_readings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    env = explain_askback("how many lots are on hand", space_id=None, session_id=None)
    assert env["reconfirm"]["reason_code"] == "explain"
    assert len(env["reconfirm"]["interpretations"]) in {2, 3}


def test_schema_ladder_runs_for_explain_when_the_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_executor.schema_retry import SchemaLadder

    class Budget:
        def allow(self) -> bool:
            return True

    monkeypatch.delenv("DMS_SCHEMA_RETRY", raising=False)
    monkeypatch.delenv("DMS_SCALE_MISS", raising=False)
    off = SchemaLadder(grantable=set(), warehouse=None, dialect="duckdb")
    assert (
        off.plan(
            outcome="checker:explain:BinderException",
            sql="SELECT 1",
            attempts=[{}],
            budget=Budget(),
            question="how many",
        )
        is None
    )
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    on = SchemaLadder(grantable=set(), warehouse=None, dialect="duckdb")
    plan = on.plan(
        outcome="checker:explain:BinderException",
        sql="SELECT 1",
        attempts=[{}],
        budget=Budget(),
        question="how many",
    )
    assert plan is not None
    assert plan.rung == "rung1"


def test_generate_is_recorded_before_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    monkeypatch.delenv("DMS_AI_CLARIFY", raising=False)
    monkeypatch.delenv("DMS_ASK_LADDER", raising=False)
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)
    order: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        order.append("generate")
        _ = ctx
        return {}

    def retrieve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        order.append("retrieve")
        _ = args, kwargs
        return {"measures": {}}

    monkeypatch.setattr(
        "dms_executor.generative_ask.retrieve_short_context",
        retrieve,
    )
    from dms_executor.generative_ask import maybe_generative_ask

    maybe_generative_ask(
        "how many lots are on hand",
        tables=[],
        compute=compute,
        submit=lambda sql: None,
        ledger_append=lambda body: None,
        demo_gate=False,
        dialect="duckdb",
        grantable=set(),
    )
    assert order
    assert order[0] == "generate"
    assert "retrieve" in order
    assert order.index("generate") < order.index("retrieve")


def _budget_payload() -> dict[str, Any]:
    return {
        "insights_fail": "insights_timeout:generate",
        "query_sql": "SELECT quantity FROM lot",
        "query_plan": {
            "tables": ["lot"],
            "joins": [],
            "measure": "quantity",
            "filters": [],
        },
    }


def test_insights_budget_stop_asks_back_when_the_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    monkeypatch.delenv("DMS_AI_CLARIFY", raising=False)
    monkeypatch.delenv("DMS_ASK_LADDER", raising=False)
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        _ = ctx
        return _budget_payload()

    from dms_executor.generative_ask import maybe_generative_ask

    env = maybe_generative_ask(
        "how many lots are on hand",
        tables=[],
        compute=compute,
        submit=lambda sql: submitted.append(sql),
        ledger_append=lambda body: None,
        demo_gate=False,
        dialect="duckdb",
        grantable=set(),
    )
    assert isinstance(env, dict)
    assert env["answer_id"] == "ans_scale_miss_budget"
    assert env.get("abstain_reason") != "insights_timeout:generate"
    block = env["reconfirm"]
    assert block["understood"].startswith("Understood measure quantity")
    assert block["sql"] == "SELECT quantity FROM lot"
    assert [item["label"] for item in block["actions"]] == ["continue", "narrow it"]
    assert submitted == []


def test_insights_budget_stop_returns_a_partial_not_an_abstain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    monkeypatch.delenv("DMS_AI_CLARIFY", raising=False)
    monkeypatch.delenv("DMS_ASK_LADDER", raising=False)
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)
    partial = {
        "answer_id": "ans_partial",
        "badge": "L1_GOVERNED_METRIC",
        "abstained": False,
        "text": "rows already ran",
        "rows": [{"quantity": 1}],
    }

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        _ = ctx
        from dms_executor.scale_miss import remember_partial

        remember_partial(partial)
        return _budget_payload()

    from dms_executor.generative_ask import maybe_generative_ask

    env = maybe_generative_ask(
        "how many lots are on hand",
        tables=[],
        compute=compute,
        submit=lambda sql: None,
        ledger_append=lambda body: None,
        demo_gate=False,
        dialect="duckdb",
        grantable=set(),
    )
    assert isinstance(env, dict)
    assert env["answer_id"] == "ans_partial"
    assert env["abstained"] is False
    assert env["text"] == "rows already ran"


def test_insights_budget_stop_still_abstains_when_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_SCALE_MISS", raising=False)
    monkeypatch.delenv("DMS_AI_CLARIFY", raising=False)
    monkeypatch.delenv("DMS_ASK_LADDER", raising=False)
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        _ = ctx
        return _budget_payload()

    from dms_executor.generative_ask import maybe_generative_ask

    env = maybe_generative_ask(
        "how many lots are on hand",
        tables=[],
        compute=compute,
        submit=lambda sql: None,
        ledger_append=lambda body: None,
        demo_gate=False,
        dialect="duckdb",
        grantable=set(),
    )
    assert isinstance(env, dict)
    assert env["answer_id"] == "ans_gen01_abstain"
    assert "insights_timeout:generate" in " ".join(str(item) for item in env["assumptions"])


def test_override_sql_that_fails_a_gate_is_not_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_SCALE_MISS", "1")
    from dms_executor.generative_ask import _submit_validated
    from dms_executor.ontology import coverage_from_sql_path
    from dms_executor.route_stamp import ROUTE_COMPILE

    compile_sql = "SELECT supplier, SUM(amount) AS spend FROM supplier GROUP BY supplier"
    # Escalated or replacement SQL that drops the group. The same gates catch it.
    override_sql = "SELECT SUM(amount) AS spend FROM supplier"
    monkeypatch.setattr(
        "dms_executor.scale_miss.compile_override",
        lambda *args, **kwargs: override_sql,
    )
    submitted: list[str] = []
    env = _submit_validated(
        compile_sql,
        question="What is our total spend by supplier?",
        space_id=None,
        session_id=None,
        submit=lambda sql: submitted.append(sql),
        ledger_append=lambda body: None,
        notes=(),
        plan_source="other",
        coverage=coverage_from_sql_path(sql=compile_sql),
        warehouse=None,
        grantable={"supplier"},
        served_route=ROUTE_COMPILE,
    )
    assert submitted == []
    blob = " ".join(str(item) for item in env["assumptions"])
    assert "unhonored_qualifier:group_by=supplier" in blob
    assert override_sql not in submitted
