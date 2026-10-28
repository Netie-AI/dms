"""WRONG-TRAP-01. A compile serve needs the question to name the measure.

The armed 52 run served trap_how_full_synonym from the compile path after the
model SQL failed EXPLAIN. The measure was utilisation_pct. The question does
not name that measure in the schema or the ontology. The same ranking still
compiles when the question does name it. No phrase list and no question id.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.reconfirm import apply_reconfirm
from dms_executor.semantic_retrieve import question_grounds_measure

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

from grade52 import (  # noqa: E402
    headline_wrong,
    not_scored_entries,
    not_scored_line,
    stamp_contradiction,
    summary_line,
    tries_line,
)

_GRANTS = {
    "inventory",
    "locations",
    "transactions",
    "suppliers",
    "shipments",
}
_BAD_SQL = "SELECT no_such_column FROM locations"
_HOW_FULL = "how full is each warehouse"
_STUFFED = "how stuffed is each warehouse"
_CAPACITY = "Show warehouse capacity utilisation"
_SALES = "Top 5 selling SKUs by sales"


def _onto(tmp_path: Path):
    db = tmp_path / "trap.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    return db, onto


def test_question_grounds_a_named_measure_not_a_grain_word(tmp_path: Path) -> None:
    _db, onto = _onto(tmp_path)
    assert question_grounds_measure(_CAPACITY, "utilisation_pct", onto) is True
    assert question_grounds_measure(_SALES, "outbound_value_myr", onto) is True
    assert question_grounds_measure("List chemicals in inventory", "stock_value_myr", onto)
    assert question_grounds_measure("Show top 3 categoty sales", "outbound_value_myr", onto)
    assert question_grounds_measure(_HOW_FULL, "utilisation_pct", onto) is False
    assert question_grounds_measure(_STUFFED, "utilisation_pct", onto) is False
    occupancy = "what is the occupancy percent of each warehouse"
    assert question_grounds_measure(occupancy, "utilisation_pct", onto)


def _payload(metric_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "phase": "generate",
        "plan_source": "ontology_plan",
        "plan_origin": "generate_sql",
        "query_sql": _BAD_SQL,
        "served_provider": "provider-called",
        "served_model": "model-called",
        "generative": {"ok": True, "sql": _BAD_SQL},
        "generate_legs": {
            "count": 1,
            "legs": [
                {
                    "returned": "sql",
                    "served_provider": "provider-called",
                    "served_model": "model-called",
                }
            ],
        },
    }
    if metric_id:
        body["ontology"] = {"ok": True, "metrics": [{"id": metric_id}]}
    return body


def _ask(tmp_path: Path, question: str, metric_id: str | None) -> dict[str, Any]:
    db, onto = _onto(tmp_path)
    submitted: list[str] = []

    def submit(sql: str) -> Any:
        submitted.append(sql)
        return SimpleNamespace(ok=True, status="ok", run_id="run_trap", output={"rows": [{"n": 1}]})

    def ledger(_payload: dict[str, Any]) -> Any:
        return SimpleNamespace(entry_id="led_trap", hash="hash_trap_not_entry")

    env = maybe_generative_ask(
        question,
        warehouse=db,
        grantable=_GRANTS,
        compute=lambda _ctx: _payload(metric_id),
        submit=submit,
        ledger_append=ledger,
        ontology=onto,
        bind_on_miss=False,
        demo_gate=False,
        dialect="duckdb",
    )
    assert env is not None
    assert_envelope_valid(env)
    env["_submitted"] = submitted
    return env


def test_compile_does_not_serve_an_ungrounded_measure(tmp_path: Path) -> None:
    for question in (_HOW_FULL, _STUFFED):
        env = _ask(tmp_path, question, "cq_capacity_utilisation")
        assert env["abstained"] is True
        assert env["badge"] == "ABSTAIN"
        assert env["rows"] == []
        assert env["_submitted"] == []
        blob = " ".join(str(item) for item in env["assumptions"])
        assert "ungrounded_measure:utilisation_pct" in blob
        assert "uncertified paraphrase" not in blob
        offered = apply_reconfirm(
            env,
            question=question,
            space_id=None,
            session_id=None,
            tables=["locations"],
            ask_path=None,
            ontology="utilisation_pct",
            schema="locations",
            complete=lambda _prompt: "Show warehouse capacity utilisation",
        )
        assert offered["reconfirm"]["suggested_question"] == "Show warehouse capacity utilisation"


def test_compile_still_serves_when_the_question_names_the_measure(tmp_path: Path) -> None:
    sales = _ask(tmp_path, _SALES, "cq_sales_top5_value")
    assert sales["abstained"] is False
    assert sales["badge"] == "L2_VALIDATED"
    assert sales["_submitted"]
    assert "outbound" in str(sales.get("sql_used") or "").lower()
    capacity = _ask(tmp_path, _CAPACITY, "cq_capacity_utilisation")
    assert capacity["abstained"] is False
    assert "utilisation_pct" in str(capacity.get("sql_used") or "").lower()
    assert "served_model" not in capacity
    assert capacity["plan_origin"] == "ontology_ranking"
    assert stamp_contradiction(capacity) is False
    assert capacity["generate_legs"]["legs"][0]["served_model"] == "model-called"


def test_model_sql_abstain_is_not_a_compile_stamp(tmp_path: Path) -> None:
    env = _ask(tmp_path, _HOW_FULL, None)
    assert env["abstained"] is True
    assert env["plan_source"] == "ontology_plan"
    assert env["plan_origin"] == "generate_sql"
    assert env["served_model"] == "model-called"
    assert stamp_contradiction(env) is False


def test_headline_wrong_includes_refusal_wrong() -> None:
    report = {
        "mode": "served",
        "dms_sha": "0" * 40,
        "pack_gold_served": "included",
        "n": 2,
        "correct": 1,
        "wrong": 0,
        "reconfirm": 0,
        "abstain": 0,
        "refusal_ok": 0,
        "refusal_wrong": 1,
        "empty_gold": 0,
        "gold_broken": 0,
    }
    assert headline_wrong(report) == 1
    line = summary_line(report)
    assert "wrong=1" in line and "refusal_wrong=1" in line


def test_not_scored_lists_pin_mismatch_ids_and_tries_gap() -> None:
    cases = [
        {
            "id": "cq_kept",
            "bucket": "CORRECT",
            "model_calls": 1,
            "pin_mismatch": False,
        },
        {
            "id": "cq_pin",
            "bucket": "CORRECT",
            "model_calls": 1,
            "pin_mismatch": True,
            "model_requested": "requested-pin",
            "served_model": "other-pin",
        },
    ]
    assert tries_line(cases) == (
        "tries correct_first=1 correct_retry=0 omitted_pin_mismatch=1 "
        "reason=pin_mismatch_not_in_tries"
    )
    assert not_scored_entries(cases) == [
        {"id": "cq_pin", "reason": "pin_mismatch:requested-pin/other-pin"}
    ]
    assert "cq_pin=pin_mismatch:requested-pin/other-pin" in not_scored_line(cases)


@pytest.mark.parametrize("question", [_HOW_FULL, _STUFFED])
def test_ungrounded_measure_names_the_gap(question: str, tmp_path: Path) -> None:
    env = _ask(tmp_path, question, "cq_capacity_utilisation")
    assert "ungrounded_measure:utilisation_pct" in str(env.get("text") or "")
