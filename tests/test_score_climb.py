"""GEN-02 live coverage climb harness: measured counts, WRONG=0, no invent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import (  # noqa: E402
    BASELINE_91C5CC99,
    BASELINE_AB_A9578348,
    DISTILL,
    EXIT_BLOCKED,
    EXIT_CONFIG,
    PLATFORM_API,
    answered_vs_baseline,
    ask_error_envelope,
    baseline_answered,
    build_climb_report,
    cf1010_blocked_detail,
    classify_path,
    climb_ab_live,
    climb_url,
    distill_block,
    is_cf1010,
    judge_envelope,
    main,
    probe_climb_host,
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
    b = BASELINE_AB_A9578348
    assert b["commit"] == "a9578348"
    assert b["exact_answered"] == 10
    assert b["generative_answered"] == 1
    assert b["wrong"] == 0
    assert b["n"] == 26
    assert b["exact_coverage_answered_pct"] == 38.46
    assert b["generative_coverage_answered_pct"] == 3.85
    assert round(100.0 * 10 / 26, 2) == 38.46
    assert round(100.0 * 1 / 26, 2) == 3.85


def test_distill_ladder_is_netie_native_not_vendor():
    assert DISTILL["ideas_only"] is True
    assert DISTILL["text2sql"]["vendor_sdk"] is False
    assert DISTILL["ontology_spine"]["yaml_pack_format"] is False
    assert list(DISTILL["ladder"]) == [
        "certified_first_then_generative",
        "ontology_spine_demo_ontology",
        "hybrid_fuse_and_crag_grades",
        "text2sql_cortex_compute_plus_bind_plan",
    ]
    vendors = {v.lower() for v in DISTILL["vendors_not_pasted"]}
    for name in ("db-gpt", "mybot", "n8n", "openwillow", "guaca", "rakazo"):
        assert name in vendors
    block = distill_block()
    blob = json.dumps(block)
    assert "99.95" not in blob
    assert "COMPLETE" not in blob
    assert block["baseline_ab"]["exact_coverage_answered_pct"] == 38.46
    assert block["baseline_ab"]["generative_coverage_answered_pct"] == 3.85
    root = Path(__file__).resolve().parents[1]
    assert list(DISTILL["founder_lock"])[:4] == [
        "semantic_retrieve",
        "ontology_relations",
        "generate_sql",
        "execute_validate",
    ]
    assert DISTILL["founder_lock"][-1] == "ml_optional_parked"
    spine = root / DISTILL["ontology_spine"]["retrieve_yaml"]
    assert spine.is_file()


def test_gen02_sources_do_not_import_vendor_sdks():
    forbidden = (
        "dbgpt",
        "db_gpt",
        "mybot",
        "n8n",
        "openwillow",
        "guaca",
        "rakazo",
        "semantica",
        "graphiti",
        "mem0",
        "deepagents",
        "langchain",
        "langgraph",
    )
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "packages/executor/dms_executor/generative_ask.py",
        root / "packages/executor/dms_executor/semantic_retrieve.py",
        root / "packages/cortex_client/cortex_client/compute.py",
        root / "scripts/score_curated.py",
    ]
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            lower = stripped.lower()
            for bad in forbidden:
                assert bad not in lower, f"{path}: {stripped}"


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


def test_cf1010_is_not_iap_or_grant():
    assert is_cf1010(403, "error code: 1010")
    assert is_cf1010(403, "<h1>Error 1010</h1>")
    assert not is_cf1010(403, "Cloudflare Access login")
    assert not is_cf1010(200, "error code: 1010")
    detail = cf1010_blocked_detail(403, "error code: 1010")
    assert detail is not None
    assert "CF1010" in detail
    assert "not IAP" in detail
    assert "DevOps" in detail
    assert cf1010_blocked_detail(403, "Cloudflare Access login") is None

    class _Resp:
        status_code = 403
        text = "error code: 1010"

    class _Exc(Exception):
        response = _Resp()

    assert ask_error_envelope(_Exc()) is None


def test_score_curated_has_no_urllib_probe():
    src = (Path(__file__).resolve().parents[1] / "scripts" / "score_curated.py").read_text(
        encoding="utf-8"
    )
    assert "urllib.request" not in src
    assert "urllib.error" not in src
    assert "urlopen" not in src
    assert "def score_http" in src
    assert "httpx.request" in src


def test_probe_climb_host_uses_score_http_not_urllib(monkeypatch):
    calls: list[tuple[str, str]] = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = json.dumps(
            {"product": "dms", "ask_mode": "live", "demo_fallback": False}
        )

    def fake_http(method: str, url: str, **_kw):
        calls.append((method, url))
        return _Resp()

    def boom(*_a, **_k):
        raise AssertionError("urllib must not probe studio.netie.ai")

    monkeypatch.setattr("score_curated.score_http", fake_http)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    kind, detail = probe_climb_host(PLATFORM_API, 5.0)
    assert kind == "ok"
    assert "dms" in detail
    assert calls == [("GET", f"{PLATFORM_API}/health")]


def test_probe_cf1010_is_blocked_devops_not_iap(monkeypatch):
    class _Resp:
        status_code = 403
        headers = {"content-type": "text/html"}
        text = "error code: 1010"

    monkeypatch.setattr("score_curated.score_http", lambda *_a, **_k: _Resp())
    kind, detail = probe_climb_host(PLATFORM_API, 5.0)
    assert kind == "blocked"
    assert "CF1010" in detail
    assert "IAP/auth" not in detail
    assert "DevOps" in detail


def test_climb_ab_cf1010_is_blocked_not_a_score(monkeypatch):
    def fake_probe(_url: str, _timeout: float) -> tuple[str, str]:
        return "blocked", "CF1010 Cloudflare browser-signature ban (not IAP)."

    monkeypatch.setattr("score_curated.probe_climb_host", fake_probe)
    assert climb_ab_live(PLATFORM_API, 1.0) == EXIT_BLOCKED


def test_ask_posts_via_score_http(monkeypatch):
    from score_curated import _ask

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"badge": "ABSTAIN", "abstained": True, "rows": []}

    calls: list[tuple[str, str, object]] = []

    def fake(method: str, url: str, **kw):
        calls.append((method, url, kw.get("json_body")))
        return _Resp()

    monkeypatch.setattr("score_curated.score_http", fake)
    env = _ask(PLATFORM_API, "q", "space", 1.0, ask_path="generative")
    assert env["badge"] == "ABSTAIN"
    assert calls[0][0] == "POST"
    assert calls[0][1] == f"{PLATFORM_API}/v1/chat/ask"
    assert calls[0][2] == {
        "question": "q",
        "space_id": "space",
        "ask_path": "generative",
    }


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
    distill = report["distill"]
    assert distill["ideas_only"] is True
    assert distill["text2sql"]["vendor_sdk"] is False
    assert distill["ladder"][0] == "certified_first_then_generative"
    assert distill["baseline_ab"]["exact_coverage_answered_pct"] == 38.46
    assert distill["baseline_ab"]["generative_coverage_answered_pct"] == 3.85
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
