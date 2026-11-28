"""R1-RULES-OUT-01. DMS_RULES_OFF defaults off. On, rule lanes do not decide.

Off is byte-identical for the router, the trap list, the sheet lane, and
measure linking. On, those entry points return None or False and measure
linking uses the ranker (DMS_RULES_OFF implies DMS_NO_WORD_RULES).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from dms_executor.bronze_sheet_ask import (
    bronze_lane_table,
    bronze_sheet_lane_enabled,
    maybe_bronze_sheet_ask,
)
from dms_executor.demo_ask import _is_predictive, answer_demo_question
from dms_executor.demo_pack import HOW_FULL_TRAP_Q, is_uncertified_paraphrase
from dms_executor.keyword_routes import locked_measure
from dms_executor.measure_link import MeasureLink, no_word_rules_enabled
from dms_executor.ontology import demo_ontology
from dms_executor.semantic_retrieve import intent_slots, retrieve_short_context

_BRONZE = "In book.xlsx sheet Sales, what are the top 3 categories by sales_value_myr?"
_FORECAST = "forecast revenue for next month"
_REVENUE = "What was total revenue?"


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _stable_env(env: dict[str, Any] | None) -> dict[str, Any] | None:
    if env is None:
        return None
    out = dict(env)
    for key in list(out):
        if key == "as_of" or key.startswith("engine_"):
            out.pop(key)
    return out


def _snapshot() -> dict[str, str]:
    onto = demo_ontology(Path("/no/such/dms-rules-off-lake"))
    question = "capacity utilisation by warehouse"
    revenue = answer_demo_question(_REVENUE)
    life = answer_demo_question("What is the meaning of life?")
    context = retrieve_short_context(question, warehouse=None, ontology=onto)
    return {
        "trap": str(is_uncertified_paraphrase(HOW_FULL_TRAP_Q)),
        "predictive": str(_is_predictive(_FORECAST)),
        "revenue": _digest(_stable_env(revenue)),
        "life": _digest(_stable_env(life)),
        "badge": str((revenue or {}).get("badge")),
        "route": str((revenue or {}).get("route")),
        "text": str((revenue or {}).get("text")),
        "bronze_lane": str(bronze_sheet_lane_enabled()),
        "bronze_table": str(bronze_lane_table(_BRONZE)),
        "lock": str(locked_measure("total revenue")),
        "slots": _digest(intent_slots(question, onto)),
        "context": _digest(context),
        "no_word": str(no_word_rules_enabled()),
    }


def test_flag_defaults_off_and_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)
    monkeypatch.delenv("DMS_NO_WORD_RULES", raising=False)
    monkeypatch.delenv("DMS_LANE_BRONZE_SHEET", raising=False)
    unset = _snapshot()
    assert no_word_rules_enabled() is False
    assert unset["no_word"] == "False"
    assert unset["lock"] == "outbound_value_myr"
    assert "Total outbound revenue" in unset["text"]
    monkeypatch.setenv("DMS_RULES_OFF", "0")
    assert _snapshot() == unset
    monkeypatch.setenv("DMS_RULES_OFF", "true")
    assert _snapshot() == unset
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", "1")
    lane_on = _snapshot()
    assert lane_on["bronze_lane"] == "True"
    assert lane_on["bronze_table"] not in {"None", ""}
    monkeypatch.setenv("DMS_RULES_OFF", "0")
    assert _snapshot() == lane_on


def test_flag_on_rule_lanes_do_not_decide(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_RULES_OFF", "1")
    monkeypatch.delenv("DMS_NO_WORD_RULES", raising=False)
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", "1")
    assert is_uncertified_paraphrase(HOW_FULL_TRAP_Q) is False
    assert _is_predictive(_FORECAST) is False
    held = answer_demo_question(_REVENUE)
    assert isinstance(held, dict)
    assert held["reason_code"] == "rules_off"
    assert held["rows"] == []
    assert "Total outbound revenue" not in str(held.get("text") or "")
    assert bronze_sheet_lane_enabled() is False
    assert bronze_lane_table(_BRONZE) is None
    assert maybe_bronze_sheet_ask(_BRONZE) is None
    assert locked_measure("total revenue") is None
    assert no_word_rules_enabled() is True


def _grade52():
    import importlib
    import sys

    path = str(Path(__file__).resolve().parents[1] / "scripts" / "verify")
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module("grade52")


def test_flag_on_ranking_compile_does_not_stamp_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generate miss must not serve the question-word compiler.

    The stub never receives model SQL (model_calls stays 0). With the flag
    on, a ranking plan is a reconfirm, and grade52 does not call it rule.
    """
    from types import SimpleNamespace

    from dms_executor.generative_ask import maybe_generative_ask

    monkeypatch.setenv("DMS_RULES_OFF", "1")
    monkeypatch.delenv("DMS_NO_WORD_RULES", raising=False)
    monkeypatch.setattr(
        "dms_executor.generative_ask.ontology_plan_from_ranking",
        lambda *_args, **_kwargs: {
            "query_plan": {
                "measure": "outbound_value_myr",
                "group_by": [],
                "filters": [],
            }
        },
    )
    submitted: list[str] = []

    def submit(sql: str) -> Any:
        submitted.append(sql)
        return SimpleNamespace(ok=True, status="ok", run_id="run_rules", output={"rows": []})

    env = maybe_generative_ask(
        "total revenue by category",
        warehouse=None,
        grantable=set(),
        compute=lambda _ctx: {"phase": "generate"},
        submit=submit,
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led", hash="hash_rules"),
        ontology=demo_ontology(Path("/no/such/dms-rules-off-lake")),
        bind_on_miss=False,
        demo_gate=False,
        dialect="duckdb",
    )
    assert submitted == []
    assert env is not None
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["badge"] == "ABSTAIN"
    assert env.get("plan_origin") not in {"ontology_ranking", "ontology_compile"}
    assert not str(env.get("ladder_rung") or "").startswith("compile")
    assert env["reconfirm"]["reason_code"] == "rules_off"
    grader = _grade52()
    assert grader.served_route(env) == "abstain"
    assert grader.path_group(grader.serve_path(env)) != "rule"


def test_rules_off_links_by_rank_not_by_the_locker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_RULES_OFF", "1")
    monkeypatch.delenv("DMS_NO_WORD_RULES", raising=False)
    onto = demo_ontology(Path("/no/such/dms-rules-off-lake"))
    seen: list[str] = []

    def _rank(question: str, ontology: object, **_kwargs: object) -> MeasureLink:
        seen.append(question)
        return MeasureLink(None, "measure_unranked", ())

    monkeypatch.setattr("dms_executor.semantic_retrieve.rank_measure_link", _rank)
    slots = intent_slots("total revenue by category", onto)
    assert seen
    assert slots.get("measure") != "outbound_value_myr"
