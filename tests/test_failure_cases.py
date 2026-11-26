"""FAILURE-CASES-01: misses become labelled failure cases; the ledger shows moves."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "failure_cases_t", ROOT / "scripts" / "verify" / "failure_cases.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["failure_cases_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(tmp: Path, name: str, buckets: dict[str, str], served: dict[str, dict]) -> Path:
    run = tmp / name / "grade52"
    run.mkdir(parents=True)
    cases = [
        {"id": qid, "bucket": b, "reason": "r", "serve_path": "ai"} for qid, b in buckets.items()
    ]
    (run / "grade52.json").write_text(
        json.dumps({"dms_sha": "a" * 40, "mode": "armed", "as_of": "2026-11-27", "cases": cases})
    )
    lines = [json.dumps({"dms_sha": "a" * 40, "mode": "armed"})]
    for qid in buckets:
        env = served.get(qid, {}).get("env", {})
        lines.append(
            json.dumps({"id": qid, "env": env, "served": served.get(qid, {}).get("served", {})})
        )
    (run / "grade52.armed.jsonl").write_text("\n".join(lines) + "\n")
    return run


def test_label_order_is_structured_fields_first(tmp_path):
    fc = _mod()
    run = _run(
        tmp_path,
        "r1",
        {"a": "CORRECT", "b": "RECONFIRM", "c": "RECONFIRM", "d": "WRONG", "e": "REFUSAL_OK"},
        {
            "b": {
                "served": {"insights_fail": "insights_timeout"},
                "env": {"reconfirm": {"reason_code": "other"}},
            },
            "c": {
                "env": {
                    "reconfirm": {
                        "reason_code": "validate:explain:BinderException",
                        "suggested_question": "q?",
                    }
                }
            },
        },
    )
    rows = fc.label_run(run)
    by = {r["id"]: r for r in rows}
    assert set(by) == {"b", "c", "d"}
    assert by["b"]["failure_class"] == "insights:insights_timeout"
    assert by["c"]["failure_class"] == "reconfirm:validate:explain:BinderException"
    assert by["c"]["reconfirm_has_question"] is True
    assert by["d"]["failure_class"] == "bucket:wrong"
    # No question text, gold, or row values are copied.
    assert all("question" not in r and "rows" not in r for r in rows)


def test_ledger_moves_across_runs(tmp_path, capsys):
    fc = _mod()
    ledger = tmp_path / "ledger.jsonl"
    r1 = _run(tmp_path, "r1", {"a": "CORRECT", "b": "WRONG", "c": "RECONFIRM"}, {})
    assert fc.main([str(r1.parent), "--ledger", str(ledger)]) == 0
    r2 = _run(tmp_path, "r2", {"a": "WRONG", "b": "CORRECT", "c": "RECONFIRM"}, {})
    assert fc.main([str(r2.parent), "--ledger", str(ledger)]) == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert out == "ledger moves broke=1 fixed=1 same=1"
    kinds = [json.loads(line)["kind"] for line in ledger.read_text().splitlines()]
    assert kinds.count("run") == 2


def test_ledger_keeps_packs_apart(tmp_path):
    fc = _mod()
    prev = [
        {"kind": "run", "run": {"pack": "pack_h"}, "graded": ["x"]},
        {"kind": "failure", "id": "x", "failure_class": "bucket:wrong", "run": {"pack": "pack_h"}},
    ]
    moves = fc.ledger_moves(prev, [], ["x"], "curated_ceo")
    assert moves == {"new_ok": 1}
    moves = fc.ledger_moves(prev, [], ["x"], "pack_h")
    assert moves == {"fixed": 1}


def test_missing_report_refused(tmp_path):
    fc = _mod()
    try:
        fc.main([str(tmp_path)])
    except SystemExit as exc:
        assert "no grade52.json" in str(exc)
    else:
        raise AssertionError("expected refusal")
