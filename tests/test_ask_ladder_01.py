"""DMS-AI-ABSTAIN-01: generative misses climb. Ungranted and hostile SQL do not.

The flag defaults off. Off, a phrase gate still returns before any model call.
On, the same miss climbs repair, richer context, plan, then an escalate role.
DMS does not name a model. A checker failure is not served.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.compute import _insights_body, begin_answer_model_calls
from dms_executor.ask_ladder import (
    ClimbSession,
    is_direct_abstain,
    named_code,
    phrase_gate_reason,
)
from dms_executor.demo_warehouse import SERVING_DIALECT, connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.sql_loop import AskCallBudget

_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_QUESTION = "Which inventory skus are listed?"
_VAGUE = "Just give me last month's number"
_BAD = "SELECT skuu FROM inventory"
_GOOD = "SELECT sku FROM inventory"
_EMPTY = "SELECT sku FROM inventory WHERE sku = '___none___'"
_HOSTILE = "SELECT * FROM read_parquet('secret.parquet')"
_UNGRAINTED = "SELECT sku FROM not_granted_table"
_PACK = Path("tests/fixtures/curated_ceo/questions.yaml")
_BANNED = ("opus", "anthropic", "gpt-4", "claude-")


def _names(**extra: Any) -> dict[str, Any]:
    base = {
        "served_model": "fake-model",
        "served_provider": "fake-provider",
        "phase": "generate",
    }
    base.update(extra)
    return base


def _prepare(db: Path) -> None:
    ensure_demo_warehouse(db)


def _submit(db: Path, seen: list[str]):
    def submit(sql: str) -> Any:
        seen.append(sql)
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_ladder", output={"rows": rows})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_ladder", hash="hash_ladder_not_entry")


def _arm(monkeypatch: pytest.MonkeyPatch, *, flag: str | None, cap: str) -> None:
    monkeypatch.setenv("DMS_INSIGHTS_CALL_CAP", cap)
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("DMS_GEN01_RULES", "0")
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.delenv("DMS_SCHEMA_RETRY", raising=False)
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    if flag is None:
        monkeypatch.delenv("DMS_ASK_LADDER", raising=False)
    else:
        monkeypatch.setenv("DMS_ASK_LADDER", flag)


def _ask(db: Path, compute, submitted: list[str], question: str = _QUESTION):
    begin_answer_model_calls()
    _prepare(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=set(_GRANTS),
        compute=compute,
        submit=_submit(db, submitted),
        ledger_append=_ledger,
        ontology=onto,
        dialect=SERVING_DIALECT,
        demo_gate=False,
    )


def _pack_questions() -> list[tuple[str, str]]:
    current = ""
    out: list[tuple[str, str]] = []
    for line in _PACK.read_text(encoding="utf-8").splitlines():
        if line.startswith("  - id:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("    question:") and current:
            out.append((current, line.split(":", 1)[1].strip()))
            current = ""
    return out


def test_named_codes_and_direct_stops() -> None:
    assert named_code("question is too vague or time-unbounded to ground") == "vague_question"
    assert named_code("predictive: history is not a forecast") == "predictive"
    assert (
        named_code("year 2099 is not a certified period; all-time history is not that year")
        == "year_not_certified"
    )
    assert (
        named_code("uncertified paraphrase: not a generative certify boundary")
        == "uncertified_paraphrase"
    )
    assert named_code("compute abstained (unsure)") == "compute_unsure"
    assert named_code("query_sql was empty") == "query_sql_empty"
    assert named_code("insights generate did not return a typed plan or SQL") == "no_plan"
    assert named_code("retrieve bind abstained (unsure)") == "compute_unsure"
    assert named_code("query_plan was not typed") == "no_plan"
    assert is_direct_abstain("ungranted:shipments")
    assert is_direct_abstain("validate:ungranted")
    assert is_direct_abstain("hostile_sql:read_parquet")
    assert is_direct_abstain("checker:hostile_sql:path_not_allowed")
    assert not is_direct_abstain("explain:missing_column")
    assert not is_direct_abstain("vague_question")
    assert phrase_gate_reason(_VAGUE, demo_gate=False) == "vague_question"
    worry = "Are we short on anything the warehouse should worry about?"
    assert phrase_gate_reason(worry, demo_gate=False) == "vague_question"
    pending = "Which high-risk suppliers have pending shipments?"
    assert phrase_gate_reason(pending, demo_gate=False) is None


def test_pack_phrase_gates_are_the_existing_checkers() -> None:
    """The 52's early give-ups are the existing gates, not a new word list."""
    hit = {
        qid
        for qid, question in _pack_questions()
        if phrase_gate_reason(question, demo_gate=False)
    }
    assert "trap_last_month" in hit
    assert "trap_short_paraphrase" in hit
    assert "trap_high_risk_pending" not in hit


