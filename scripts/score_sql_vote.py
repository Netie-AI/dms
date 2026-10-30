"""Paired score of a vote arm against a main arm.

Each case is one question: ``id``, ``main``, ``new``. A verdict is
``correct``, ``wrong``, ``reconfirm``, or ``abstain``.

The 21-case fixture is the three failure types the second reading retries:
10 pre-run refusals, 7 EXPLAIN failures, 4 empty results. Each row records
``before_retry=failed`` and ``after_retry=recovered``. That fixture is not
the held-out split.

The held-out BIRD split (n=348) is not in this repo. With no ``--cases``
file this script prints STUB and does not score that split. Not PASS.
"""

from __future__ import annotations

import argparse
import json
import sys
from math import comb
from pathlib import Path
from typing import Any

VERDICTS = frozenset({"correct", "wrong", "reconfirm", "abstain"})

_FIXTURE: list[dict[str, str]] = [
    {"id": "q1", "main": "correct", "new": "correct"},
    {"id": "q2", "main": "wrong", "new": "correct"},
    {"id": "q3", "main": "correct", "new": "wrong"},
    {"id": "q4", "main": "correct", "new": "reconfirm"},
]

# The measured miss was 10 + 7 + 4. This stub says the retry recovered each
# one. It is not a live run of those questions.
_FAILURE_COUNTS = (
    ("pre_run_refusal", 10),
    ("explain_fail", 7),
    ("empty_rows", 4),
)


def failure_fixture() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, count in _FAILURE_COUNTS:
        for index in range(1, count + 1):
            rows.append(
                {
                    "id": f"{name}_{index:02d}",
                    "failure": name,
                    "before_retry": "failed",
                    "after_retry": "recovered",
                    "main": "correct",
                    "new": "correct",
                }
            )
    return rows


def retry_recovery(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Ids whose second reading failed and the retry recovered."""
    counts: dict[str, int] = {}
    recovered: list[str] = []
    for case in cases:
        kind = str(case.get("failure") or "")
        counts[kind] = counts.get(kind, 0) + 1
        if case.get("before_retry") == "failed" and case.get("after_retry") == "recovered":
            recovered.append(str(case.get("id") or ""))
    return {"counts": counts, "recovered": recovered, "live": False}


def sign_p(wins: int, losses: int) -> float:
    """Two-sided sign test. Ties are not counted."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(comb(n, i) for i in range(k + 1))
    return min(1.0, 2 * tail / (2**n))


def paired_score(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts, precision, reconfirm rate, and the ids that moved."""
    correct = wrong = reconfirm = abstain = 0
    recovered: list[str] = []
    new_wrongs: list[str] = []
    correct_gained = correct_lost = 0
    wrong_removed = wrong_added = 0
    for case in cases:
        ident = str(case.get("id") or "")
        main = str(case.get("main") or "")
        new = str(case.get("new") or "")
        if main not in VERDICTS or new not in VERDICTS:
            raise ValueError(f"bad verdict on {ident or '?'}")
        if new == "correct":
            correct += 1
        elif new == "wrong":
            wrong += 1
        elif new == "reconfirm":
            reconfirm += 1
        else:
            abstain += 1
        if new == "correct" and main != "correct":
            recovered.append(ident)
            correct_gained += 1
        if main == "correct" and new != "correct":
            correct_lost += 1
        if main == "wrong" and new != "wrong":
            wrong_removed += 1
        if new == "wrong" and main != "wrong":
            new_wrongs.append(ident)
            wrong_added += 1
    n = len(cases)
    answered = correct + wrong
    precision = (correct / answered) if answered else None
    return {
        "n": n,
        "correct": correct,
        "wrong": wrong,
        "reconfirm": reconfirm,
        "abstain": abstain,
        "precision": precision,
        "reconfirm_rate": (reconfirm / n) if n else None,
        "recovered": recovered,
        "new_wrongs": new_wrongs,
        "p_correct": sign_p(correct_gained, correct_lost),
        "p_wrong": sign_p(wrong_removed, wrong_added),
        "live": False,
    }


def _print_report(report: dict[str, Any], *, label: str) -> None:
    precision = report["precision"]
    precision_text = "n/a" if precision is None else f"{precision:.3f}"
    rate = report["reconfirm_rate"]
    rate_text = "n/a" if rate is None else f"{rate:.3f}"
    print(
        f"{label} n={report['n']} correct={report['correct']} wrong={report['wrong']} "
        f"precision={precision_text} reconfirm_rate={rate_text} "
        f"p_correct={report['p_correct']:.4g} p_wrong={report['p_wrong']:.4g}"
    )
    print(f"recovered={report['recovered']}")
    print(f"new_wrongs={report['new_wrongs']}")
    print("not PASS")


def self_check() -> int:
    report = paired_score(_FIXTURE)
    expect = {
        "n": 4,
        "correct": 2,
        "wrong": 1,
        "reconfirm": 1,
        "abstain": 0,
        "precision": 2 / 3,
        "reconfirm_rate": 0.25,
        "recovered": ["q2"],
        "new_wrongs": ["q3"],
        "p_correct": 1.0,
        "p_wrong": 1.0,
    }
    for key, value in expect.items():
        if report[key] != value:
            print(f"self-check mismatch {key}: {report[key]!r} != {value!r}")
            return 1
    recovered = retry_recovery(failure_fixture())
    expect_counts = {name: count for name, count in _FAILURE_COUNTS}
    if recovered["counts"] != expect_counts or len(recovered["recovered"]) != 21:
        print(f"self-check mismatch recovery: {recovered!r}")
        return 1
    _print_report(report, label="STUB self-check")
    print(
        "STUB recovery "
        f"pre_run_refusal={expect_counts['pre_run_refusal']} "
        f"explain_fail={expect_counts['explain_fail']} "
        f"empty_rows={expect_counts['empty_rows']} "
        f"recovered={len(recovered['recovered'])}"
    )
    print("STUB: four-row score plus 21 recovered failures. Not the held-out split. Not PASS.")
    return 0


def _load(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check or args.cases is None:
        code = self_check()
        if args.cases is None:
            print(
                "STUB held-out n=348 was not run. No BIRD Mini-Dev databases "
                "and no model route in this environment. Not a score. Not PASS."
            )
            return code
        if code:
            return code
    report = paired_score(_load(args.cases))
    report["live"] = True
    _print_report(report, label="cases")
    print("live file scored. Not PASS unless wrong is 0 on the held-out split.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
