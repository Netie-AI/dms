"""UX-CLARIFY-CARD-01. Ask back before SQL when DMS_EARLY_CLARIFY=1.

Flag off does not call the early role. A silent model does not change the
serve. The card is the existing clarify envelope. A confirmed pick is a
writer example, never a stored answer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from cortex_client.compute import _insights_body, begin_answer_model_calls
from dms_executor import Executor
from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.verified_queries import list_clarify_examples, lookup_verified_query

_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_CLEAR = "Which inventory skus are listed?"
_AMBIG = "Which warehouse is best?"
_SKU = "SELECT sku FROM inventory"
_VALUE = "Total outbound value by warehouse for the latest month"
_SHIP = "Shipment count by warehouse for the latest month"


def _arm(monkeypatch: pytest.MonkeyPatch, *, flag: str | None) -> None:
    for name in (
        "DMS_AI_FIRST",
        "DMS_AI_CLARIFY",
        "DMS_ASK_LADDER",
        "DMS_SCALE_MISS",
        "DMS_DEADLINE_ASKBACK",
        "DMS_VQ_RAG",
        "DMS_CLOOP_B",
        "DMS_SCHEMA_RETRY",
        "DMS_LANE_ONTOLOGY_RANKED",
    ):
        monkeypatch.delenv(name, raising=False)
    if flag is None:
        monkeypatch.delenv("DMS_EARLY_CLARIFY", raising=False)
    else:
        monkeypatch.setenv("DMS_EARLY_CLARIFY", flag)


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
        ledger_append=lambda _payload: {"entry_id": "led_early", "hash": "hash_early"},
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


def _ambiguous() -> dict[str, Any]:
    return {
        "clarify_verdict": {
            "category": "ambiguous",
            "readings": [
                {
                    "id": "value",
                    "text": _VALUE,
                    "slots": {
                        "metric": "outbound_value_myr",
                        "grain": "warehouse",
                        "filter": "latest month",
                    },
                },
                {
                    "id": "ship",
                    "text": _SHIP,
                    "slots": {
                        "metric": "shipment_count",
                        "grain": "warehouse",
                        "filter": "latest month",
                    },
                },
            ],
        }
    }


def test_flag_off_is_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "off.duckdb"

    def _run(flag: str | None) -> tuple[list[str], list[str], dict[str, Any] | None]:
        _arm(monkeypatch, flag=flag)
        kinds: list[str] = []
        submitted: list[str] = []

        def compute(ctx: dict[str, Any]) -> dict[str, Any]:
            kinds.append(str(ctx.get("sql_vote_kind") or ""))
            return _names(query_sql=_SKU)

        env = _ask(db, compute, submitted, _CLEAR)
        return kinds, submitted, env

    unset_kinds, unset_sql, unset_env = _run(None)
    zero_kinds, zero_sql, zero_env = _run("0")
    assert "early_clarify" not in unset_kinds
    assert unset_kinds == zero_kinds
    assert unset_sql == zero_sql
    assert _view(unset_env) == _view(zero_env)
    again_kinds, again_sql, again_env = _run(None)
    assert again_kinds == unset_kinds
    assert again_sql == unset_sql
    assert _view(again_env) == _view(unset_env)


def test_ambiguous_card_is_fast_and_has_no_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, flag="1")
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        return _ambiguous()

    started = time.perf_counter()
    env = _ask(tmp_path / "amb.duckdb", compute, submitted, _AMBIG)
    elapsed = time.perf_counter() - started
    assert elapsed < 3
    assert submitted == []
    assert len(calls) == 1
    assert calls[0]["sql_vote_kind"] == "early_clarify"
    assert calls[0]["route_hint"] == "early_clarify"
    assert calls[0]["early_timeout_s"] == 3.0
    prompt = str(calls[0]["sql_vote_prompt"])
    assert "Ontology metrics:" in prompt
    assert "Schema:" in prompt
    assert "Do not write SQL" in prompt
    assert prompt.endswith(_AMBIG)
    assert "gpt-" not in json.dumps(calls)
    assert env is not None
    assert env["route"] == "needs_clarification"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env.get("sql_used") in (None, "")
    assert env.get("abstain_reason") == "clarify_ambiguous"
    block = env["clarify"]
    assert block["blocks"] is True
    assert block["reason_code"] == "clarify_ambiguous"
    assert [item["text"] for item in block["readings"]] == [_VALUE, _SHIP]
    assert all(item["sql_preview"] == "" for item in block["readings"])
    assert_envelope_valid(env)


def test_one_reading_is_not_a_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, flag="1")
    calls: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(str(ctx.get("sql_vote_kind") or ""))
        if ctx.get("sql_vote_kind") == "early_clarify":
            return {
                "clarify_verdict": {
                    "category": "ambiguous",
                    "readings": [{"id": "only", "text": _VALUE, "slots": {}}],
                }
            }
        return _names(query_sql="")

    env = _ask(tmp_path / "one.duckdb", compute, [], _AMBIG)
    assert calls[0] == "early_clarify"
    assert env is None or env.get("route") != "needs_clarification"


def test_unambiguous_and_silent_match_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "clear.duckdb"

    def _run(flag: str | None, *, answerable: bool) -> tuple[list[str], dict[str, Any]]:
        _arm(monkeypatch, flag=flag)
        kinds: list[str] = []
        submitted: list[str] = []

        def compute(ctx: dict[str, Any]) -> dict[str, Any]:
            kinds.append(str(ctx.get("sql_vote_kind") or ""))
            if ctx.get("sql_vote_kind") == "early_clarify":
                if answerable:
                    return {
                        "clarify_verdict": {
                            "category": "answerable",
                            "readings": [],
                            "missing": [],
                        }
                    }
                return _names(query_sql=_SKU)
            return _names(query_sql=_SKU)

        env = _ask(db, compute, submitted, _CLEAR)
        assert env is not None
        return kinds, _view(env)

    off_kinds, off_view = _run(None, answerable=True)
    on_kinds, on_view = _run("1", answerable=True)
    assert off_kinds == [kind for kind in on_kinds if kind != "early_clarify"]
    assert on_kinds[0] == "early_clarify"
    assert on_view == off_view
    assert on_view["clarify"] is None
    silent_kinds, silent_view = _run("1", answerable=False)
    assert silent_kinds[0] == "early_clarify"
    assert silent_view == off_view


def test_pick_runs_the_pipeline_and_stores_an_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, flag="1")
    db = tmp_path / "pick.duckdb"
    submitted: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx.get("sql_vote_kind") == "early_clarify":
            return _ambiguous()
        return _names(query_sql=_SKU)

    env = _ask(db, compute, submitted, _AMBIG)
    assert env is not None and submitted == []
    ex = Executor(cortex=None, warehouse_path=db)
    asked: list[str] = []

    def _live(question: str, **_kwargs: Any) -> dict[str, Any]:
        asked.append(question)
        return {
            "abstained": False,
            "badge": "L2_VALIDATED",
            "sql_used": _SKU,
            "rows": [{"sku": "A"}],
            "text": "one sku",
        }

    monkeypatch.setattr(ex, "live_ask", _live)
    monkeypatch.setattr(ex, "offer_reconfirm", lambda env, **_kwargs: env)
    out = ex.clarify_pick(env["clarify"]["clarify_id"], reading_id="value")
    assert out["rows"] == [{"sku": "A"}]
    assert out["sql_used"] == _SKU
    assert _VALUE in asked[0]
    assert "outbound_value_myr" in asked[0]
    listed = list_clarify_examples(
        space_id=None,
        warehouse=db,
        grantable=set(_GRANTS),
        dialect=SERVING_DIALECT,
    )
    assert len(listed) == 1
    assert listed[0]["provenance"] == "clarify_example"
    assert _AMBIG in listed[0]["question"]
    assert _VALUE in listed[0]["question"]
    assert listed[0]["sql"] == _SKU
    assert (
        lookup_verified_query(
            listed[0]["question"],
            warehouse=db,
            grantable=set(_GRANTS),
            dialect=SERVING_DIALECT,
        )
        is None
    )


def test_refine_is_one_new_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch, flag="1")
    from dms_executor.clarify import run_pick

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        return _ambiguous()

    env = _ask(tmp_path / "refine.duckdb", compute, [], _AMBIG)
    assert env is not None
    asked: list[str] = []
    run_pick(
        env["clarify"]["clarify_id"],
        reading_id=None,
        text="Total outbound value by warehouse for the latest month",
        grantable=set(_GRANTS),
        dialect=SERVING_DIALECT,
        ask=lambda question, **_kwargs: asked.append(question) or {"badge": "ABSTAIN"},
    )
    assert asked == ["Total outbound value by warehouse for the latest month"]


def test_role_is_not_a_model_name() -> None:
    body = _insights_body(
        _AMBIG,
        session_id="ses",
        space_id="spc",
        ontology={"metrics": []},
        route_hint="early_clarify",
    )
    assert body["model_preference"] == "early_clarify"
    assert "model" not in body
    assert "strict" not in body


def test_seam_sends_the_role_and_a_three_second_cap() -> None:
    import dms_executor

    seen: dict[str, Any] = {}

    class _Cortex:
        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            seen["question"] = question
            seen["kwargs"] = kwargs
            return {"clarify_verdict": {"category": "answerable", "readings": []}}

    dms_executor._insights_compute_seam(
        _Cortex(),
        _AMBIG,
        session_id="ses",
        space_id="spc",
        ontology={
            "sql_vote_prompt": "prompt body",
            "sql_vote_kind": "early_clarify",
            "route_hint": "early_clarify",
            "early_timeout_s": 3.0,
        },
    )
    assert seen["question"] == "prompt body"
    assert seen["kwargs"]["route_hint"] == "early_clarify"
    assert seen["kwargs"]["timeout"] == 3.0
    onto = seen["kwargs"]["ontology"]
    assert "early_timeout_s" not in onto
    assert "route_hint" not in onto
    assert "sql_vote_prompt" not in onto
