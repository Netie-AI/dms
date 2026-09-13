"""Genie-bar instrument: curated CEO questions through POST /v1/chat/ask.

Same two numbers as score_answers.py (F26). This pack is the Databricks
walkthrough analog: exact certified questions a manager can click, plus
traps that must abstain. It does not start EPIC-019 (no new VQ repo).

  python scripts/score_curated.py --self-check
  python scripts/score_curated.py --live
  python scripts/score_curated.py --ab
  python scripts/score_curated.py --climb --url https://studio.netie.ai/api
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


def ask_error_envelope(exc: BaseException) -> dict[str, Any] | None:
    """403/409 grant/session refusal is ABSTAIN, not a scorer WRONG."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
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


def _ask(base: str, question: str, space_id: str, timeout: float) -> dict[str, Any]:
    import httpx

    resp = httpx.post(
        f"{base.rstrip('/')}/v1/chat/ask",
        json={"question": question, "space_id": space_id},
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
    print(f"PASS: curated pack {len(ids)} cases, judge fail-closed on green trap")
    return 0


def score_pack_live(url: str, timeout: float) -> tuple[dict[str, int], list[dict[str, Any]]]:
    pack = load_pack(DEFAULT_PACK)
    tallies = _tally()
    cases_out: list[dict[str, Any]] = []
    for case in pack["questions"]:
        qid = str(case["id"])
        space = resolve_space(case, pack["spaces"])
        err = ""
        try:
            env = _ask(url, str(case["question"]), space, timeout)
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
        n = len(env.get("rows") or [])
        print(
            f"{qid}\t{verdict}\t{badge}\troute={route}\tpath={path}"
            f"\trows={n}\texpect={case['expect']}"
        )
        cases_out.append(
            {
                "id": qid,
                "verdict": verdict,
                "badge": badge,
                "route": route,
                "path": path,
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
    """Verified sales ontology for the generative A/B lane. Not a pack expand."""
    import duckdb
    from dms_executor.generative_ask import load_verified_ontology
    from dms_executor.ontology import Ontology

    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE lots (lot_id VARCHAR, sku VARCHAR, category VARCHAR, qty DOUBLE)")
        con.execute(
            "INSERT INTO lots VALUES "
            "('L1','SKU-1','ALPHA',10),('L2','SKU-1','ALPHA',20),"
            "('L3','SKU-1','ALPHA',30),('L4','SKU-2','BETA',40)"
        )
        con.execute(
            "CREATE TABLE sales (txn_id VARCHAR, sku VARCHAR, region VARCHAR, amount DOUBLE)"
        )
        con.execute("INSERT INTO sales VALUES ('T1','SKU-1','North',100),('T2','SKU-2','South',50)")
        con.execute("CREATE TABLE regions (region VARCHAR, country VARCHAR)")
        con.execute("INSERT INTO regions VALUES ('North','MY'),('South','MY')")
    finally:
        con.close()
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_object("lot", "lots", ["lot_id"])
    o.add_object("region", "regions", ["region"])
    o.add_object(
        "product",
        "(SELECT sku, ANY_VALUE(category) AS category FROM lots GROUP BY sku)",
        ["sku"],
    )
    o.add_link("sale_of_lot", "sale", ["sku"], "lot", ["sku"])
    o.add_link("sale_of_product", "sale", ["sku"], "product", ["sku"])
    o.add_link("sale_in_region", "sale", ["region"], "region", ["region"])
    o.add_measure("revenue", "sale", "SUM(f.amount)")
    loaded = load_verified_ontology(path, o)
    if loaded is None:
        raise RuntimeError("A/B sales ontology failed verify")
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

    from cortex_client.models import LedgerAppendResponse
    from cortex_contract.execution import QueryResult
    from dms_executor.demo_grants import DEMO_SPACE_GRANTS, canonical_space_id
    from dms_executor.demo_pack import maybe_pack_ask, maybe_uncertified_refuse_ask
    from dms_executor.generative_ask import maybe_generative_ask
    from dms_executor.semantic_retrieve import bind_plan

    pack = load_pack(pack_path)
    tmp = Path(tempfile.mkdtemp()) / "ab_gen01.duckdb"
    onto = _ab_seed(tmp)
    rows = [{"i": i, "v": float(i)} for i in range(8)]

    def submit(_sql: str) -> QueryResult:
        return QueryResult(ok=True, status="ok", run_id="run_ab", output={"rows": rows})

    def ledger(_payload: dict[str, Any]) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_ab", hash="hash_ab_not_entry")

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
            grantable={"sales", "lots", "regions"},
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
            }
        )
    n = len(pack["questions"])
    exact_r = _path_report("exact_match", exact_t, n)
    gen_r = _path_report("generative_semantic", gen_t, n)
    return {
        "kind": "dms.ab_gen01",
        "pack": "curated_ceo",
        "exact_match": exact_r,
        "generative": gen_r,
        "wrong": exact_r["wrong"] + gen_r["wrong"],
        "passed": exact_r["wrong"] == 0 and gen_r["wrong"] == 0,
        "cases": cases_out,
    }


def ab_offline() -> int:
    report = run_ab_curated()
    exact = report["exact_match"]
    gen = report["generative"]
    print(
        f"{'path':<22} n ok layer abstain wrong answered coverage_answered"
    )
    for row in (exact, gen):
        print(
            f"{row['path']:<22} {row['n']} {row['ok']} {row['layer']} "
            f"{row['abstain']} {row['wrong']} {row['answered']} "
            f"{row['coverage_answered_pct']:.2f} pct"
        )
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    (art / "ab_gen01.json").write_text(
        json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    if not report["passed"]:
        print("FAIL: A/B WRONG>0")
        return 1
    print("PASS: A/B WRONG=0 on both paths (not EPIC-019 COMPLETE)")
    return 0


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
    """ok | blocked | fail. Health only (stdlib). Does not invent a score."""
    import urllib.error
    import urllib.request

    health = f"{url.rstrip('/')}/health"
    req = urllib.request.Request(health, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=min(timeout, 15.0)) as resp:
            raw = resp.read(8000)
            status = int(resp.status)
            ctype = resp.headers.get("content-type")
    except urllib.error.HTTPError as exc:
        ctype = exc.headers.get("content-type") if exc.headers else None
        return classify_health(int(exc.code), None, ctype)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return "blocked", f"{type(exc).__name__}: {exc}"
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
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


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--ab", action="store_true")
    p.add_argument("--climb", action="store_true")
    p.add_argument("--url", default=None)
    p.add_argument("--timeout", type=float, default=60.0)
    args = p.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.ab:
        return ab_offline()
    if args.climb:
        target = climb_url(args.url)
        if not target:
            print(
                "CONFIG: --climb needs --url or DMS_API_BASE "
                f"(Platform: {PLATFORM_API}). No laptop default."
            )
            return EXIT_CONFIG
        return climb(target, args.timeout)
    if args.live:
        url = (args.url or os.environ.get("DMS_URL") or DEFAULT_URL).rstrip("/")
        return live(url, args.timeout)
    print(
        "usage: python scripts/score_curated.py "
        "--self-check | --live | --ab | --climb --url URL"
    )
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
