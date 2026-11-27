"""R1-CLARIFY-01: ask back with 1 to 3 readings. Flag default off.

Synthetic schema only. Flag off does not call the understand role.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from cortex_client.compute import _insights_body, begin_answer_model_calls
from dms_executor.clarify import resolve_pick, run_pick
from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology

_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_CLEAR = "Which inventory skus are listed?"
_VAGUE = "Just give me last month's number"
_SKU = "SELECT sku FROM inventory"
_LOC = "SELECT location_id FROM locations"
_SECRET = "SELECT sku FROM secret_table"
_ROOT = Path(__file__).resolve().parents[1]


def _arm(monkeypatch: pytest.MonkeyPatch, *, flag: str | None) -> None:
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.delenv("DMS_SCHEMA_RETRY", raising=False)
    monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
    if flag is None:
        monkeypatch.delenv("DMS_AI_CLARIFY", raising=False)
    else:
        monkeypatch.setenv("DMS_AI_CLARIFY", flag)


def _names(**extra: Any) -> dict[str, Any]:
    base = {"phase": "generate", "served_model": "fake-model"}
    base.update(extra)
    return base


def _ask(
    db: Path,
    compute: Any,
    submitted: list[str],
    question: str,
) -> dict[str, Any] | None:
    begin_answer_model_calls()
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))

    def submit(sql: str) -> dict[str, Any]:
        submitted.append(sql)
        return {"rows": [{"sku": "A"}], "sql_used": sql}

    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=set(_GRANTS),
        compute=compute,
        submit=submit,
        ledger_append=lambda _payload: {"entry_id": "led_clarify", "hash": "hash_clarify"},
        ontology=onto,
        bind_on_miss=False,
        dialect=SERVING_DIALECT,
        demo_gate=False,
    )


def _view(env: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(env, dict):
        return {"none": True}
    return {
        "badge": env.get("badge"),
        "abstained": env.get("abstained"),
        "route": env.get("route"),
        "served_route": env.get("served_route"),
        "sql_used": env.get("sql_used"),
        "rows": env.get("rows"),
        "text": env.get("text"),
        "clarify": env.get("clarify"),
    }


def test_flag_off_is_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, flag=None)
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        return _names(query_sql=_SKU)

    db = tmp_path / "off.duckdb"
    vague = _ask(db, compute, submitted, _VAGUE)
    assert calls == []
    assert submitted == []
    assert vague is not None and vague["abstained"] is True
    assert vague.get("clarify") is None
    again = _ask(db, compute, submitted, _VAGUE)
    assert _view(vague) == _view(again)
    clear_calls: list[dict[str, Any]] = []
    clear_sql: list[str] = []

    def clear_compute(ctx: dict[str, Any]) -> dict[str, Any]:
        clear_calls.append(dict(ctx))
        return _names(query_sql=_SKU)

    first = _ask(db, clear_compute, clear_sql, _CLEAR)
    second_calls: list[dict[str, Any]] = []
    second_sql: list[str] = []

    def second_compute(ctx: dict[str, Any]) -> dict[str, Any]:
        second_calls.append(dict(ctx))
        return _names(query_sql=_SKU)

    second = _ask(db, second_compute, second_sql, _CLEAR)
    assert json.dumps(clear_calls, sort_keys=True) == json.dumps(second_calls, sort_keys=True)
    assert clear_sql == second_sql
    assert _view(first) == _view(second)
    assert first is None or first.get("clarify") is None
    assert all(item.get("route_hint") != "reason" for item in clear_calls)


def test_flag_on_never_calls_phrase_checkers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DMS_AI_CLARIFY on: wording checkers stay at zero, including a silent model."""
    _arm(monkeypatch, flag="1")
    monkeypatch.setenv("DMS_INSIGHTS_CALL_CAP", "4")
    hits = {"unsure": 0, "predictive": 0}

    class _Search:
        def search(self, *_args: object, **_kwargs: object) -> None:
            hits["unsure"] += 1
            return None

    def _predictive(*_args: object, **_kwargs: object) -> bool:
        hits["predictive"] += 1
        return False

    monkeypatch.setattr("dms_executor.generative_ask._UNSURE_ASK", _Search())
    monkeypatch.setattr("dms_executor.generative_ask._is_predictive", _predictive)
    monkeypatch.setattr("dms_executor.demo_ask._is_predictive", _predictive)

    def _silent(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        return {"generative": {"query_sql": "SELECT 1"}}

    calls: list[dict[str, Any]] = []
    submitted: list[str] = []
    env = _ask(tmp_path / "silent.duckdb", _silent, submitted, "What should I worry about?")
    assert hits == {"unsure": 0, "predictive": 0}
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env.get("clarify") is None
    assert submitted == []
    assert calls and calls[0].get("route_hint") == "reason"
    kinds = [str(item.get("sql_vote_kind") or "") for item in calls]
    assert kinds[0] == "understand"
    assert "repair" in kinds and "context" in kinds and "escalate" in kinds
    assert calls[-1].get("route_hint") == "escalate"
    block = env.get("reconfirm")
    assert isinstance(block, dict)
    assert block.get("reason_code") == env.get("abstain_reason") == "ladder_exhausted:no_sql"
    blob = json.dumps(env)
    assert "too vague" not in blob
    assert "predictive: history" not in blob

    calls.clear()
    submitted.clear()
    predictive = _ask(
        tmp_path / "predict.duckdb",
        _silent,
        submitted,
        "What will revenue be next quarter?",
    )
    assert hits == {"unsure": 0, "predictive": 0}
    assert predictive is not None
    assert predictive.get("reconfirm", {}).get("reason_code") == "ladder_exhausted:no_sql"
    assert submitted == []

    def _answerable(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        if ctx.get("route_hint") == "reason":
            return {
                "clarify_verdict": {
                    "category": "answerable",
                    "readings": [],
                    "missing": [],
                }
            }
        return _names(query_sql=_SKU)

    calls.clear()
    continued = _ask(
        tmp_path / "clear.duckdb",
        _answerable,
        submitted,
        "What will revenue be next quarter?",
    )
    assert hits == {"unsure": 0, "predictive": 0}
    assert continued is None or continued.get("reconfirm", {}).get("reason_code") != "predictive"
    assert "predictive: history" not in json.dumps(continued)


def test_ambiguous_readings_are_checked_and_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, flag="1")
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []
    previews = [_SKU, _LOC]

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        if ctx.get("route_hint") == "reason":
            return {
                "clarify_verdict": {
                    "category": "ambiguous",
                    "readings": [
                        {
                            "id": "sku",
                            "text": "List inventory sku values",
                            "slots": {"grain": "sku"},
                        },
                        {
                            "id": "loc",
                            "text": "List location ids",
                            "slots": {"grain": "location"},
                        },
                    ],
                    "missing": ["time"],
                }
            }
        sql = previews[sum(1 for item in calls if item.get("sql_vote_kind") == "preview") - 1]
        return _names(query_sql=sql)

    env = _ask(tmp_path / "amb.duckdb", compute, submitted, _VAGUE)
    assert submitted == []
    assert env is not None
    assert env["route"] == "needs_clarification"
    assert env["abstained"] is True and env["rows"] == []
    block = env["clarify"]
    assert block["blocks"] is True
    assert len(block["readings"]) == 2
    assert block["readings"][0]["sql_preview"] == _SKU
    assert block["readings"][1]["sql_preview"] == _LOC
    assert calls[0]["route_hint"] == "reason"
    assert "gpt-" not in json.dumps(calls)
    assert_envelope_valid(env)
    refused = resolve_pick(
        block["clarify_id"],
        reading_id="sku",
        text=None,
        grantable={"locations"},
        dialect=SERVING_DIALECT,
    )
    assert refused.question is None
    assert refused.envelope is not None
    assert refused.envelope.get("clarify") is None
    assert "ungranted" in json.dumps(refused.envelope)
    asked: list[str] = []
    picked = run_pick(
        block["clarify_id"],
        reading_id="loc",
        text=None,
        grantable=set(_GRANTS),
        dialect=SERVING_DIALECT,
        ask=lambda question, **_kwargs: asked.append(question) or {"badge": "ABSTAIN"},
    )
    assert picked.ran is not None
    assert asked and "List location ids" in asked[0]


def test_pick_refuses_ungranted_before_the_second_card_is_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, flag="1")
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("route_hint") == "reason":
            return {
                "clarify_verdict": {
                    "category": "ambiguous",
                    "readings": [
                        {"id": "ok", "text": "List inventory sku values", "slots": {}},
                        {"id": "no", "text": "Read the secret table", "slots": {}},
                    ],
                    "missing": [],
                }
            }
        if "secret" in str(ctx.get("sql_vote_prompt") or ""):
            return _names(query_sql=_SECRET)
        return _names(query_sql=_SKU)

    env = _ask(tmp_path / "grant.duckdb", compute, submitted, _VAGUE)
    assert submitted == []
    assert env is not None
    previews = [item["sql_preview"] for item in env["clarify"]["readings"]]
    assert _SECRET not in previews
    assert _SKU in previews


