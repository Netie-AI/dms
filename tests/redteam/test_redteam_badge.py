"""badge_label_audit: every rule must be able to fail, and a clean envelope must pass."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from rt_badge import badge_label_audit


def _good() -> dict[str, Any]:
    return {
        "answer_id": "ans_gen01",
        "badge": "L2_VALIDATED",
        "abstained": False,
        "route": "generated",
        "ask_mode": "live",
        "text": "Found 1 row(s).",
        "rows": [{"n": 1}],
        "values": [{"id": "v0", "value": 1.0, "label": "n"}],
        "sql_used": "SELECT 1 AS n",
        "audit_id": "led_1",
        "assumptions": ["GEN-01 ontology compile", "executed via Cortex submit after validate"],
        "plan_origin": "generate_sql",
        "plan_source": "ontology_plan",
        "contributing_sources": [],
        "drillthrough_token": None,
    }


def _abstain() -> dict[str, Any]:
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "route": "generated",
        "ask_mode": "live",
        "text": "I cannot certify an ontology-grounded query.",
        "rows": [],
        "values": [],
        "sql_used": None,
        "contributing_sources": [],
        "drillthrough_token": None,
        "assumptions": ["GEN-01: validate:ungranted:alerts"],
    }


def _has(audit: dict[str, Any], prefix: str) -> bool:
    return any(r.startswith(prefix) for r in audit["reasons"])


def test_clean_generated_l2_passes() -> None:
    a = badge_label_audit(_good())
    assert a["ok"] and a["reasons"] == []


def test_clean_abstain_passes() -> None:
    assert badge_label_audit(_abstain())["ok"]


@pytest.mark.parametrize("badge", ["L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_ANOMALOUS"])
def test_generated_route_may_only_be_l2_validated(badge: str) -> None:
    env = _good() | {"badge": badge}
    a = badge_label_audit(env)
    assert not a["ok"] and _has(a, "badge_exceeds_route")


def test_certified_badge_on_a_model_plan_is_flagged() -> None:
    a = badge_label_audit(_good() | {"badge": "L0_CERTIFIED", "route": "sql"})
    assert not a["ok"] and _has(a, "certified_badge_on_model_plan")


def test_abstained_true_with_confident_badge() -> None:
    a = badge_label_audit(_good() | {"abstained": True})
    assert not a["ok"] and _has(a, "abstained_badge_mismatch")


def test_abstain_badge_with_abstained_false() -> None:
    a = badge_label_audit(_abstain() | {"abstained": False})
    assert not a["ok"] and _has(a, "abstained_badge_mismatch")


def test_badge_not_allowed() -> None:
    a = badge_label_audit(_good() | {"badge": "L9_SUPER"})
    assert not a["ok"] and _has(a, "badge_not_allowed")


@pytest.mark.parametrize(
    "patch,prefix",
    [
        ({"rows": [], "values": []}, "confident_without_rows_or_values"),
        ({"rows": []}, "confident_without_rows"),
        ({"sql_used": None}, "confident_without_sql"),
        ({"sql_used": "-- live ask (SQL not returned)"}, "confident_with_sql_not_returned"),
    ],
)
def test_confident_without_evidence(patch: dict[str, Any], prefix: str) -> None:
    a = badge_label_audit(_good() | patch)
    assert not a["ok"] and _has(a, prefix)


@pytest.mark.parametrize(
    "patch",
    [
        {"values": [{"id": "v0", "value": 1.0}]},
        {"rows": [{"n": 1}]},
        {"contributing_sources": [{"kind": "sql"}]},
        {"drillthrough_token": "tok_12345678"},
    ],
)
def test_abstain_must_carry_no_data(patch: dict[str, Any]) -> None:
    a = badge_label_audit(_abstain() | patch)
    assert not a["ok"] and _has(a, "abstain_carries_data")


def test_demoted_abstain_may_keep_its_sql_as_a_note() -> None:
    a = badge_label_audit(_abstain() | {"sql_used": "SELECT COUNT(*) FROM suppliers"})
    assert a["ok"] and "abstain_keeps_sql_used:demoted_after_execution" in a["notes"]


def test_abstain_text_with_a_figure() -> None:
    a = badge_label_audit(_abstain() | {"text": "I cannot, but the total was 1,234.50 anyway."})
    assert not a["ok"] and _has(a, "abstain_text_states_a_figure")
    assert badge_label_audit(_abstain() | {"text": "year 2099 is not a certified period"})["ok"]


def test_l2_without_execution_note_or_ledger_id() -> None:
    env = _good()
    env["assumptions"] = ["GEN-01 ontology compile"]
    assert _has(badge_label_audit(env), "l2_validated_without_execution_note")
    env2 = _good() | {"audit_id": "ans_gen01"}
    assert _has(badge_label_audit(env2), "l2_validated_without_ledger_audit_id")
    assert _has(
        badge_label_audit(_good() | {"audit_id": ""}), "l2_validated_without_ledger_audit_id"
    )


def test_demo_mode_with_confident_badge() -> None:
    a = badge_label_audit(_good() | {"ask_mode": "demo"})
    assert not a["ok"] and _has(a, "demo_answer_with_confident_badge")
    b = badge_label_audit(_good() | {"demo_fallback_used": True})
    assert _has(b, "demo_fallback_without_banner")


def test_refusal_route_with_confident_badge() -> None:
    a = badge_label_audit(_good() | {"route": "refused"})
    assert not a["ok"] and _has(a, "refusal_route_with_confident_badge")


def test_confident_badge_over_a_demotion_note() -> None:
    env = _good()
    env["assumptions"].append("prose figure not in query result: withheld (E4)")
    assert _has(badge_label_audit(env), "confident_badge_over_a_demotion_note")


def test_by_construction_routes() -> None:
    sheet = _good() | {
        "route": "bronze_sheet",
        "badge": "L0_CERTIFIED",
        "assumptions": ["bronze sheet x::Sales"],
    }
    sheet.pop("plan_origin")
    sheet.pop("plan_source")
    a = badge_label_audit(sheet)
    assert a["ok"] and any(n.startswith("badge_by_construction") for n in a["notes"])
    assert _has(badge_label_audit(sheet | {"badge": "L1_GOVERNED_METRIC"}), "badge_exceeds_route")
    pack = copy.deepcopy(sheet) | {"route": "governed_metric", "badge": "L0_CERTIFIED"}
    assert _has(badge_label_audit(pack), "badge_exceeds_route")


def test_engine_assigned_route_is_a_note_not_a_violation() -> None:
    env = _good() | {
        "route": "sql",
        "badge": "L1_GOVERNED_METRIC",
        "assumptions": ["live Cortex ask"],
    }
    env.pop("plan_origin")
    env.pop("plan_source")
    a = badge_label_audit(env)
    assert a["ok"] and any(n.startswith("engine_assigned_badge") for n in a["notes"])


def test_never_raises_on_junk() -> None:
    assert not badge_label_audit(None)["ok"]  # type: ignore[arg-type]
    assert isinstance(badge_label_audit({})["reasons"], list)
