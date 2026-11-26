"""FAILURE-CASES-01: every non-correct graded case becomes a labelled failure case.

Reads one grade52 run directory (``grade52.json`` plus the armed capture
``grade52.armed.jsonl`` when present) and writes ``failure_cases.jsonl``
beside it: one row per case whose bucket is not CORRECT or REFUSAL_OK.

The label (``failure_class``) is read from structured fields only, in this
order: the Insights fail code, the reconfirm ``reason_code``, the envelope
``abstain_code``, then the grade bucket. Free text is copied for a reader
and never parsed. No question text, gold SQL, or row values are written,
so a held-out pack can be labelled without leaking it.

``--ledger PATH`` appends the rows to a cross-run ledger and prints, per
question id, the move from that id's previous ledger row (fixed, broke,
same class, class changed). The ledger is the eval loop's memory: a fix is
a class whose count drops on the next run, on the 52 and on a held-out pack.

    python scripts/verify/failure_cases.py RUN_DIR
    python scripts/verify/failure_cases.py RUN_DIR --ledger /path/failure_ledger.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

OK_BUCKETS = frozenset({"CORRECT", "REFUSAL_OK"})
OUT_NAME = "failure_cases.jsonl"


def _load_capture(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "grade52.armed.jsonl"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if isinstance(rec, dict) and "id" in rec and "env" in rec:
            out[str(rec["id"])] = rec
    return out


def _text(value: Any) -> str:
    return str(value or "").strip()


def failure_class(case: Mapping[str, Any], record: Mapping[str, Any] | None) -> str:
    """Structured label, most specific first. Never parsed from prose."""
    env = (record or {}).get("env") or {}
    served = (record or {}).get("served") or {}
    reconfirm = env.get("reconfirm") if isinstance(env.get("reconfirm"), Mapping) else {}
    for key, value in (
        ("insights", served.get("insights_fail")),
        ("reconfirm", reconfirm.get("reason_code")),
        ("abstain", env.get("abstain_code") or case.get("abstain_code")),
    ):
        code = _text(value)
        if code:
            return f"{key}:{code}"
    return f"bucket:{_text(case.get('bucket')).lower() or 'unknown'}"


def case_row(
    case: Mapping[str, Any], record: Mapping[str, Any] | None, run: Mapping[str, Any]
) -> dict[str, Any]:
    env = (record or {}).get("env") or {}
    served = (record or {}).get("served") or {}
    reconfirm = env.get("reconfirm") if isinstance(env.get("reconfirm"), Mapping) else {}
    return {
        "run": run,
        "id": case.get("id"),
        "bucket": case.get("bucket"),
        "failure_class": failure_class(case, record),
        "grade_reason": case.get("reason"),
        "serve_path": case.get("serve_path"),
        "badge": case.get("badge"),
        "model_requested": served.get("model_requested"),
        "served_model": served.get("served_model") or env.get("served_model"),
        "insights_calls": served.get("insights_calls"),
        "model_sql_present": bool(_text(served.get("model_sql"))),
        "sql_served": bool(_text(env.get("sql_used"))),
        "reconfirm_has_question": bool(_text(reconfirm.get("suggested_question"))),
    }


def _run_meta(report: Mapping[str, Any], run_dir: Path) -> dict[str, Any]:
    return {
        "dms_sha": report.get("dms_sha"),
        "cortex_sha": report.get("cortex_sha"),
        "mode": report.get("mode"),
        "pack": report.get("pack") or "curated_ceo",
        "as_of": report.get("as_of"),
        "dir": str(run_dir.parent.name if run_dir.name == "grade52" else run_dir.name),
    }


def label_run(run_dir: Path) -> list[dict[str, Any]]:
    report = json.loads((run_dir / "grade52.json").read_text(encoding="utf-8"))
    capture = _load_capture(run_dir)
    run = _run_meta(report, run_dir)
    rows = []
    for case in report.get("cases") or []:
        if str(case.get("bucket")) in OK_BUCKETS:
            continue
        rows.append(case_row(case, capture.get(str(case.get("id"))), run))
    return rows


def class_counts(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, int]]:
    counts = Counter(str(r["failure_class"]) for r in rows)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _last_state(previous: Sequence[Mapping[str, Any]], pack: str) -> dict[str, str | None]:
    """id -> failure class in the latest earlier run of ``pack`` that graded it (None = ok)."""
    state: dict[str, str | None] = {}
    current: dict[str, str | None] = {}
    for row in previous:
        if (row.get("run") or {}).get("pack", "curated_ceo") != pack:
            continue
        if row.get("kind") == "run":
            state.update(current)
            current = {str(qid): None for qid in row.get("graded") or []}
        elif str(row.get("id")) in current:
            current[str(row.get("id"))] = str(row.get("failure_class"))
    state.update(current)
    return state


def ledger_moves(
    previous: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    graded_ids: Iterable[str],
    pack: str,
) -> Counter[str]:
    """Per graded id against its last run: fixed, broke, same, class_changed, new_*."""
    last = _last_state(previous, pack)
    now = {str(r["id"]): str(r["failure_class"]) for r in rows}
    moves: Counter[str] = Counter()
    for qid in graded_ids:
        after = now.get(qid)
        if qid not in last:
            moves["new_failure" if after else "new_ok"] += 1
            continue
        before = last[qid]
        if before is None and after is None:
            moves["still_ok"] += 1
        elif before is None:
            moves["broke"] += 1
        elif after is None:
            moves["fixed"] += 1
        elif before == after:
            moves["same"] += 1
        else:
            moves["class_changed"] += 1
    return moves


def append_ledger(ledger: Path, rows: Sequence[Mapping[str, Any]], run_dir: Path) -> Counter[str]:
    """One ``kind=run`` row (the ids graded), then this run's failure rows."""
    previous: list[dict[str, Any]] = []
    if ledger.is_file():
        previous = [
            json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line
        ]
    report = json.loads((run_dir / "grade52.json").read_text(encoding="utf-8"))
    run = _run_meta(report, run_dir)
    graded = [str(c.get("id")) for c in report.get("cases") or []]
    moves = ledger_moves(previous, rows, graded, str(run["pack"]))
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "run", "run": run, "graded": graded}, sort_keys=True) + "\n")
        for row in rows:
            fh.write(json.dumps({"kind": "failure", **row}, sort_keys=True) + "\n")
    return moves


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="directory holding grade52.json")
    parser.add_argument("--ledger", type=Path, help="cross-run ledger to append to")
    args = parser.parse_args(argv)
    run_dir = args.run_dir
    if (run_dir / "grade52").is_dir() and not (run_dir / "grade52.json").is_file():
        run_dir = run_dir / "grade52"
    if not (run_dir / "grade52.json").is_file():
        raise SystemExit("failure_cases: no grade52.json in run_dir")
    rows = label_run(run_dir)
    out = run_dir / OUT_NAME
    out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    print(f"failure_cases n={len(rows)} -> {out}")
    for name, count in class_counts(rows):
        print(f"  {count:3d} {name}")
    if args.ledger is not None:
        moves = append_ledger(args.ledger, rows, run_dir)
        print("ledger moves " + (" ".join(f"{k}={moves[k]}" for k in sorted(moves)) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