def test_clear_question_is_one_extra_call_and_the_same_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "clear.duckdb"

    def _run(flag: str | None) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
        _arm(monkeypatch, flag=flag)
        calls: list[dict[str, Any]] = []
        submitted: list[str] = []

        def compute(ctx: dict[str, Any]) -> dict[str, Any]:
            calls.append(dict(ctx))
            if ctx.get("route_hint") == "reason":
                return {
                    "clarify_verdict": {
                        "category": "answerable",
                        "readings": [],
                        "missing": [],
                    }
                }
            return _names(query_sql=_SKU)

        env = _ask(db, compute, submitted, _CLEAR)
        return calls, submitted, env

    off_calls, off_sql, off_env = _run(None)
    on_calls, on_sql, on_env = _run("1")
    assert len(on_calls) == len(off_calls) + 1
    assert on_calls[0].get("route_hint") == "reason"
    assert off_sql == on_sql
    assert _view(off_env)["sql_used"] == _view(on_env)["sql_used"]
    assert _view(off_env)["served_route"] == _view(on_env)["served_route"]


def test_unanswerable_is_not_in_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, flag="1")
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        assert ctx.get("route_hint") == "reason"
        return {"clarify_verdict": {"category": "unanswerable", "readings": [], "missing": []}}

    env = _ask(tmp_path / "none.duckdb", compute, submitted, _VAGUE)
    assert submitted == []
    assert env is not None and env.get("clarify") is None
    block = env.get("reconfirm")
    assert isinstance(block, dict) and block.get("reason_code") == "not_in_data"
    assert env["rows"] == []
    assert_envelope_valid(env)


