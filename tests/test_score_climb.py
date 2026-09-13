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


def test_main_climb_without_url_is_config(monkeypatch):
    monkeypatch.delenv("DMS_API_BASE", raising=False)
    monkeypatch.delenv("STUDIO_API_BASE", raising=False)
    monkeypatch.delenv("DMS_URL", raising=False)
    assert main(["--climb"]) == EXIT_CONFIG


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