def test_flag_off_phrase_does_not_call_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, flag=None, cap="4")
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(ctx)
        return _names(query_sql=_GOOD)

    env = _ask(tmp_path / "off.duckdb", compute, [], _VAGUE)
    assert calls == []
    assert env is not None and env["abstained"] is True and env["rows"] == []
    blob = json.dumps(env)
    assert "ladder_exhausted" not in blob
    assert "too vague" in blob or "vague" in blob


def test_flag_off_checker_failure_does_not_climb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, flag=None, cap="4")
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(ctx)
        return _names(query_sql=_BAD)

    env = _ask(tmp_path / "off-bad.duckdb", compute, submitted)
    assert len(calls) == 1
    assert submitted == []
    assert env is not None and env["abstained"] is True
    assert "ladder_exhausted" not in json.dumps(env)


def test_repair_serves_checker_clean_sql(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, flag="1", cap="4")
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        if len(calls) == 1:
            return _names(query_sql=_BAD)
        return _names(query_sql=_GOOD)

    env = _ask(tmp_path / "repair.duckdb", compute, submitted)
    assert env is not None and env["abstained"] is False
    assert env["rows"]
    assert submitted == [_GOOD]
    assert _BAD not in submitted
    assert len(calls) == 2
    prompt = str(calls[1].get("sql_vote_prompt") or "")
    assert "feedback:" in prompt
    assert prompt.rstrip().endswith(_QUESTION) or "\nQuestion:\n" in prompt
    blob = json.dumps(calls)
    for banned in _BANNED:
        assert banned not in blob
    assert "route_hint" not in calls[1]


