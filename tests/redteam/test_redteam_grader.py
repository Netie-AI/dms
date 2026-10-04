"""Grader unit tests: each strictness rule must be able to turn a verdict red.

The mechanical grader is deliberately stricter than scripts/oracle_row_match.py (case,
NULL, column names, prose). If any of these tests goes green on a loosened grader, the
harness has lost the ability to fail (R-0007).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from rt_grader import (
    abstain_reason,
    compare_rows,
    grade,
    headline_check,
    is_answered,
    prose_check,
    prose_tokens,
)


def _env(
    rows: list[dict[str, Any]], *, text: str | None = None, badge: str = "L2_VALIDATED"
) -> dict[str, Any]:
    """A real, valid envelope built by the real constructor."""
    from dms_executor.envelope import build_answer_envelope

    body = text if text is not None else f"Found {len(rows)} row(s)."
    return build_answer_envelope(
        answer_id="ans_rt_unit",
        text=body,
        badge=badge,
        rows=rows,
        sql_used="SELECT 1 AS x",
        assumptions=["GEN-01 ontology compile", "executed via Cortex submit after validate"],
        audit_id="led_unit_1",
        route="generated",
        question="unit",
    )


def _abstain_env(reason: str = "GEN-01: validate:ungranted:alerts") -> dict[str, Any]:
    from dms_executor.envelope import build_answer_envelope

    return build_answer_envelope(
        answer_id="ans_rt_abstain",
        text=(
            "I cannot certify an ontology-grounded query for that question, "
            "so I am not executing one."
        ),
        badge="ABSTAIN",
        abstained=True,
        assumptions=[reason],
        route="generated",
        question="unit",
    )


# ------------------------------------------------------------------ strictness rules
def test_identical_rows_match() -> None:
    rows = [{"sku": "A", "n": 1}, {"sku": "B", "n": 2}]
    assert compare_rows(rows, list(reversed(rows)))["ok"]


def test_strings_are_case_sensitive() -> None:
    r = compare_rows([{"sku": "beta"}], [{"sku": "BETA"}])
    assert not r["ok"] and "rows_mismatch:values" in r["reasons"]


def test_strings_are_not_stripped() -> None:
    assert not compare_rows([{"sku": "BETA "}], [{"sku": "BETA"}])["ok"]


@pytest.mark.parametrize(
    "served,gold",
    [
        (None, ""),
        ("", None),
        (None, 0),
        (0, None),
        (False, 0),
        (0, False),
        ("", 0),
        ("null", None),
    ],
)
def test_null_blank_zero_false_are_four_different_cells(served: Any, gold: Any) -> None:
    assert not compare_rows([{"v": served}], [{"v": gold}])["ok"]


def test_column_name_mismatch_is_a_label_issue_but_values_still_graded() -> None:
    r = compare_rows([{"revenue": 5}], [{"stock_value_myr": 5}])
    assert r["ok"]
    assert any(x.startswith("column_name_mismatch") for x in r["label_issues"])
    bad = compare_rows([{"revenue": 6}], [{"stock_value_myr": 5}])
    assert not bad["ok"]
    assert any(x.startswith("column_name_mismatch") for x in bad["label_issues"])


def test_column_name_case_matters() -> None:
    r = compare_rows([{"Revenue": 5}], [{"revenue": 5}])
    assert any(x.startswith("column_name_mismatch") for x in r["label_issues"])


def test_column_count_mismatch_is_wrong() -> None:
    r = compare_rows([{"a": 1, "b": 2}], [{"a": 1}])
    assert not r["ok"] and any(x.startswith("column_count_mismatch") for x in r["reasons"])


def test_column_order_is_ignored() -> None:
    assert compare_rows([{"a": 1, "b": "x"}], [{"b": "x", "a": 1}])["ok"]


def test_float_noise_passes_but_display_rounding_does_not() -> None:
    assert compare_rows([{"v": 0.1 + 0.2}], [{"v": 0.3}])["ok"]
    assert not compare_rows([{"v": 1234.57}], [{"v": 1234.5678}])["ok"]


def test_integral_values_are_exact() -> None:
    assert not compare_rows([{"v": 10**13 + 1}], [{"v": 10**13}])["ok"]
    assert compare_rows([{"v": 3.0}], [{"v": 3}])["ok"]


def test_decimal_gold_against_float_served() -> None:
    assert compare_rows([{"v": 12.5}], [{"v": Decimal("12.50")}])["ok"]


def test_numeric_string_served_for_numeric_gold_is_a_label_issue() -> None:
    r = compare_rows([{"v": "12.50"}], [{"v": Decimal("12.5")}])
    assert r["ok"] and "numeric_served_as_string:v" in r["label_issues"]


def test_numeric_string_is_not_coerced_for_text_gold() -> None:
    assert not compare_rows([{"code": "123"}], [{"code": "00123"}])["ok"]
    assert not compare_rows([{"code": 123}], [{"code": "123"}])["ok"]


def test_duplicate_rows_are_counted() -> None:
    assert not compare_rows([{"a": 1}, {"a": 1}], [{"a": 1}, {"a": 2}])["ok"]


def test_row_count_mismatch() -> None:
    r = compare_rows([{"a": 1}], [{"a": 1}, {"a": 2}])
    assert "rows_mismatch:count=1/2" in r["reasons"]


def test_order_matters_only_with_order_by_and_limit_in_gold() -> None:
    fwd = [{"a": 3}, {"a": 2}, {"a": 1}]
    rev = list(reversed(fwd))
    top = "SELECT a FROM t ORDER BY a DESC LIMIT 3"
    assert not compare_rows(rev, fwd, gold_sql=top)["ok"]
    only_order = compare_rows(rev, fwd, gold_sql="SELECT a FROM t ORDER BY a DESC")
    assert only_order["ok"] and "row_order_differs_from_gold_order_by" in only_order["label_issues"]
    assert compare_rows(rev, fwd, gold_sql="SELECT a FROM t")["ok"]


def test_masked_cell_is_called_out() -> None:
    r = compare_rows([{"name": "DMSMASK_person_01"}], [{"name": "Ann"}])
    assert not r["ok"] and "masked_cell:name" in r["reasons"]


def test_empty_equals_empty_and_is_not_equal_to_rows() -> None:
    assert compare_rows([], [])["ok"]
    assert not compare_rows([], [{"a": 1}])["ok"]


def test_ragged_served_rows_are_wrong() -> None:
    assert not compare_rows([{"a": 1}, {"b": 2}], [{"a": 1}, {"a": 2}])["ok"]


# ------------------------------------------------------------------ prose
def test_prose_tokens_skip_identifiers_dates_and_row_count_is_allowed() -> None:
    text = "Found 2 row(s).\n  - sku=SKU-00397, ts=2026-07-01 10:00:00, total=1,234.50"
    assert prose_tokens(text) == ["2", "1234.50"]
    env = {
        "text": text,
        "rows": [{"sku": "SKU-00397", "total": 1234.5}, {"sku": "SKU-1", "total": 2.5}],
        "values": [],
    }
    assert prose_check(env, "q") == []


def test_prose_number_not_in_rows_is_flagged() -> None:
    env = {
        "text": "Found 1 row(s). Margin improved by 777 units.",
        "rows": [{"a": 5}],
        "values": [],
    }
    assert prose_check(env, "q") == ["777"]


def test_prose_number_echoed_from_question_is_allowed() -> None:
    env = {"text": "Top 5 shown. total=10", "rows": [{"t": 10}], "values": []}
    assert prose_check(env, "show the top 5") == []


def test_headline_must_equal_gold_for_single_value() -> None:
    env = _env([{"total": 7890.0}], text="Found 1 row(s).\n  - total=7890.0")
    hard, _ = headline_check(env, [{"total": 29840.0}])
    assert any(x.startswith("headline_value_not_gold") for x in hard)
    assert any(x.startswith("headline_text_mismatch") for x in hard)
    ok_hard, _ = headline_check(
        _env([{"total": 29840.0}], text="Found 1 row(s).\n  - total=29840.0"), [{"total": 29840.0}]
    )
    assert ok_hard == []


# ------------------------------------------------------------------ verdicts
def _grade(
    env: Any, *, expect: str = "answer", status: int | None = 200, gold: Any = None, **kw: Any
) -> dict[str, Any]:
    gold_rows = [{"x": 1}] if gold is None else gold
    return grade(
        expect=expect,
        question="q",
        http_status=status,
        http_body=env,
        gold_sql="SELECT 1 AS x",
        gold_rows=gold_rows,
        gold_error=None,
        **kw,
    )


def test_verdict_correct() -> None:
    assert _grade(_env([{"x": 1}]))["verdict"] == "CORRECT"


def test_verdict_wrong_values() -> None:
    out = _grade(_env([{"x": 2}]))
    assert out["verdict"] == "WRONG" and any("rows_mismatch" in r for r in out["reasons"])


def test_verdict_wrong_on_prose_number() -> None:
    out = _grade(_env([{"x": 1}], text="Found 1 row(s). There were 777 units."))
    assert out["verdict"] == "WRONG"
    assert any(r.startswith("prose_number_not_in_rows") for r in out["reasons"])


def test_verdict_abstain_names_the_reason() -> None:
    out = _grade(_abstain_env("GEN-01: validate:ungranted:alerts"))
    assert out["verdict"] == "ABSTAIN" and out["abstain_reason"] == "validate:ungranted:alerts"


def test_expect_abstain_with_confident_badge_is_wrong() -> None:
    out = _grade(_env([{"x": 1}]), expect="abstain", gold=[])
    assert out["verdict"] == "WRONG"
    assert any(r.startswith("confident_answer_where_abstain_expected") for r in out["reasons"])


def test_expect_abstain_with_abstain_is_correct() -> None:
    out = _grade(_abstain_env(), expect="abstain", gold=[])
    assert out["verdict"] == "CORRECT" and out["abstain_reason"]


def test_http_refusal_verdicts() -> None:
    body = {"detail": {"code": "grounding_not_grantable", "message": "no"}}
    assert _grade(body, status=403, expect="abstain")["verdict"] == "CORRECT"
    assert _grade(body, status=403, expect="answer")["verdict"] == "ABSTAIN"
    assert _grade({"detail": "boom"}, status=500)["verdict"] == "HARNESS_ERROR"


def test_harness_error_paths() -> None:
    assert _grade(None, status=None, exception="X: y")["verdict"] == "HARNESS_ERROR"
    assert (
        _grade(_env([{"x": 1}]), discarded="crossed_midnight:discarded")["verdict"]
        == "HARNESS_ERROR"
    )
    gold_err = grade(
        expect="answer",
        question="q",
        http_status=200,
        http_body=_env([{"x": 1}]),
        gold_sql="SELECT nope",
        gold_rows=None,
        gold_error="BinderException",
    )
    assert gold_err["verdict"] == "HARNESS_ERROR"


def test_abstain_reason_skips_bookkeeping_notes() -> None:
    demoted = {
        "assumptions": [
            "GEN-01 ontology compile",
            "executed via Cortex submit after validate",
            "GEN-01 Cortex ontology_plan SQL",
            "grouped/ranked ask answered by an ungrouped scalar: shape mismatch (E10/FF-01)",
        ],
        "text": "You asked for a breakdown ...",
    }
    assert abstain_reason(demoted).endswith("(E10/FF-01)")
    named = {"assumptions": ["GEN-01 ontology compile", "GEN-01: validate:ungranted:alerts"]}
    assert abstain_reason(named) == "validate:ungranted:alerts"
    bare = {
        "assumptions": ["GEN-01 ontology compile", "include: x"],
        "text": "Nothing to say.\nmore",
    }
    assert abstain_reason(bare) == "Nothing to say."


def test_is_answered_and_abstain_reason_helpers() -> None:
    assert is_answered(_env([{"x": 1}]))
    assert not is_answered(_abstain_env())
    assert not is_answered({"badge": "L2_VALIDATED", "abstained": True})
    assert abstain_reason({"abstain_reason": "reserved_param:as_of"}) == "reserved_param:as_of"
    assert abstain_reason({"text": "first line\nsecond"}) == "first line"
