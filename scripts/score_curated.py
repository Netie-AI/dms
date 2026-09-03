"""Genie-bar instrument: curated CEO questions through POST /v1/chat/ask.

Same two numbers as score_answers.py (F26). This pack is the Databricks
walkthrough analog: exact certified questions a manager can click, plus
traps that must abstain. It does not start EPIC-019 (no new VQ repo).

  python scripts/score_curated.py --self-check
  python scripts/score_curated.py --live
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
DEFAULT_URL = "http://127.0.0.1:8090"

CONFIDENT = frozenset(
    {"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS"}
)


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


def judge(case: dict[str, Any], env: dict[str, Any]) -> str:
    """OK | ABSTAIN | LAYER | WRONG. WRONG is the only P0."""
    expect = str(case.get("expect") or "l0").lower()
    badge = str(env.get("badge") or "")
    rows = env.get("rows") or env.get("values") or []
    n = len(rows) if isinstance(rows, list) else 0
    min_rows = int(case.get("min_rows") or 0)
    confident = is_confident(env)

    if expect == "abstain":
        return "WRONG" if confident else "ABSTAIN"

    if not confident:
        return "ABSTAIN"
    if min_rows and n < min_rows:
        return "WRONG"
    if expect == "l0" and not badge.startswith("L0"):
        return "LAYER"
    return "OK"


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
    if len(ids) < 13:
        print(f"FAIL: Genie walkthrough needs >= 13 cases, got {len(ids)}")
        return 1
    expects = {c["expect"] for c in pack["questions"]}
    if "l0" not in expects or "abstain" not in expects:
        print("FAIL: pack must include l0 hits and abstain traps")
        return 1
    planted_ok = judge(
        {"expect": "l0", "min_rows": 1},
        {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"x": 1}]},
    )
    planted_wrong = judge(
        {"expect": "abstain"},
        {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"x": 1}]},
    )
    planted_abs = judge(
        {"expect": "l0"},
        {"badge": "ABSTAIN", "abstained": True, "rows": []},
    )
    if planted_ok != "OK" or planted_wrong != "WRONG" or planted_abs != "ABSTAIN":
        print("FAIL: judge plant")
        return 1
    print(f"PASS: curated pack {len(ids)} cases, judge fail-closed on green trap")
    return 0


def live(url: str, timeout: float) -> int:
    pack = load_pack(DEFAULT_PACK)
    tallies = {"OK": 0, "ABSTAIN": 0, "LAYER": 0, "WRONG": 0}
    for case in pack["questions"]:
        qid = case["id"]
        space = resolve_space(case, pack["spaces"])
        try:
            env = _ask(url, str(case["question"]), space, timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"{qid}\tERROR\t{type(exc).__name__}: {exc}")
            tallies["WRONG"] += 1
            continue
        verdict = judge(case, env)
        tallies[verdict] += 1
        badge = env.get("badge")
        n = len(env.get("rows") or [])
        print(f"{qid}\t{verdict}\t{badge}\trows={n}\texpect={case['expect']}")
    n = len(pack["questions"])
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
        return 1
    print("PASS: 0 WRONG")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--url", default=os.environ.get("DMS_URL", DEFAULT_URL))
    p.add_argument("--timeout", type=float, default=60.0)
    args = p.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.live:
        return live(args.url, args.timeout)
    print("usage: python scripts/score_curated.py --self-check | --live")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