def test_free_text_is_one_new_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, flag="1")

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("route_hint") == "reason":
            return {
                "clarify_verdict": {
                    "category": "ambiguous",
                    "readings": [
                        {"id": "sku", "text": "List inventory sku values", "slots": {}},
                    ],
                    "missing": ["time"],
                }
            }
        return _names(query_sql=_SKU)

    env = _ask(tmp_path / "free.duckdb", compute, [], _VAGUE)
    assert env is not None
    cid = env["clarify"]["clarify_id"]
    asked: list[str] = []
    run_pick(
        cid,
        reading_id=None,
        text="How many locations are listed?",
        grantable=set(_GRANTS),
        dialect=SERVING_DIALECT,
        ask=lambda question, **_kwargs: asked.append(question) or {},
    )
    assert asked == ["How many locations are listed?"]
    with pytest.raises(KeyError):
        resolve_pick(
            cid,
            reading_id="sku",
            text=None,
            grantable=set(_GRANTS),
            dialect=SERVING_DIALECT,
        )


def test_reason_role_is_not_a_model_name() -> None:
    body = _insights_body(
        "Which inventory skus are listed?",
        session_id="ses",
        space_id="spc",
        ontology={"metrics": []},
        route_hint="reason",
    )
    assert body["model_preference"] == "reason"
    assert "model" not in body
    assert "strict" not in body
    banned = _insights_body(
        "Which inventory skus are listed?",
        session_id="ses",
        space_id="spc",
        ontology={"metrics": []},
        route_hint="gpt-4",
    )
    assert banned["model_preference"] != "gpt-4"
    from dms_executor import _insights_compute_seam

    class _Cortex:
        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            self.question = question
            self.kwargs = kwargs
            return {}

    cortex = _Cortex()
    _insights_compute_seam(
        cortex,
        _CLEAR,
        session_id="ses",
        space_id="spc",
        ontology={
            "route_hint": "reason",
            "sql_vote_prompt": "Classify\nQuestion:\n" + _CLEAR,
            "sql_vote_kind": "understand",
            "metrics": [],
        },
    )
    assert cortex.kwargs.get("route_hint") == "reason"
    assert "gpt-4" not in json.dumps(cortex.kwargs)


