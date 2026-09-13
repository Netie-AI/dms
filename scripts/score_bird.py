"""SCORE-BIRD-01 -- measured live score on BIRD Space (bounded gender attach).

Platform SCORE-BIRD GO 2026-09-13. Real OK/LAYER/ABSTAIN/WRONG only. WRONG=0
law. Full 75-table Mini-Dev extract is leftover. Keys stay in OpenVault.

GEN-01 (#179 @ a9578348) is the product path inside POST /v1/chat/ask.
This harness A/B's exact-match pack (must miss BIRD) vs that live path.
It is not GEN-02's curated coverage climb. Not EPIC-020b / #108 COMPLETE.

  python scripts/score_bird.py --self-check
  DMS_API_BASE=https://<studio>/api python scripts/score_bird.py --live

No laptop default URL. Unset live env is CONFIG, not PASS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from score_curated import (  # noqa: E402
    ask_error_envelope,
    judge,
    load_pack,
)

DEFAULT_PACK = ROOT / "tests" / "fixtures" / "bird_minidev" / "questions.yaml"
BIRD_SPACE = "f0da7dd3-58b3-4d15-84a8-a18f2853ed87"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3
TRANSPORT_BLOCK = frozenset({"ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException"})


def load_bird(path: Path = DEFAULT_PACK) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    honesty = dict(data.get("honesty") or {})
    pack = load_pack(path)
    pack["honesty"] = honesty
    return pack


def honesty_ok(honesty: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if list(honesty.get("attached_tables") or []) != ["gender"]:
        errs.append("attached_tables must be [gender]")
    if int(honesty.get("max_rows") or 0) != 50:
        errs.append("max_rows must be 50")
    if int(honesty.get("leftover_full_extract_tables") or 0) != 75:
        errs.append("leftover_full_extract_tables must be 75")
    if int(honesty.get("source_count") or 0) != 1:
        errs.append("source_count must be 1")
    if str(honesty.get("space_id") or "") != BIRD_SPACE:
        errs.append("space_id must be the Platform BIRD Space")
    if str(honesty.get("data_source") or "") != "12b6f170":
        errs.append("data_source must be 12b6f170")
    blob = json.dumps(honesty)
    if any(k in blob for k in ("99.95", "99,95")):
        errs.append("honesty must not embed a high-nines percent")
    if "COMPLETE" in blob:
        errs.append("honesty must not claim COMPLETE")
    return errs


def _tally() -> dict[str, int]:
    return {"OK": 0, "ABSTAIN": 0, "LAYER": 0, "WRONG": 0}


def _path_report(name: str, tallies: dict[str, int], n: int) -> dict[str, Any]:
    wrong = tallies["WRONG"]
    answered = tallies["OK"] + tallies["LAYER"]
    precision: float | None
    if answered + wrong == 0:
        precision = None
    else:
        precision = round(100.0 * answered / (answered + wrong), 2)
    return {
        "path": name,
        "n": n,
        "ok": tallies["OK"],
        "layer": tallies["LAYER"],
        "abstain": tallies["ABSTAIN"],
        "wrong": wrong,
        "answered": answered,
        "precision_on_answered_pct": precision,
        "coverage_answered_pct": round(100.0 * answered / n, 2) if n else 0.0,
    }


def _miss() -> dict[str, Any]:
    return {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "path miss"}


def exact_match_env(question: str, space_id: str) -> dict[str, Any]:
    """Demo pack / VQ-04 refuse only. Empty grants: BIRD is not Finance."""
    from dms_executor.demo_pack import maybe_pack_ask, maybe_uncertified_refuse_ask

    env = maybe_uncertified_refuse_ask(question, space_id=space_id)
    if env is not None:
        return env

    def _dead_submit(_sql: str) -> None:
        raise RuntimeError("exact-match BIRD lane must not submit")

    def _dead_ledger(_payload: dict[str, Any]) -> None:
        raise RuntimeError("exact-match BIRD lane must not ledger")

    env = maybe_pack_ask(
        question,
        space_id=space_id,
        grantable=set(),
        submit=_dead_submit,
        ledger_append=_dead_ledger,
    )
    return env if env is not None else _miss()


def print_honesty(honesty: dict[str, Any]) -> None:
    tables = ",".join(str(t) for t in (honesty.get("attached_tables") or []))
    print(
        f"honesty attached=[{tables}] max_rows={honesty.get('max_rows')} "
        f"source_count={honesty.get('source_count')} "
        f"leftover_tables={honesty.get('leftover_full_extract_tables')} "
        f"data_source={honesty.get('data_source')}"
    )
    print("not COMPLETE: EPIC-020b #173, EPIC-020 #108, SCORE-BIRD-01")
    print("no high-nines percent claim. keys: OpenVault only. not a DB-GPT clone.")


def self_check(path: Path = DEFAULT_PACK) -> int:
    pack = load_bird(path)
    errs = honesty_ok(pack["honesty"])
    ids = [c["id"] for c in pack["questions"]]
    if len(ids) != len(set(ids)):
        errs.append("duplicate ids")
    if len(ids) < 6:
        errs.append(f"need gender hits + leftover traps, got {len(ids)}")
    expects = {str(c.get("expect") or "").lower() for c in pack["questions"]}
    if "answered" not in expects:
        errs.append("pack needs expect:answered gender cases")
    if "refuse" not in expects:
        errs.append("pack needs expect:refuse leftover traps")
    attached = [c["id"] for c in pack["questions"] if c.get("attached") == "gender"]
    leftover = [c["id"] for c in pack["questions"] if c.get("leftover")]
    if len(attached) < 2:
        errs.append("need >=2 gender-attached asks")
    if len(leftover) < 3:
        errs.append("need >=3 multi-table leftover traps")
    spaces = pack["spaces"]
    if str(spaces.get("bird") or "") != BIRD_SPACE:
        errs.append("spaces.bird must pin Platform BIRD Space")
    blob = path.read_text(encoding="utf-8")
    if "99.95" in blob or "99,95" in blob:
        errs.append("fixture must not embed a high-nines percent")
    planted = judge(
        {"expect": "refuse"},
        {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"x": 1}]},
    )
    gender_ok = judge(
        {"expect": "answered", "min_rows": 1},
        {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"x": 1}]},
    )
    gender_abs = judge(
        {"expect": "answered", "min_rows": 1},
        {"badge": "ABSTAIN", "abstained": True, "rows": []},
    )
    empty_green = judge(
        {"expect": "answered", "min_rows": 1},
        {"badge": "L2_VALIDATED", "abstained": False, "rows": []},
    )
    if planted != "WRONG" or gender_ok != "OK" or gender_abs != "ABSTAIN":
        errs.append("judge plant")
    if empty_green != "WRONG":
        errs.append("confident empty gender answer must be WRONG")
    if errs:
        print("FAIL: " + "; ".join(errs))
        return EXIT_FAIL
    print(
        f"PASS: bird pack {len(ids)} cases, leftover traps {len(leftover)}, "
        "judge fail-closed. not a live measurement."
    )
    print_honesty(pack["honesty"])
    return EXIT_PASS


def _forbid_wildcard(label: str, url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host in {"0.0.0.0", "*", "::", "[::]"}:
        raise ValueError(
            f"{label} hostname {host!r} is a public bind. DMS :8090 stays 127.0.0.1."
        )


def live_url(env: dict[str, str], url_arg: str | None) -> str:
    raw = (url_arg or env.get("DMS_API_BASE") or env.get("BIRD_SCORE_URL") or "").strip()
    if not raw:
        raise ValueError(
            "DMS_API_BASE (or BIRD_SCORE_URL / --url) is required for --live. "
            "No 127.0.0.1:8090 default. Unset is CONFIG, not PASS."
        )
    _forbid_wildcard("DMS_API_BASE", raw)
    return raw.rstrip("/")


def _ask_live(base: str, question: str, space_id: str, timeout: float) -> dict[str, Any]:
    import httpx

    resp = httpx.post(
        f"{base}/v1/chat/ask",
        json={"question": question, "space_id": space_id},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("ask response is not an object")
    return body


def _blocked_kind(exc: BaseException) -> str | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 404:
        return "space_not_found"
    name = type(exc).__name__
    if name in TRANSPORT_BLOCK:
        return "transport"
    return None


def run_exact(pack: dict[str, Any], space_id: str) -> tuple[dict[str, int], list[dict[str, Any]]]:
    tallies = _tally()
    rows: list[dict[str, Any]] = []
    for case in pack["questions"]:
        qid = str(case["id"])
        env = exact_match_env(str(case["question"]), space_id)
        verdict = judge(case, env)
        tallies[verdict] += 1
        rows.append(
            {
                "id": qid,
                "expect": case.get("expect"),
                "exact": verdict,
                "exact_badge": env.get("badge"),
            }
        )
        print(f"{qid}\texact\t{verdict}\t{env.get('badge')}\texpect={case.get('expect')}")
    return tallies, rows


def run_live(
    pack: dict[str, Any],
    space_id: str,
    url: str,
    timeout: float,
) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
    """Return (ok|blocked, tallies, per-case). blocked does not invent PASS."""
    tallies = _tally()
    rows: list[dict[str, Any]] = []
    for case in pack["questions"]:
        qid = str(case["id"])
        try:
            env = _ask_live(url, str(case["question"]), space_id, timeout)
        except Exception as exc:  # noqa: BLE001
            blocked = _blocked_kind(exc)
            if blocked:
                print(f"{qid}\tlive\tBLOCKED\terror.type={blocked}\t{type(exc).__name__}")
                return "blocked", tallies, rows
            env = ask_error_envelope(exc)
            if env is None:
                print(f"{qid}\tlive\tERROR\t{type(exc).__name__}: {exc}")
                tallies["WRONG"] += 1
                rows.append({"id": qid, "generative": "WRONG", "generative_badge": "ERROR"})
                continue
            print(f"{qid}\tlive\tGRANT_REFUSE\t{type(exc).__name__}")
        verdict = judge(case, env)
        tallies[verdict] += 1
        n = len(env.get("rows") or env.get("values") or [])
        rows.append(
            {
                "id": qid,
                "generative": verdict,
                "generative_badge": env.get("badge"),
                "rows": n,
            }
        )
        print(
            f"{qid}\tlive\t{verdict}\t{env.get('badge')}\trows={n}\t"
            f"expect={case.get('expect')}"
        )
    return "ok", tallies, rows


def _write_artifact(report: dict[str, Any]) -> None:
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in report.items() if k != "cases"}
    (art / "score_bird.json").write_text(
        json.dumps(slim, indent=2) + "\n", encoding="utf-8"
    )


def print_paths(*rows: dict[str, Any]) -> None:
    print(f"{'path':<22} n ok layer abstain wrong answered precision")
    for row in rows:
        prec = row.get("precision_on_answered_pct")
        prec_s = "n/a" if prec is None else f"{prec:.2f} pct"
        print(
            f"{row['path']:<22} {row['n']} {row['ok']} {row['layer']} "
            f"{row['abstain']} {row['wrong']} {row['answered']} {prec_s}"
        )


def ab_offline() -> int:
    pack = load_bird()
    space = os.environ.get("BIRD_SPACE_ID", "").strip() or BIRD_SPACE
    print("SCORE-BIRD-01 A/B exact-match only (no live Studio). generative=BLOCKED.")
    print_honesty(pack["honesty"])
    exact_t, cases = run_exact(pack, space)
    n = len(pack["questions"])
    exact_r = _path_report("exact_match", exact_t, n)
    print_paths(exact_r)
    print("generative_live BLOCKED error.type=env.unset owner=Platform/studio")
    print("Run --live on prove/Studio for GEN-01 product path counts.")
    report = {
        "kind": "dms.score_bird",
        "pack": "bird_minidev",
        "space_id": space,
        "honesty": pack["honesty"],
        "exact_match": exact_r,
        "generative": {"path": "generative_live", "blocked": True},
        "wrong": exact_r["wrong"],
        "passed": False,
        "complete": False,
        "cases": cases,
    }
    _write_artifact(report)
    if exact_r["wrong"]:
        print("FAIL: exact-match WRONG>0")
        return EXIT_FAIL
    print("VERDICT: BLOCKED (generative not measured). Not COMPLETE.")
    return EXIT_BLOCKED


def live(url: str, timeout: float, space_id: str) -> int:
    pack = load_bird()
    print("SCORE-BIRD-01 live A/B: exact-match pack vs POST /v1/chat/ask (GEN-01).")
    print_honesty(pack["honesty"])
    print(f"space_id={space_id}")
    print(f"DMS_API_BASE={url}")
    exact_t, exact_cases = run_exact(pack, space_id)
    status, live_t, live_cases = run_live(pack, space_id, url, timeout)
    n = len(pack["questions"])
    exact_r = _path_report("exact_match", exact_t, n)
    by_id = {row["id"]: dict(row) for row in exact_cases}
    for row in live_cases:
        by_id.setdefault(row["id"], {}).update(row)
    if status == "blocked":
        print_paths(exact_r)
        print("generative_live BLOCKED. Do not invent OK/LAYER/ABSTAIN/WRONG.")
        report = {
            "kind": "dms.score_bird",
            "pack": "bird_minidev",
            "space_id": space_id,
            "honesty": pack["honesty"],
            "exact_match": exact_r,
            "generative": {"path": "generative_live", "blocked": True},
            "wrong": exact_r["wrong"],
            "passed": False,
            "complete": False,
            "cases": list(by_id.values()),
        }
        _write_artifact(report)
        print("VERDICT: BLOCKED. Not COMPLETE. Not 99.95.")
        return EXIT_BLOCKED
    gen_r = _path_report("generative_live", live_t, n)
    print_paths(exact_r, gen_r)
    wrong = exact_r["wrong"] + gen_r["wrong"]
    report = {
        "kind": "dms.score_bird",
        "pack": "bird_minidev",
        "space_id": space_id,
        "honesty": pack["honesty"],
        "exact_match": exact_r,
        "generative": gen_r,
        "wrong": wrong,
        "passed": wrong == 0,
        "complete": False,
        "cases": list(by_id.values()),
    }
    _write_artifact(report)
    if wrong:
        print("FAIL: WRONG>0 (confidently wrong or transport error)")
        return EXIT_FAIL
    print("PASS: WRONG=0 on both paths. Not EPIC-020b COMPLETE. leftover=75 tables.")
    return EXIT_PASS


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--ab", action="store_true")
    p.add_argument("--url", default=None)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--space", default=None)
    args = p.parse_args(argv)
    if args.self_check:
        return self_check()
    space = (args.space or os.environ.get("BIRD_SPACE_ID") or BIRD_SPACE).strip()
    if args.live:
        try:
            url = live_url(dict(os.environ), args.url)
        except ValueError as exc:
            print(f"CONFIG: {exc}")
            print("VERDICT: CONFIG")
            return EXIT_CONFIG
        return live(url, args.timeout, space)
    if args.ab:
        return ab_offline()
    print(
        "usage: python scripts/score_bird.py --self-check | --ab | --live\n"
        "--live requires DMS_API_BASE (A/B exact vs GEN-01 product path)."
    )
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
