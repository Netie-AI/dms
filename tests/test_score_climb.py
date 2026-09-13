"""GEN-02 live coverage climb harness: measured counts, WRONG=0, no invent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import (  # noqa: E402
    BASELINE_91C5CC99,
    EXIT_CONFIG,
    PLATFORM_API,
    answered_vs_baseline,
    baseline_answered,
    build_climb_report,
    classify_path,
    climb_url,
    judge_envelope,
    main,
    self_check,
)


def test_baseline_is_vq04_measured_not_a_target():
    b = BASELINE_91C5CC99
    assert b["commit"] == "91c5cc99"
    assert b["ok"] == 7
    assert b["layer"] == 10
    assert b["abstain"] == 9
    assert b["wrong"] == 0
    assert b["n"] == 26
    assert baseline_answered() == 17


def test_climb_url_fail_closed():
    assert climb_url(None, {}) is None
    assert climb_url("", {}) is None
    assert climb_url(None, {"DMS_API_BASE": PLATFORM_API}) == PLATFORM_API
    assert climb_url("https://studio.netie.ai/api/", {}) == "https://studio.netie.ai/api"


def test_ab_baseline_a9578348_counts():
    from score_curated import BASELINE_AB_A9578348

    b = BASELINE_AB_A9578348
    assert b["commit"] == "a9578348"
    assert b["exact_answered"] == 10
    assert b["generative_answered"] == 1
    assert b["wrong"] == 0
    assert b["n"] == 26


def test_classify_crag_validate_or_abstain():
    from score_curated import classify_crag

    assert (
        classify_crag(
            {
                "route": "generated",
                "abstained": False,
                "badge": "L2_VALIDATED",
                "assumptions": ["executed via Cortex submit after validate"],
            }
        )
        == "validated"
    )
    assert (
        classify_crag(
            {
                "route": "generated",
                "abstained": True,
                "badge": "ABSTAIN",
                "assumptions": ["GEN-01: validate:explain:BinderException"],
            }
        )
        == "abstain_validate"
    )
    assert (
        classify_crag(
            {
                "route": "generated",
                "abstained": True,
                "badge": "ABSTAIN",
                "assumptions": ["GEN-01: compute abstained (unsure)"],
            }
        )
        == "abstain_gate"
    )


def test_main_climb_ab_without_url_is_config(monkeypatch):
    monkeypatch.delenv("DMS_API_BASE", raising=False)
    monkeypatch.delenv("STUDIO_API_BASE", raising=False)
    monkeypatch.delenv("DMS_URL", raising=False)
    assert main(["--climb"]) == EXIT_CONFIG
    assert main(["--climb", "--ab"]) == EXIT_CONFIG


def test_classify_path_splits_exact_vs_generative():
    assert classify_path("generated") == "generative"
    assert classify_path("governed_metric") == "exact_match"
    assert classify_path("verified_query") == "exact_match"
    assert classify_path("abstain") == "other"
    assert classify_path(None) == "other"


def test_answered_vs_baseline_labels():
    assert answered_vs_baseline(18, 17) == "rose"
    assert answered_vs_baseline(17, 17) == "flat"
    assert answered_vs_baseline(16, 17) == "fell"


def test_demo_fallback_is_wrong_not_ok():
    assert (
        judge_envelope(
            {"expect": "l0", "min_rows": 1},
            {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "rows": [{"v": 1}],
                "demo_fallback_used": True,
            },
        )
        == "WRONG"
    )


def test_health_iap_403_is_blocked_not_a_score():
    from score_curated import classify_health

    kind, detail = classify_health(403, None, "text/html")
    assert kind == "blocked"
    assert "Not a score" in detail
    kind, _ = classify_health(401, None, "text/html")
    assert kind == "blocked"


def test_health_demo_fallback_fails():
    from score_curated import classify_health

    kind, detail = classify_health(
        200,
        {"demo_fallback": True, "ask_mode": "live", "product": "dms"},
        "application/json",
    )
    assert kind == "fail"
    assert "demo_fallback" in detail


def test_health_live_ok():
    from score_curated import classify_health

    kind, _ = classify_health(
        200,
        {"demo_fallback": False, "ask_mode": "live", "product": "dms"},
        "application/json",
    )
    assert kind == "ok"


def test_green_refuse_still_wrong():
    assert (
        judge_envelope(
            {"expect": "refuse"},
            {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"v": 1}]},
        )
        == "WRONG"
    )


def test_climb_report_never_invents_complete_or_9995():
    tallies = {"OK": 8, "LAYER": 10, "ABSTAIN": 8, "WRONG": 0}
    cases = [
        {
            "id": "cq_x",
            "verdict": "LAYER",
            "route": "generated",
            "path": "generative",
            "expect": "l0",
        },
        {
            "id": "cq_pack",
            "verdict": "OK",
            "route": "governed_metric",
            "path": "exact_match",
            "expect": "l0",
        },
    ]
    report = build_climb_report(tallies, cases=cases, url=PLATFORM_API)
    blob = json.dumps(report)
    assert "99.95" not in blob
    assert "COMPLETE" not in blob
    assert report["claim"] == "measured"
    assert report["passed_wrong_zero"] is True
    assert report["answered_vs_baseline"] == "rose"
    assert report["delta"]["answered"] == 1
    assert report["answered_by_path"]["generative"] == 1
    assert report["answered_by_path"]["exact_match"] == 1
    assert report["wrong"] == 0


def test_climb_report_wrong_fails_law():
    tallies = {"OK": 7, "LAYER": 10, "ABSTAIN": 8, "WRONG": 1}
    report = build_climb_report(tallies, cases=[], url=PLATFORM_API)
    assert report["passed_wrong_zero"] is False
    assert report["answered_vs_baseline"] == "flat"


def test_self_check_covers_climb_plants():
    assert self_check() == 0


def test_offline_ab_wrong_zero_and_gen_not_below_baseline():
    from score_curated import run_ab_curated

    report = run_ab_curated()
    blob = json.dumps(report)
    assert "99.95" not in blob
    assert "COMPLETE" not in blob
    assert report["claim"] == "measured"
    assert report["wrong"] == 0
    assert report["exact_match"]["wrong"] == 0
    assert report["generative"]["wrong"] == 0
    assert report["generative"]["answered"] >= report["baseline_ab"]["generative_answered"]
    planted = {
        "trap_last_month",
        "trap_short_paraphrase",
        "trap_how_full_synonym",
        "trap_delayed_count",
        "trap_stock_by_bin",
        "trap_alerts_ungranted",
    }
    for row in report["cases"]:
        if row["id"] in planted:
            assert row["generative"] != "WRONG", row
            assert row["generative_badge"] == "ABSTAIN", row