def test_four_rungs_end_at_escalate_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Budget starts unused, so repair, context, plan, and escalate all run."""
    _arm(monkeypatch, flag="1", cap="4")
    db = tmp_path / "rungs.duckdb"
    _prepare(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        if str(calls[-1].get("sql_vote_kind")) == "escalate":
            return _names(query_sql=_GOOD)
        return {}

    def check(sql: str) -> str | None:
        return None if sql == _GOOD else "explain:missing_column"

    def submit(sql: str, _payload: dict[str, Any]) -> dict[str, Any]:
        submitted.append(sql)
        return {
            "abstained": False,
            "rows": [{"sku": "A"}],
            "assumptions": [],
            "served_route": "model",
        }

    budget = AskCallBudget()
    session = ClimbSession(
        compute=compute,
        budget=budget,
        ctx={"objects": []},
        question=_QUESTION,
        warehouse=db,
        dialect=SERVING_DIALECT,
        grantable=set(_GRANTS),
        space_id=None,
        session_id=None,
        ontology=onto,
        attempts=[],
        check=check,
        submit=submit,
        reserve=False,
    )
    env = session.on_abstain("explain:missing_column", _BAD, "generative")
    assert env is not None and env["abstained"] is False
    assert submitted == [_GOOD]
    kinds = [str(item.get("sql_vote_kind")) for item in calls]
    assert kinds[:3] == ["repair", "context", "plan"]
    assert "escalate" in kinds
    assert calls[-1].get("route_hint") == "escalate"
    context = str(calls[1].get("sql_vote_prompt") or "")
    assert "column samples" in context and "value hints" in context
    plan = str(calls[2].get("sql_vote_prompt") or "")
    assert "Do not write SQL" in plan
    blob = json.dumps(calls)
    for banned in _BANNED:
        assert banned not in blob
    assert "model" not in calls[-1]


def test_ungranted_and_hostile_do_not_climb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, flag="1", cap="4")
    for name, sql in (("ungranted", _UNGRAINTED), ("hostile", _HOSTILE)):
        calls: list[dict[str, Any]] = []
        submitted: list[str] = []

        def compute(ctx: dict[str, Any], sql: str = sql) -> dict[str, Any]:
            calls.append(ctx)
            return _names(query_sql=sql)

        env = _ask(tmp_path / f"{name}.duckdb", compute, submitted)
        assert len(calls) == 1, name
        assert submitted == [], name
        assert env is not None and env["abstained"] is True and env["rows"] == [], name
        blob = json.dumps(env)
        assert "ladder_exhausted" not in blob, name
        if name == "ungranted":
            assert "ungranted" in blob or "sql_relation_not_granted" in blob
        else:
            assert "hostile_sql" in blob


def test_empty_result_is_fed_back_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _arm(monkeypatch, flag="1", cap="4")
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(ctx)
        return _names(query_sql=_EMPTY)

    with caplog.at_level(logging.WARNING, logger="dms_executor.pipeline_failure"):
        env = _ask(tmp_path / "empty.duckdb", compute, submitted)
    assert env is not None and env["abstained"] is True and env["rows"] == []
    assert str(env.get("abstain_reason") or "").startswith("ladder_exhausted:")
    assert len(calls) > 1
    assert any("empty_result" in str(item.get("sql_vote_prompt") or "") for item in calls[1:])
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ladder_exhausted" in text
    assert _QUESTION not in text
    assert "___none___" not in text


def test_phrase_on_calls_the_model_then_names_the_abstain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _arm(monkeypatch, flag="1", cap="4")
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(ctx)
        return _names()

    with caplog.at_level(logging.WARNING, logger="dms_executor.pipeline_failure"):
        env = _ask(tmp_path / "vague.duckdb", compute, [], _VAGUE)
    assert calls
    assert env is not None and env["abstained"] is True and env["rows"] == []
    reason = str(env.get("abstain_reason") or "")
    assert "vague_question" in reason
    assert "Found " not in str(env.get("text") or "")
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "vague_question" in text
    assert _VAGUE not in text


def test_plan_then_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, flag="1", cap="4")
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        if len(calls) == 1:
            return {
                "vote_plan": {
                    "tables": ["inventory"],
                    "joins": [],
                    "measure": "sku",
                    "filters": [],
                }
            }
        return _names(query_sql=_GOOD)

    budget = AskCallBudget()
    session = ClimbSession(
        compute=compute,
        budget=budget,
        ctx={"objects": ["inventory"]},
        question=_QUESTION,
        warehouse=None,
        dialect=SERVING_DIALECT,
        grantable=set(_GRANTS),
        space_id=None,
        session_id=None,
        ontology=None,
        attempts=[],
        check=lambda sql: None if sql == _GOOD else "explain:missing_column",
        submit=lambda sql, _payload: {
            "abstained": False,
            "rows": [{"sku": "A"}],
            "assumptions": [],
        },
        reserve=False,
    )
    session.index = 2
    env = session.on_abstain("no_sql", None, "generative")
    assert env is not None and env["abstained"] is False
    assert [str(item.get("sql_vote_kind")) for item in calls] == ["plan", "plan_solve"]
    assert "Follow the plan" in str(calls[1].get("sql_vote_prompt") or "")
    assert "\nQuestion:\n" in str(calls[1].get("sql_vote_prompt") or "")


def test_cap_reserves_the_reconfirm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, flag="1", cap="2")
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(ctx)
        return _names(query_sql=_GOOD)

    budget = AskCallBudget()
    budget.take()
    session = ClimbSession(
        compute=compute,
        budget=budget,
        ctx={},
        question=_QUESTION,
        warehouse=None,
        dialect=SERVING_DIALECT,
        grantable=set(_GRANTS),
        space_id=None,
        session_id=None,
        ontology=None,
        attempts=[],
        check=lambda _sql: None,
        submit=lambda sql, _payload: {"abstained": False, "rows": [{"sku": "A"}]},
        reserve=True,
    )
    assert session.on_abstain("explain:missing_column", _BAD, "generative") is None
    assert calls == []
    assert session.exhausted_reason("explain:missing_column") == "explain"


def test_escalate_role_is_not_a_model_name() -> None:
    body = _insights_body(
        "Which inventory skus are listed?",
        session_id="ses",
        space_id="spc",
        ontology={"metrics": []},
        route_hint="escalate",
    )
    assert body["model_preference"] == "escalate"
    assert "model" not in body
    assert "strict" not in body
    for banned in _BANNED:
        assert banned not in json.dumps(body)


def test_served_envelope_still_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, flag="1", cap="4")
    submitted: list[str] = []

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_GOOD)

    env = _ask(tmp_path / "ok.duckdb", compute, submitted)
    assert env is not None and env["abstained"] is False
    assert_envelope_valid(env)
    assert submitted == [_GOOD]