def test_offer_leaves_a_clarify_block_alone() -> None:
    from dms_api.routes.chat import _offer_reconfirm

    class _Ask:
        def offer_reconfirm(self, env: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
            out = dict(env)
            out["text"] = "replaced"
            return out

    blocked = {"text": "Which reading?", "clarify": {"blocks": True, "readings": []}}
    assert _offer_reconfirm(_Ask(), blocked, body=_Body(), cortex=None) is blocked
    plain = {"text": "original", "abstained": True, "badge": "ABSTAIN"}
    assert _offer_reconfirm(_Ask(), plain, body=_Body(), cortex=None)["text"] == "replaced"


class _Body:
    question = "q"
    space_id = None
    session_id = None
    grounded_tables = None
    ask_path = None


def test_pick_route_is_mounted() -> None:
    from dms_api.routes.chat import router

    paths = [str(getattr(route, "path", "")) for route in router.routes]
    assert "/v1/chat/clarify/pick" in paths


def test_no_question_word_rule_in_clarify_or_new_generative_patterns() -> None:
    clarify = (_ROOT / "packages/executor/dms_executor/clarify.py").read_text(encoding="utf-8")
    ask = (_ROOT / "packages/executor/dms_executor/generative_ask.py").read_text(encoding="utf-8")
    assert _compile_count(clarify) == 0
    assert _string_in_count(clarify) == 0
    assert "import re" not in clarify
    # Baselines on main before this ticket: 4 re.compile calls, 2 string memberships.
    assert _compile_count(ask) == 4
    assert _string_in_count(ask) == 2


def test_ambiguity_pack_precision_and_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic AMBROSIA-style labels. The fake returns the labeled category."""
    _arm(monkeypatch, flag="1")
    labeled = [
        ("ambiguous", "ambiguous"),
        ("ambiguous", "ambiguous"),
        ("answerable", "answerable"),
        ("unanswerable", "unanswerable"),
    ]
    predicted_clarify = 0
    true_clarify = 0
    hit = 0
    db = tmp_path / "pack.duckdb"
    for index, (gold, category) in enumerate(labeled):
        submitted: list[str] = []

        def compute(ctx: dict[str, Any], category: str = category) -> dict[str, Any]:
            if ctx.get("route_hint") == "reason":
                readings = []
                if category == "ambiguous":
                    readings = [
                        {"id": "a", "text": "List inventory sku values", "slots": {}},
                        {"id": "b", "text": "List location ids", "slots": {}},
                    ]
                return {
                    "clarify_verdict": {
                        "category": category,
                        "readings": readings,
                        "missing": ["time"] if category == "ambiguous" else [],
                    }
                }
            kind = str(ctx.get("sql_vote_kind") or "")
            if kind == "preview":
                sql = _LOC if "location" in str(ctx.get("sql_vote_prompt") or "") else _SKU
                return _names(query_sql=sql)
            return _names(query_sql=_SKU)

        env = _ask(db, compute, submitted, f"case {index} measure")
        got = (
            isinstance(env, dict)
            and env.get("route") == "needs_clarification"
        )
        if gold == "ambiguous":
            true_clarify += 1
            if got:
                hit += 1
        if got:
            predicted_clarify += 1
        if gold == "unanswerable":
            assert isinstance(env, dict)
            assert env.get("reconfirm", {}).get("reason_code") == "not_in_data"
        if gold == "answerable":
            assert not got
    precision = hit / predicted_clarify if predicted_clarify else 0
    recall = hit / true_clarify if true_clarify else 0
    assert precision == 1
    assert recall == 1


def _compile_count(source: str) -> int:
    count = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else ""
            if name == "compile":
                count += 1
    return count


def _string_in_count(source: str) -> int:
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ast.In) for op in node.ops):
            continue
        if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            count += 1
    return count
