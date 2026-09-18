"""Genie-bar instrument: curated CEO questions through POST /v1/chat/ask.

Same two numbers as score_answers.py (F26). This pack is the Databricks
walkthrough analog: exact certified questions a manager can click, plus
traps that must abstain. It does not start EPIC-019 (no new VQ repo).

  python scripts/score_curated.py --self-check
  python scripts/score_curated.py --live
  python scripts/score_curated.py --ab
  python scripts/score_curated.py --climb --url https://studio.netie.ai/api
  python scripts/score_curated.py --climb --ab --url https://studio.netie.ai/api
  python scripts/score_curated.py --prove-path
  python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
DEFAULT_ORACLES = ROOT / "tests" / "fixtures" / "curated_ceo" / "oracles.yaml"
DEFAULT_URL = "http://127.0.0.1:8090"
# Documented Platform target for GEN-02. Not a silent default (fail closed).
PLATFORM_API = "https://studio.netie.ai/api"

CONFIDENT = frozenset(
    {"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS"}
)
REFUSE = frozenset({"abstain", "refuse", "trap"})
# HTTP refusals from Space grants / manifest. Not a transport outage.
GRANT_REFUSAL_STATUS = frozenset({403, 409})
EXACT_ROUTES = frozenset({"governed_metric", "verified_query"})
GENERATIVE_ROUTES = frozenset({"generated"})
PLAN_SOURCES = frozenset({"ontology_plan", "bind_plan", "other"})
HOLD_MAY_CLEAR_FIELD = "Phase A HOLD may clear"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3

# Live curated_ceo @ 91c5cc99 (VQ-04 refuse traps). Frozen measurement, not a target.
BASELINE_91C5CC99: dict[str, Any] = {
    "commit": "91c5cc99",
    "n": 26,
    "ok": 7,
    "layer": 10,
    "abstain": 9,
    "wrong": 0,
}
# Isolated A/B @ a9578348 (GEN-01, Platform prove). Frozen, not a slogan.
# 10/26 = 38.46 pct, 1/26 = 3.85 pct. Do not edit to invent a rise.
BASELINE_AB_A9578348: dict[str, Any] = {
    "commit": "a9578348",
    "n": 26,
    "exact_answered": 10,
    "generative_answered": 1,
    "exact_coverage_answered_pct": 38.46,
    "generative_coverage_answered_pct": 3.85,
    "wrong": 0,
}

# QUALIFIED 15/26 (57.69 pct) gen answered on this pack. Not proven ontology_plan.
# Not COMPLETE. Prove-path labels the producer; Platform owns live counts.
QUALIFIED_GEN_COVERAGE_CLAIM: dict[str, Any] = {
    "pack": "curated_ceo",
    "n": 26,
    "status": "QUALIFIED",
    "offline_ab_answered": 15,
    "offline_ab_answered_pct": 57.69,
    "note": "15/26 gen answered is QUALIFIED pending plan_source prove",
}

# Platform D distill -> Netie-native mapping. Ideas only; no vendor paste.
DISTILL: dict[str, Any] = {
    "ideas_only": True,
    "vendors_not_pasted": (
        "DB-GPT",
        "mybot",
        "n8n",
        "OpenWillow",
        "guaca",
        "rakazo",
        "Semantica",
        "Graphiti",
        "Mem0",
        "DeepAgents",
    ),
    "ladder": (
        "certified_first_then_generative",
        "ontology_spine_demo_ontology",
        "hybrid_fuse_and_crag_grades",
        "text2sql_cortex_compute_plus_bind_plan",
    ),
    "ontology_spine": {
        "kind": "demo_ontology",
        "source": "packages/executor/dms_executor/ontology.py",
        "yaml_pack_format": False,
        "from_manifest": "extract path, not certified SQL",
        "retrieve_yaml": "packages/executor/dms_executor/ontology_spine.yaml",
    },
    "text2sql": {
        "cortex": "POST /v1/insights generate=true ask=false (ontology_plan)",
        "fallback": "POST /dms/query typed query_plan",
        "slots": "bind_plan typed query_plan on compute miss (isolated gen only)",
        "vendor_sdk": False,
    },
    "try_harder": (
        "retrieve then Cortex compute then bind_plan then compile then "
        "execute-validate; abstain only after that attempt (keep_gt gate). "
        "ML route/train/apply not this slice. No LangChain/LangGraph."
    ),
    "founder_lock": (
        "semantic_retrieve",
        "ontology_relations",
        "generate_sql",
        "execute_validate",
        "ml_optional_parked",
    ),
}


def distill_block() -> dict[str, Any]:
    """Harness record of Platform D mapping. Not a coverage number."""
    return {
        "ideas_only": DISTILL["ideas_only"],
        "vendors_not_pasted": list(DISTILL["vendors_not_pasted"]),
        "ladder": list(DISTILL["ladder"]),
        "ontology_spine": dict(DISTILL["ontology_spine"]),
        "text2sql": dict(DISTILL["text2sql"]),
        "try_harder": DISTILL["try_harder"],
        "founder_lock": list(DISTILL["founder_lock"]),
        "baseline_ab": {
            "commit": BASELINE_AB_A9578348["commit"],
            "n": BASELINE_AB_A9578348["n"],
            "exact_coverage_answered_pct": BASELINE_AB_A9578348[
                "exact_coverage_answered_pct"
            ],
            "generative_coverage_answered_pct": BASELINE_AB_A9578348[
                "generative_coverage_answered_pct"
            ],
            "wrong": BASELINE_AB_A9578348["wrong"],
        },
    }


def load_pack(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    questions = list(data.get("questions") or [])
    spaces = dict(data.get("spaces") or {})
    if not questions:
        raise SystemExit(f"no questions in {path}")
    return {"questions": questions, "spaces": spaces}


def load_oracles(path: Path = DEFAULT_ORACLES) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("oracles") or {}
    if not isinstance(rows, dict):
        raise SystemExit(f"oracles must be a mapping in {path}")
    return rows


def resolve_space(case: dict[str, Any], spaces: dict[str, Any]) -> str:
    alias = str(case.get("space") or "finance")
    if alias in spaces:
        return str(spaces[alias])
    return alias


def is_confident(env: dict[str, Any]) -> bool:
    badge = str(env.get("badge") or "")
    if env.get("abstained") or badge == "ABSTAIN":
        return False
    return badge in CONFIDENT


def is_cf1010(status: int, text: str | None) -> bool:
    """Cloudflare 1010 is a browser-signature ban, not a DMS grant 403."""
    if int(status) != 403:
        return False
    blob = (text or "").lower()
    return "error code: 1010" in blob or "error 1010" in blob


def cf1010_blocked_detail(status: int, text: str | None) -> str | None:
    if not is_cf1010(status, text):
        return None
    return (
        "CF1010 Cloudflare browser-signature ban (not IAP). "
        "Use httpx/curl-class fetch, not urllib. "
        "If httpx still 1010: Platform DevOps exception on studio.netie.ai."
    )


def score_http(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float,
) -> Any:
    """httpx/curl-class fetch. urllib CF1010s studio.netie.ai (SCORE-CLIENT-01)."""
    import httpx

    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
    if json_body is not None:
        kwargs["json"] = json_body
    return httpx.request(method, url, **kwargs)


def ask_error_envelope(exc: BaseException) -> dict[str, Any] | None:
    """403/409 grant/session refusal is ABSTAIN, not a scorer WRONG."""
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    text = getattr(resp, "text", None) if resp is not None else None
    if status is not None and is_cf1010(int(status), text):
        return None
    if status in GRANT_REFUSAL_STATUS:
        return {"badge": "ABSTAIN", "abstained": True, "rows": []}
    return None


def judge(case: dict[str, Any], env: dict[str, Any]) -> str:
    """OK | ABSTAIN | LAYER | WRONG. WRONG is the only P0."""
    expect = str(case.get("expect") or "l0").lower()
    badge = str(env.get("badge") or "")
    rows = env.get("rows") or env.get("values") or []
    n = len(rows) if isinstance(rows, list) else 0
    min_rows = int(case.get("min_rows") or 0)
    confident = is_confident(env)

    if expect in REFUSE:
        return "WRONG" if confident else "ABSTAIN"

    if not confident:
        return "ABSTAIN"
    if min_rows and n < min_rows:
        return "WRONG"
    if expect == "l0" and not badge.startswith("L0"):
        return "LAYER"
    return "OK"


def judge_envelope(case: dict[str, Any], env: dict[str, Any]) -> str:
    """Live judge. Silent demo fallback is WRONG (lying 200), not OK."""
    if env.get("demo_fallback_used"):
        return "WRONG"
    return judge(case, env)


def classify_path(route: Any) -> str:
    """Attribution on the product path. Not a second pack."""
    r = str(route or "")
    if r in GENERATIVE_ROUTES:
        return "generative"
    if r in EXACT_ROUTES:
        return "exact_match"
    return "other"


def classify_plan_source(env: dict[str, Any] | None) -> str:
    """ontology_plan | bind_plan | other from envelope telemetry only.

    Do not infer from SQL, question text, or assumption strings. A missing
    field is other so an old host cannot be guessed into Cortex AI coverage.
    """
    if not isinstance(env, dict):
        return "other"
    raw = str(env.get("plan_source") or "").strip().lower()
    return raw if raw in PLAN_SOURCES else "other"


def classify_crag(env: dict[str, Any]) -> str:
    """CRAG-style validate-or-abstain grade. Ideas-only; not a vendor clone.

    validated = gen execute after validate. abstain_validate = EXPLAIN/grant/hostile
    fail. abstain_gate = unsure/uncertified. skipped = exact-match or miss.
    """
    notes = " ".join(str(x) for x in (env.get("assumptions") or []))
    route = str(env.get("route") or "")
    abstained = bool(env.get("abstained") or env.get("badge") == "ABSTAIN")
    if route == "generated" and not abstained:
        return "validated"
    if abstained and "validate:" in notes:
        return "abstain_validate"
    if abstained and (
        "unsure" in notes
        or "uncertified" in notes
        or "too vague" in notes
        or "query_plan was not typed" in notes
    ):
        return "abstain_gate"
    if abstained:
        return "abstain"
    return "skipped"


def answered_vs_baseline(measured: int, baseline: int) -> str:
    if measured > baseline:
        return "rose"
    if measured < baseline:
        return "fell"
    return "flat"


def baseline_answered() -> int:
    return int(BASELINE_91C5CC99["ok"]) + int(BASELINE_91C5CC99["layer"])


def climb_url(url: str | None, env: dict[str, str] | None = None) -> str | None:
    """Fail closed: no laptop default. Platform sets --url or DMS_API_BASE."""
    raw = (url or "").strip()
    if raw:
        return raw.rstrip("/")
    bag = env if env is not None else os.environ
    for key in ("DMS_API_BASE", "STUDIO_API_BASE", "DMS_URL"):
        val = str(bag.get(key) or "").strip()
        if val:
            return val.rstrip("/")
    return None


def _ask(
    base: str,
    question: str,
    space_id: str,
    timeout: float,
    ask_path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"question": question, "space_id": space_id}
    if ask_path:
        payload["ask_path"] = ask_path
    resp = score_http(
        "POST",
        f"{base.rstrip('/')}/v1/chat/ask",
        json_body=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("ask response is not an object")
    return body


def self_check() -> int:
    pack = load_pack(DEFAULT_PACK)
    ids = [c["id"] for c in pack["questions"]]
    if len(ids) != len(set(ids)):
        print("FAIL: duplicate ids")
        return 1
    if len(ids) < 24:
        print(f"FAIL: Genie walkthrough needs >= 24 cases, got {len(ids)}")
        return 1
    expects = {str(c.get("expect") or "").lower() for c in pack["questions"]}
    if "l0" not in expects or not (expects & REFUSE):
        print("FAIL: pack must include l0 hits and abstain/refuse traps")
        return 1
    oracles = load_oracles()
    missing_sql = [
        str(c["id"])
        for c in pack["questions"]
        if str(c.get("expect") or "").lower() == "l0"
        and not str((oracles.get(c["id"]) or {}).get("sql") or "").strip()
    ]
    if missing_sql:
        print(f"FAIL: expect:l0 without Cortex SQL in oracles.yaml: {missing_sql}")
        return 1
    planted_ok = judge(
        {"expect": "l0", "min_rows": 1},
        {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"x": 1}]},
    )
    planted_wrong = judge(
        {"expect": "abstain"},
        {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"x": 1}]},
    )
    planted_refuse = judge(
        {"expect": "refuse"},
        {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"x": 1}]},
    )
    planted_abs = judge(
        {"expect": "l0"},
        {"badge": "ABSTAIN", "abstained": True, "rows": []},
    )
    if (
        planted_ok != "OK"
        or planted_wrong != "WRONG"
        or planted_refuse != "WRONG"
        or planted_abs != "ABSTAIN"
    ):
        print("FAIL: judge plant")
        return 1
    planted_demo = judge_envelope(
        {"expect": "l0", "min_rows": 1},
        {
            "badge": "L0_CERTIFIED",
            "abstained": False,
            "rows": [{"x": 1}],
            "demo_fallback_used": True,
        },
    )
    if planted_demo != "WRONG":
        print("FAIL: demo fallback must not score OK")
        return 1
    b = BASELINE_91C5CC99
    if (
        int(b["ok"]) + int(b["layer"]) + int(b["abstain"]) + int(b["wrong"]) != int(b["n"])
        or int(b["wrong"]) != 0
        or int(b["n"]) != len(ids)
    ):
        print("FAIL: baseline @ 91c5cc99 does not match pack / WRONG=0")
        return 1
    if climb_url(None, {}) is not None:
        print("FAIL: climb url must not default")
        return 1
    if climb_url(None, {"DMS_API_BASE": PLATFORM_API}) != PLATFORM_API:
        print("FAIL: climb url from DMS_API_BASE")
        return 1
    if classify_health(403, None, "text/html")[0] != "blocked":
        print("FAIL: IAP 403 must be BLOCKED, not a score")
        return 1
    if not is_cf1010(403, "error code: 1010"):
        print("FAIL: CF1010 plant")
        return 1
    if is_cf1010(403, "Cloudflare Access login"):
        print("FAIL: IAP HTML is not CF1010")
        return 1
    ab = BASELINE_AB_A9578348
    if (
        int(ab["exact_answered"]) + int(ab["generative_answered"]) < 1
        or int(ab["wrong"]) != 0
        or int(ab["n"]) != len(ids)
        or int(ab["exact_answered"]) != 10
        or int(ab["generative_answered"]) != 1
        or float(ab["exact_coverage_answered_pct"]) != 38.46
        or float(ab["generative_coverage_answered_pct"]) != 3.85
        or round(100.0 * int(ab["exact_answered"]) / int(ab["n"]), 2) != 38.46
        or round(100.0 * int(ab["generative_answered"]) / int(ab["n"]), 2) != 3.85
    ):
        print("FAIL: A/B baseline @ a9578348 drifted")
        return 1
    if (
        DISTILL["ideas_only"] is not True
        or DISTILL["text2sql"]["vendor_sdk"] is not False
        or DISTILL["ontology_spine"]["yaml_pack_format"] is not False
        or DISTILL["ladder"][0] != "certified_first_then_generative"
        or list(DISTILL["founder_lock"])[:4]
        != [
            "semantic_retrieve",
            "ontology_relations",
            "generate_sql",
            "execute_validate",
        ]
    ):
        print("FAIL: distill constraints drifted")
        return 1
    spine = ROOT / str(DISTILL["ontology_spine"]["retrieve_yaml"])
    if not spine.is_file():
        print("FAIL: ontology spine yaml missing")
        return 1
    if classify_crag(
        {
            "route": "generated",
            "abstained": False,
            "badge": "L2_VALIDATED",
            "assumptions": ["executed via Cortex submit after validate"],
        }
    ) != "validated":
        print("FAIL: CRAG validated plant")
        return 1
    if classify_crag(
        {
            "route": "generated",
            "abstained": True,
            "badge": "ABSTAIN",
            "assumptions": ["GEN-01: validate:explain"],
        }
    ) != "abstain_validate":
        print("FAIL: CRAG validate-or-abstain plant")
        return 1
    fake_t = {"OK": 8, "LAYER": 10, "ABSTAIN": 8, "WRONG": 0}
    fake_cases = [
        {
            "id": "cq_x",
            "verdict": "LAYER",
            "route": "generated",
            "path": "generative",
            "expect": "l0",
        }
    ]
    report = build_climb_report(fake_t, cases=fake_cases, url=PLATFORM_API)
    blob = json.dumps(report)
    if "99.95" in blob or "COMPLETE" in blob:
        print("FAIL: climb report invented COMPLETE / 99.95")
        return 1
    if report["answered_vs_baseline"] != "rose" or report["passed_wrong_zero"] is not True:
        print("FAIL: climb delta plant")
        return 1
    if report.get("claim") not in (None, "measured"):
        print("FAIL: climb claim must stay measured")
        return 1
    if classify_plan_source(
        {
            "assumptions": ["compute_fallback:bind_plan"],
            "route": "generated",
            "sql_used": "SELECT 1",
        }
    ) != "other":
        print("FAIL: plan_source must not be guessed from assumptions")
        return 1
    if classify_plan_source({"plan_source": "ontology_plan"}) != "ontology_plan":
        print("FAIL: plan_source ontology_plan plant")
        return 1
    if classify_plan_source({"plan_source": "bind_plan"}) != "bind_plan":
        print("FAIL: plan_source bind_plan plant")
        return 1
    insights_plant = build_gen_path_prove_report(
        {"OK": 1, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0},
        cases=[{"id": "cq_sku_count", "verdict": "OK", "plan_source": "ontology_plan"}],
        mode="offline",
    )
    if int(insights_plant["by_plan_source"]["ontology_plan"]["answered"]) < 1:
        print("FAIL: prove must count ontology_plan>=1 when Insights path works")
        return 1
    if "COMPLETE" in json.dumps(insights_plant):
        print("FAIL: insights plant invented COMPLETE")
        return 1
    yes_cases = (
        [{"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"} for i in range(14)]
        + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(12)]
    )
    yes_t = {"OK": 14, "LAYER": 0, "ABSTAIN": 12, "WRONG": 0}
    yes = build_gen_path_prove_report(
        tallies=yes_t, cases=yes_cases, mode="offline"
    )
    yes_blob = json.dumps(yes)
    if "COMPLETE" in yes_blob or "99.95" in yes_blob or "DB-GPT-class" in yes_blob:
        print("FAIL: prove report invented COMPLETE / 99.95")
        return 1
    if yes[HOLD_MAY_CLEAR_FIELD] != "YES" or yes["phase_a_hold_may_clear"] != "YES":
        print("FAIL: majority ontology_plan WRONG=0 should be YES")
        return 1
    no_bind = build_gen_path_prove_report(
        tallies={"OK": 15, "LAYER": 0, "ABSTAIN": 11, "WRONG": 0},
        cases=(
            [{"id": f"q{i}", "verdict": "OK", "plan_source": "bind_plan"} for i in range(15)]
            + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(11)]
        ),
        mode="offline",
    )
    if no_bind[HOLD_MAY_CLEAR_FIELD] != "NO":
        print("FAIL: bind_plan majority must be NO")
        return 1
    no_wrong = build_gen_path_prove_report(
        tallies={"OK": 14, "LAYER": 0, "ABSTAIN": 11, "WRONG": 1},
        cases=(
            [{"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"} for i in range(14)]
            + [{"id": "w", "verdict": "WRONG", "plan_source": "ontology_plan"}]
            + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(11)]
        ),
        mode="offline",
    )
    if no_wrong[HOLD_MAY_CLEAR_FIELD] != "NO":
        print("FAIL: WRONG>0 must be NO even if ontology_plan majority")
        return 1
    qclaim = QUALIFIED_GEN_COVERAGE_CLAIM
    if (
        qclaim["status"] != "QUALIFIED"
        or int(qclaim["n"]) != len(ids)
        or int(qclaim["offline_ab_answered"]) != 15
        or float(qclaim["offline_ab_answered_pct"]) != 57.69
        or round(100.0 * 15 / 26, 2) != 57.69
    ):
        print("FAIL: QUALIFIED 15/26 pack identity drifted")
        return 1
    print(f"PASS: curated pack {len(ids)} cases, judge fail-closed on green trap")
    return 0


def score_pack_live(
    url: str,
    timeout: float,
    ask_path: str | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    pack = load_pack(DEFAULT_PACK)
    tallies = _tally()
    cases_out: list[dict[str, Any]] = []
    for case in pack["questions"]:
        qid = str(case["id"])
        space = resolve_space(case, pack["spaces"])
        err = ""
        try:
            env = _ask(url, str(case["question"]), space, timeout, ask_path=ask_path)
        except Exception as exc:  # noqa: BLE001
            env = ask_error_envelope(exc)
            if env is None:
                err = f"{type(exc).__name__}: {exc}"
                print(f"{qid}\tERROR\t{err}")
                tallies["WRONG"] += 1
                cases_out.append(
                    {
                        "id": qid,
                        "verdict": "WRONG",
                        "badge": None,
                        "route": None,
                        "path": "other",
                        "crag": "skipped",
                        "plan_source": "other",
                        "rows": 0,
                        "expect": case.get("expect"),
                        "error": err,
                    }
                )
                continue
            print(f"{qid}\tGRANT_REFUSE\t{type(exc).__name__}: {exc}")
        verdict = judge_envelope(case, env)
        tallies[verdict] += 1
        badge = env.get("badge")
        route = env.get("route")
        path = classify_path(route)
        crag = classify_crag(env)
        plan_source = classify_plan_source(env)
        n = len(env.get("rows") or [])
        print(
            f"{qid}\t{verdict}\t{badge}\troute={route}\tpath={path}\t"
            f"plan_source={plan_source}\tcrag={crag}"
            f"\trows={n}\texpect={case['expect']}"
        )
        cases_out.append(
            {
                "id": qid,
                "verdict": verdict,
                "badge": badge,
                "route": route,
                "path": path,
                "plan_source": plan_source,
                "crag": crag,
                "rows": n,
                "expect": case.get("expect"),
                "demo_fallback_used": bool(env.get("demo_fallback_used")),
            }
        )
    return tallies, cases_out


def live(url: str, timeout: float) -> int:
    tallies, _cases = score_pack_live(url, timeout)
    n = sum(tallies.values())
    wrong = tallies["WRONG"]
    answered_ok = tallies["OK"] + tallies["LAYER"]
    precision = 100.0 if answered_ok + wrong == 0 else (
        100.0 * answered_ok / (answered_ok + wrong)
    )
    print(
        f"precision-on-answered {precision:.2f} pct  "
        f"coverage {tallies['OK']}/{n}  "
        f"WRONG {wrong}  abstain {tallies['ABSTAIN']}  layer {tallies['LAYER']}"
    )
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    (art / "score_curated.json").write_text(
        json.dumps(
            {
                "kind": "dms.score_curated",
                "pack": "curated_ceo",
                "precision_on_answered": round(precision, 2),
                "coverage_pct": round(100.0 * tallies["OK"] / n, 2) if n else 0.0,
                "correct": tallies["OK"],
                "answered": tallies["OK"] + tallies["LAYER"],
                "wrong": wrong,
                "total": n,
                "abstained": tallies["ABSTAIN"],
                "passed": wrong == 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if wrong:
        print("FAIL: confidently wrong or transport error")
        return EXIT_FAIL
    print("PASS: 0 WRONG")
    return EXIT_PASS


def _ab_miss() -> dict[str, Any]:
    return {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "path miss"}


def _ab_seed(path: Path) -> Any:
    """Demo warehouse + demo_ontology for the generative A/B lane.

    Not a certified-pack expand. Same lake the exact-match pack names.
    """
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.generative_ask import load_verified_ontology
    from dms_executor.ontology import demo_ontology

    ensure_demo_warehouse(path)
    loaded = load_verified_ontology(path, demo_ontology(path))
    if loaded is None:
        raise RuntimeError("A/B demo ontology failed verify")
    return loaded


def _tally() -> dict[str, int]:
    return {"OK": 0, "ABSTAIN": 0, "LAYER": 0, "WRONG": 0}


def _path_report(name: str, tallies: dict[str, int], n: int) -> dict[str, Any]:
    wrong = tallies["WRONG"]
    answered = tallies["OK"] + tallies["LAYER"]
    return {
        "path": name,
        "n": n,
        "ok": tallies["OK"],
        "layer": tallies["LAYER"],
        "abstain": tallies["ABSTAIN"],
        "wrong": wrong,
        "answered": answered,
        "coverage_answered_pct": round(100.0 * answered / n, 2) if n else 0.0,
    }


def run_ab_curated(pack_path: Path = DEFAULT_PACK) -> dict[str, Any]:
    """Offline A/B: exact-match pack vs retrieve+bind generative on the same pack.

    Fake submit/ledger so CI has no keys. Does not expand certified packs.
    """
    import tempfile
    from types import SimpleNamespace

    from dms_executor.demo_grants import DEMO_SPACE_GRANTS, canonical_space_id
    from dms_executor.demo_pack import maybe_pack_ask, maybe_uncertified_refuse_ask
    from dms_executor.generative_ask import maybe_generative_ask
    from dms_executor.semantic_retrieve import bind_plan

    pack = load_pack(pack_path)
    tmp = Path(tempfile.mkdtemp()) / "ab_gen01.duckdb"
    onto = _ab_seed(tmp)
    def submit(sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(tmp)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            out = [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:  # noqa: BLE001 -- execute-validate fail is an abstain
            return SimpleNamespace(ok=False, status="err", run_id="run_ab", output=None)
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_ab", output={"rows": out})

    def ledger(_payload: dict[str, Any]) -> Any:
        return SimpleNamespace(entry_id="led_ab", hash="hash_ab_not_entry")

    exact_t = _tally()
    gen_t = _tally()
    cases_out: list[dict[str, Any]] = []
    for case in pack["questions"]:
        q = str(case["question"])
        space = resolve_space(case, pack["spaces"])
        entry = DEMO_SPACE_GRANTS.get(canonical_space_id(space))
        grants = set(entry[1]) if entry else set()
        exact_env = maybe_uncertified_refuse_ask(q, space_id=space) or maybe_pack_ask(
            q,
            space_id=space,
            grantable=grants,
            submit=submit,
            ledger_append=ledger,
        )
        exact_env = exact_env if exact_env is not None else _ab_miss()
        gen_env = maybe_generative_ask(
            q,
            space_id=space,
            warehouse=tmp,
            grantable=grants,
            compute=lambda ctx, _q=q: bind_plan(_q, ctx),
            submit=submit,
            ledger_append=ledger,
            ontology=onto,
        )
        gen_env = gen_env if gen_env is not None else _ab_miss()
        ev = judge(case, exact_env)
        gv = judge(case, gen_env)
        exact_t[ev] += 1
        gen_t[gv] += 1
        cases_out.append(
            {
                "id": case["id"],
                "expect": case.get("expect"),
                "exact": ev,
                "generative": gv,
                "exact_badge": exact_env.get("badge"),
                "generative_badge": gen_env.get("badge"),
                "plan_source": classify_plan_source(gen_env),
                "crag": classify_crag(gen_env),
            }
        )
    n = len(pack["questions"])
    exact_r = _path_report("exact_match", exact_t, n)
    gen_r = _path_report("generative_semantic", gen_t, n)
    crag_counts: dict[str, int] = {}
    for row in cases_out:
        key = str(row.get("crag") or "skipped")
        crag_counts[key] = crag_counts.get(key, 0) + 1
    base = BASELINE_AB_A9578348
    return {
        "kind": "dms.ab_gen01",
        "pack": "curated_ceo",
        "claim": "measured",
        "exact_match": exact_r,
        "generative": gen_r,
        "crag": crag_counts,
        "baseline_ab": {
            "commit": base["commit"],
            "exact_answered": base["exact_answered"],
            "generative_answered": base["generative_answered"],
            "exact_coverage_answered_pct": base["exact_coverage_answered_pct"],
            "generative_coverage_answered_pct": base["generative_coverage_answered_pct"],
            "n": base["n"],
            "wrong": base["wrong"],
        },
        "distill": distill_block(),
        "generative_vs_baseline": answered_vs_baseline(
            gen_r["answered"], int(base["generative_answered"])
        ),
        "wrong": exact_r["wrong"] + gen_r["wrong"],
        "passed": exact_r["wrong"] == 0 and gen_r["wrong"] == 0,
        "cases": cases_out,
    }


def ab_offline() -> int:
    report = run_ab_curated()
    exact = report["exact_match"]
    gen = report["generative"]
    base = report["baseline_ab"]
    print(
        f"{'path':<22} n ok layer abstain wrong answered coverage_answered"
    )
    for row in (exact, gen):
        print(
            f"{row['path']:<22} {row['n']} {row['ok']} {row['layer']} "
            f"{row['abstain']} {row['wrong']} {row['answered']} "
            f"{row['coverage_answered_pct']:.2f} pct"
        )
    print(
        f"baseline @ {base['commit']}: exact_answered={base['exact_answered']} "
        f"generative_answered={base['generative_answered']} WRONG={base['wrong']}"
    )
    print(
        f"generative vs baseline: {report['generative_vs_baseline']}  "
        f"crag={report['crag']}"
    )
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in report.items() if k != "cases"}
    blob = json.dumps(slim, indent=2)
    if "99.95" in blob or "COMPLETE" in blob:
        print("FAIL: A/B report invented COMPLETE / 99.95")
        return EXIT_FAIL
    (art / "ab_gen01.json").write_text(blob + "\n", encoding="utf-8")
    if not report["passed"]:
        print("FAIL: A/B WRONG>0")
        return EXIT_FAIL
    print("PASS: A/B WRONG=0 on both paths (not EPIC-019 COMPLETE)")
    return EXIT_PASS


def _answered_by_path(cases: list[dict[str, Any]]) -> dict[str, int]:
    out = {"exact_match": 0, "generative": 0, "other": 0}
    for row in cases:
        if row.get("verdict") not in {"OK", "LAYER"}:
            continue
        path = str(row.get("path") or "other")
        if path not in out:
            path = "other"
        out[path] += 1
    return out


def build_climb_report(
    tallies: dict[str, int],
    *,
    cases: list[dict[str, Any]],
    url: str,
) -> dict[str, Any]:
    n = sum(tallies.values())
    wrong = tallies["WRONG"]
    answered = tallies["OK"] + tallies["LAYER"]
    base = BASELINE_91C5CC99
    base_ans = baseline_answered()
    vs = answered_vs_baseline(answered, base_ans)
    by_path = _answered_by_path(cases)
    return {
        "kind": "dms.score_climb",
        "ticket": "GEN-02",
        "issue": 180,
        "pack": "curated_ceo",
        "url": url,
        "claim": "measured",
        "baseline": {
            "commit": base["commit"],
            "n": base["n"],
            "ok": base["ok"],
            "layer": base["layer"],
            "abstain": base["abstain"],
            "wrong": base["wrong"],
            "answered": base_ans,
        },
        "measured": {
            "n": n,
            "ok": tallies["OK"],
            "layer": tallies["LAYER"],
            "abstain": tallies["ABSTAIN"],
            "wrong": wrong,
            "answered": answered,
            "coverage_ok_pct": round(100.0 * tallies["OK"] / n, 2) if n else 0.0,
            "coverage_answered_pct": round(100.0 * answered / n, 2) if n else 0.0,
        },
        "delta": {
            "ok": tallies["OK"] - int(base["ok"]),
            "layer": tallies["LAYER"] - int(base["layer"]),
            "abstain": tallies["ABSTAIN"] - int(base["abstain"]),
            "wrong": wrong - int(base["wrong"]),
            "answered": answered - base_ans,
        },
        "answered_by_path": by_path,
        "answered_vs_baseline": vs,
        "wrong": wrong,
        "passed_wrong_zero": wrong == 0,
        "distill": distill_block(),
        "cases": cases,
    }


def classify_health(
    status: int, body: dict[str, Any] | None, ctype: str | None
) -> tuple[str, str]:
    """ok | blocked | fail. IAP/auth is BLOCKED, not a 26-WRONG score."""
    if status in {401, 403}:
        return "blocked", f"health status={status} (IAP/auth). Not a score."
    if status != 200:
        return "fail", f"health status={status}"
    if body is None:
        kind = (ctype or "").split(";")[0].strip().lower()
        if kind == "text/html":
            return "fail", "health is HTML (SPA /health? use /api)"
        return "fail", "health is not JSON"
    if body.get("demo_fallback") is True:
        return "fail", "demo_fallback=true (lying affordance)"
    if str(body.get("ask_mode") or "") == "demo":
        return "fail", "ask_mode=demo"
    return "ok", f"product={body.get('product')} ask_mode={body.get('ask_mode')}"


def probe_climb_host(url: str, timeout: float) -> tuple[str, str]:
    """ok | blocked | fail. Health only (httpx). Does not invent a score."""
    try:
        import httpx
    except ImportError:
        return "blocked", "httpx required (DMS .venv). Not a score."

    health = f"{url.rstrip('/')}/health"
    try:
        resp = score_http("GET", health, timeout=min(timeout, 15.0))
    except httpx.HTTPError as exc:
        return "blocked", f"{type(exc).__name__}: {exc}"
    status = int(resp.status_code)
    ctype = resp.headers.get("content-type") if resp.headers is not None else None
    text = str(getattr(resp, "text", "") or "")[:8000]
    cf = cf1010_blocked_detail(status, text)
    if cf:
        return "blocked", cf
    try:
        parsed = json.loads(text)
    except ValueError:
        return classify_health(status, None, ctype)
    body = parsed if isinstance(parsed, dict) else None
    return classify_health(status, body, ctype)


def climb(url: str, timeout: float) -> int:
    kind, detail = probe_climb_host(url, timeout)
    print(f"GEN-02 climb host {url}  [{kind}] {detail}")
    if kind == "blocked":
        print("BLOCKED: cannot reach host. Not a score. Not COMPLETE.")
        return EXIT_BLOCKED
    if kind == "fail":
        print("FAIL: host is not a live governed ask")
        return EXIT_FAIL
    try:
        tallies, cases = score_pack_live(url, timeout)
    except ImportError:
        print("CONFIG: httpx required (DMS .venv). Not a score.")
        return EXIT_CONFIG
    report = build_climb_report(tallies, cases=cases, url=url)
    measured = report["measured"]
    base = report["baseline"]
    delta = report["delta"]
    by_path = report["answered_by_path"]
    print(f"{'':<10} {'n':>3} {'ok':>3} {'layer':>5} {'abstain':>7} {'wrong':>5} {'answered':>8}")
    print(
        f"{'baseline':<10} {base['n']:>3} {base['ok']:>3} {base['layer']:>5} "
        f"{base['abstain']:>7} {base['wrong']:>5} {base['answered']:>8}  @ {base['commit']}"
    )
    print(
        f"{'measured':<10} {measured['n']:>3} {measured['ok']:>3} {measured['layer']:>5} "
        f"{measured['abstain']:>7} {measured['wrong']:>5} {measured['answered']:>8}"
    )
    print(
        f"{'delta':<10} {'':>3} {delta['ok']:>+3} {delta['layer']:>+5} "
        f"{delta['abstain']:>+7} {delta['wrong']:>+5} {delta['answered']:>+8}"
    )
    print(
        f"answered_by_path exact_match={by_path['exact_match']} "
        f"generative={by_path['generative']} other={by_path['other']}"
    )
    print(f"answered vs baseline @ {base['commit']}: {report['answered_vs_baseline']}")
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in report.items() if k != "cases"}
    (art / "score_climb.json").write_text(
        json.dumps(slim, indent=2) + "\n", encoding="utf-8"
    )
    (art / "score_climb_cases.json").write_text(
        json.dumps(report["cases"], indent=2) + "\n", encoding="utf-8"
    )
    if not report["passed_wrong_zero"]:
        print("FAIL: WRONG>0 (law). Not COMPLETE.")
        return EXIT_FAIL
    print(
        "PASS: WRONG=0 measured. Climb is the answered delta, not a 99.95% claim. "
        "Not EPIC-019 COMPLETE."
    )
    return EXIT_PASS


def _crag_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in cases:
        key = str(row.get("crag") or "skipped")
        out[key] = out.get(key, 0) + 1
    return out


def climb_ab_live(url: str, timeout: float) -> int:
    """Live isolated A/B: ask_path=exact vs ask_path=generative. WRONG=0 law."""
    kind, detail = probe_climb_host(url, timeout)
    print(f"GEN-02 live A/B host {url}  [{kind}] {detail}")
    if kind == "blocked":
        print("BLOCKED: cannot reach host. Not a score. Not COMPLETE.")
        return EXIT_BLOCKED
    if kind == "fail":
        print("FAIL: host is not a live governed ask")
        return EXIT_FAIL
    print("-- ask_path=exact --")
    try:
        exact_t, exact_cases = score_pack_live(url, timeout, ask_path="exact")
        print("-- ask_path=generative --")
        gen_t, gen_cases = score_pack_live(url, timeout, ask_path="generative")
    except ImportError:
        print("CONFIG: httpx required (DMS .venv). Not a score.")
        return EXIT_CONFIG
    n = sum(exact_t.values())
    exact_r = _path_report("exact_match", exact_t, n)
    gen_r = _path_report("generative_semantic", gen_t, n)
    base = BASELINE_AB_A9578348
    crag = _crag_counts(gen_cases)
    vs = answered_vs_baseline(gen_r["answered"], int(base["generative_answered"]))
    report = {
        "kind": "dms.ab_live",
        "ticket": "GEN-02",
        "issue": 180,
        "pack": "curated_ceo",
        "url": url,
        "claim": "measured",
        "baseline_ab": {
            "commit": base["commit"],
            "exact_answered": base["exact_answered"],
            "generative_answered": base["generative_answered"],
            "exact_coverage_answered_pct": base["exact_coverage_answered_pct"],
            "generative_coverage_answered_pct": base["generative_coverage_answered_pct"],
            "n": base["n"],
            "wrong": base["wrong"],
        },
        "distill": distill_block(),
        "exact_match": exact_r,
        "generative": gen_r,
        "crag": crag,
        "generative_vs_baseline": vs,
        "wrong": exact_r["wrong"] + gen_r["wrong"],
        "passed_wrong_zero": exact_r["wrong"] == 0 and gen_r["wrong"] == 0,
    }
    blob = json.dumps(report, indent=2)
    if "99.95" in blob or "COMPLETE" in blob:
        print("FAIL: live A/B invented COMPLETE / 99.95")
        return EXIT_FAIL
    print(f"{'path':<22} n ok layer abstain wrong answered coverage_answered")
    for row in (exact_r, gen_r):
        print(
            f"{row['path']:<22} {row['n']} {row['ok']} {row['layer']} "
            f"{row['abstain']} {row['wrong']} {row['answered']} "
            f"{row['coverage_answered_pct']:.2f} pct"
        )
    print(
        f"baseline @ {base['commit']}: exact_answered={base['exact_answered']} "
        f"generative_answered={base['generative_answered']}"
    )
    print(f"generative vs baseline: {vs}  crag={crag}")
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    (art / "score_climb_ab.json").write_text(blob + "\n", encoding="utf-8")
    (art / "score_climb_ab_cases.json").write_text(
        json.dumps({"exact": exact_cases, "generative": gen_cases}, indent=2) + "\n",
        encoding="utf-8",
    )
    if not report["passed_wrong_zero"]:
        print("FAIL: WRONG>0 (law). Not COMPLETE.")
        return EXIT_FAIL
    print(
        "PASS: live A/B WRONG=0 measured. Climb is the gen answered delta, "
        "not a 99.95% claim. Not EPIC-019 COMPLETE."
    )
    return EXIT_PASS


def _plan_source_bucket(cases: list[dict[str, Any]]) -> dict[str, int]:
    out = {"ontology_plan": 0, "bind_plan": 0, "other": 0}
    for row in cases:
        if row.get("verdict") not in {"OK", "LAYER"}:
            continue
        src = str(row.get("plan_source") or "other")
        if src not in out:
            src = "other"
        out[src] += 1
    return out


def decide_phase_a_hold_may_clear(
    *,
    wrong: int,
    n: int,
    pack: str,
    ontology_answered: int,
    bind_answered: int,
    answered: int,
) -> tuple[str, str]:
    """YES only if majority answered path is proven Cortex ontology_plan.

    Does not stamp COMPLETE. Re-baselined packs stay NO for Decision.
    """
    if wrong:
        return "NO", "WRONG>0; HOLD stays"
    if pack != "curated_ceo" or int(n) != int(QUALIFIED_GEN_COVERAGE_CLAIM["n"]):
        return "NO", "pack re-baselined; Decision must accept leftover"
    if answered <= 0:
        return "NO", "zero answered; no majority ontology_plan"
    if ontology_answered * 2 <= answered:
        return (
            "NO",
            f"ontology_plan is not majority of answered "
            f"({ontology_answered}/{answered})",
        )
    if ontology_answered <= bind_answered:
        return (
            "NO",
            f"ontology_plan ({ontology_answered}) does not exceed "
            f"bind_plan ({bind_answered})",
        )
    return (
        "YES",
        f"majority answered path is ontology_plan "
        f"({ontology_answered}/{answered}) WRONG=0",
    )


def build_gen_path_prove_report(
    tallies: dict[str, int],
    *,
    cases: list[dict[str, Any]],
    mode: str,
    url: str | None = None,
    pack: str = "curated_ceo",
) -> dict[str, Any]:
    n = sum(int(v) for v in tallies.values()) or len(cases)
    wrong = int(tallies.get("WRONG") or 0)
    answered = int(tallies.get("OK") or 0) + int(tallies.get("LAYER") or 0)
    raw = _plan_source_bucket(cases)
    by_source: dict[str, Any] = {}
    for key, count in raw.items():
        by_source[key] = {
            "answered": count,
            "answered_pct": round(100.0 * count / answered, 2) if answered else 0.0,
            "pack_pct": round(100.0 * count / n, 2) if n else 0.0,
        }
    hold, reason = decide_phase_a_hold_may_clear(
        wrong=wrong,
        n=n,
        pack=pack,
        ontology_answered=raw["ontology_plan"],
        bind_answered=raw["bind_plan"],
        answered=answered,
    )
    return {
        "kind": "dms.gen_path_prove",
        "ticket": "GEN-PATH-PROVE-01",
        "issue": 199,
        "claim": "measured",
        "pack": pack,
        "mode": mode,
        "url": url,
        "qualified_claim": dict(QUALIFIED_GEN_COVERAGE_CLAIM),
        "n": n,
        "ok": int(tallies.get("OK") or 0),
        "layer": int(tallies.get("LAYER") or 0),
        "abstain": int(tallies.get("ABSTAIN") or 0),
        "wrong": wrong,
        "answered": answered,
        "passed_wrong_zero": wrong == 0,
        "by_plan_source": by_source,
        HOLD_MAY_CLEAR_FIELD: hold,
        "phase_a_hold_may_clear": hold,
        "phase_a_hold_may_clear_reason": reason,
        "cases": cases,
    }


def _write_prove_report(report: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    blob = json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2)
    if "COMPLETE" in blob or "99.95" in blob or "DB-GPT-class" in blob:
        print("FAIL: gen-path prove invented COMPLETE / 99.95")
        return EXIT_FAIL, report
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    (art / "score_gen_path_prove.json").write_text(blob + "\n", encoding="utf-8")
    (art / "score_gen_path_prove_cases.json").write_text(
        json.dumps(report["cases"], indent=2) + "\n", encoding="utf-8"
    )
    by = report["by_plan_source"]
    print(
        f"{'plan_source':<16} answered answered_pct pack_pct"
    )
    for key in ("ontology_plan", "bind_plan", "other"):
        row = by[key]
        print(
            f"{key:<16} {row['answered']} {row['answered_pct']:.2f} pct "
            f"{row['pack_pct']:.2f} pct"
        )
    print(
        f"WRONG {report['wrong']}  answered {report['answered']}/{report['n']}  "
        f"{HOLD_MAY_CLEAR_FIELD}: {report[HOLD_MAY_CLEAR_FIELD]}"
    )
    print(f"reason: {report['phase_a_hold_may_clear_reason']}")
    print("Harness only. Live counts are Platform. HOLD is not an epic stamp.")
    if not report["passed_wrong_zero"]:
        print("FAIL: WRONG>0 (law).")
        return EXIT_FAIL, report
    print("PASS: WRONG=0 measured plan_source labels.")
    return EXIT_PASS, report


def prove_path_offline() -> int:
    ab = run_ab_curated()
    cases = [
        {
            "id": row["id"],
            "verdict": row["generative"],
            "badge": row.get("generative_badge"),
            "plan_source": row.get("plan_source") or "other",
            "expect": row.get("expect"),
            "crag": row.get("crag"),
        }
        for row in ab["cases"]
    ]
    gen = ab["generative"]
    tallies = {
        "OK": int(gen["ok"]),
        "LAYER": int(gen["layer"]),
        "ABSTAIN": int(gen["abstain"]),
        "WRONG": int(gen["wrong"]),
    }
    report = build_gen_path_prove_report(
        tallies, cases=cases, mode="offline", pack=str(ab.get("pack") or "curated_ceo")
    )
    code, _ = _write_prove_report(report)
    if int(ab["wrong"]):
        print("FAIL: exact-match lane WRONG>0 on same pack")
        return EXIT_FAIL
    return code


def prove_path_live(url: str, timeout: float) -> int:
    kind, detail = probe_climb_host(url, timeout)
    print(f"GEN-PATH-PROVE-01 host {url}  [{kind}] {detail}")
    if kind == "blocked":
        print("BLOCKED: cannot reach host. Not a score.")
        return EXIT_BLOCKED
    if kind == "fail":
        print("FAIL: host is not a live governed ask")
        return EXIT_FAIL
    print("-- ask_path=generative (plan_source labels) --")
    try:
        gen_t, gen_cases = score_pack_live(url, timeout, ask_path="generative")
        print("-- ask_path=exact (WRONG=0 on same pack) --")
        exact_t, _exact_cases = score_pack_live(url, timeout, ask_path="exact")
    except ImportError:
        print("CONFIG: httpx required (DMS .venv). Not a score.")
        return EXIT_CONFIG
    report = build_gen_path_prove_report(
        gen_t, cases=gen_cases, mode="live", url=url
    )
    code, _ = _write_prove_report(report)
    if int(exact_t["WRONG"]):
        print("FAIL: exact-match lane WRONG>0 on same pack")
        return EXIT_FAIL
    return code


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--ab", action="store_true")
    p.add_argument("--climb", action="store_true")
    p.add_argument("--prove-path", action="store_true")
    p.add_argument("--url", default=None)
    p.add_argument("--timeout", type=float, default=120.0)
    args = p.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.prove_path:
        if args.climb:
            target = climb_url(args.url)
            if not target:
                print(
                    "CONFIG: --prove-path --climb needs --url or DMS_API_BASE "
                    f"(Platform: {PLATFORM_API}). No laptop default."
                )
                return EXIT_CONFIG
            return prove_path_live(target, args.timeout)
        target = climb_url(args.url)
        if target:
            return prove_path_live(target, args.timeout)
        return prove_path_offline()
    if args.climb:
        target = climb_url(args.url)
        if not target:
            print(
                "CONFIG: --climb needs --url or DMS_API_BASE "
                f"(Platform: {PLATFORM_API}). No laptop default."
            )
            return EXIT_CONFIG
        if args.ab:
            return climb_ab_live(target, args.timeout)
        return climb(target, args.timeout)
    if args.ab:
        return ab_offline()
    if args.live:
        url = (args.url or os.environ.get("DMS_URL") or DEFAULT_URL).rstrip("/")
        return live(url, args.timeout)
    print(
        "usage: python scripts/score_curated.py "
        "--self-check | --live | --ab | --climb --url URL | --climb --ab --url URL "
        "| --prove-path | --prove-path --url URL"
    )
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
