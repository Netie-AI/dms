"""EVAL-01: a score is a bound, the worst of several runs, and a diff.

0 wrong in n served answers bounds the wrong-served rate at about 3/n at 95
percent (rule of three), so the grader prints that bound per route instead of
letting "0 wrong" stand alone. Abstains are not served answers and never
shrink the bound. Several runs of the same commit are graded on their worst
case, because one lucky run of a model lane is not a score. A baseline diff
names what moved, and refuses a baseline from another grader version.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import grade52  # noqa: E402

_COPY_ONLY = ROOT / "tests" / "fixtures" / "ask_guide" / "copy_only_57d85c52.jsonl"
_COPY_ONLY_RISK = (
    "risk served=16 wrong=0 wrong_ub95=17.1% coverage=16/43 | "
    "ai served=0 wrong=0 wrong_ub95=none | "
    "rule served=16 wrong=0 wrong_ub95=17.1% | "
    "unattributed served=0 wrong=0 wrong_ub95=none"
)


@pytest.fixture(autouse=True)
def _artifacts_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner"))


def _records() -> tuple[str, list[dict[str, Any]]]:
    lines = _COPY_ONLY.read_text(encoding="utf-8").splitlines()
    return lines[0], [json.loads(line) for line in lines[1:] if line.strip()]


def _correct_ids() -> list[str]:
    report = grade52.grade_envelopes_path(_COPY_ONLY)
    return sorted(c["id"] for c in report["cases"] if c["bucket"] == grade52.BUCKET_CORRECT)


def _write(path: Path, header: str, records: list[dict[str, Any]]) -> Path:
    body = "".join(json.dumps(record) + "\n" for record in records)
    path.write_text(header + "\n" + body, encoding="utf-8")
    return path


def _variant(path: Path, *, wrong: set[str] = frozenset(), abstain: set[str] = frozenset()) -> Path:
    """The fixture with chosen correct cases served short a row, or abstained."""
    header, records = _records()
    for record in records:
        env = record["env"]
        if record["id"] in wrong:
            env["rows"] = env["rows"][:-1]
        if record["id"] in abstain:
            env.update({"badge": "ABSTAIN", "abstained": True, "route": "abstain", "rows": []})
    return _write(path, header, records)


def test_wrong_rate_bound_is_exact_and_tracks_the_rule_of_three() -> None:
    assert grade52.wrong_rate_upper(0, 0) is None
    assert grade52.wrong_rate_upper(4, 4) == 1.0
    for n in (16, 52, 300, 1000):
        bound = grade52.wrong_rate_upper(0, n)
        assert bound is not None
        assert bound == pytest.approx(1 - 0.05 ** (1 / n), rel=1e-12)
        assert bound < 3 / n
    assert grade52.wrong_rate_upper(0, 300) < 0.01
    for wrong, n in ((1, 52), (3, 300), (7, 5000)):
        bound = grade52.wrong_rate_upper(wrong, n)
        assert bound is not None and wrong / n < bound < 1
        tail = sum(math.comb(n, i) * bound**i * (1 - bound) ** (n - i) for i in range(wrong + 1))
        assert tail == pytest.approx(0.05, abs=1e-9)


def test_only_served_answers_count_toward_the_bound() -> None:
    cases = [
        {"bucket": "CORRECT", "serve_path": "ai"},
        {"bucket": "WRONG", "serve_path": "L1"},
        {"bucket": "REFUSAL_WRONG", "serve_path": "unattributed"},
        {"bucket": "ABSTAIN", "serve_path": "unattributed"},
        {"bucket": "REFUSAL_OK", "serve_path": "unattributed"},
        {"bucket": "EMPTY_GOLD", "serve_path": "L0"},
        {"bucket": "GOLD_BROKEN", "serve_path": "unattributed"},
    ]
    risk = grade52.risk_counts(cases)
    assert risk["all"] == {"served": 3, "wrong": 2}
    assert risk["ai"] == {"served": 1, "wrong": 0}
    assert risk["rule"] == {"served": 1, "wrong": 1}
    assert risk["unattributed"] == {"served": 1, "wrong": 1}


def test_copy_only_main_prints_its_bound_not_a_bare_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = grade52.grade_envelopes_path(_COPY_ONLY)
    assert grade52.risk_line(report) == _COPY_ONLY_RISK
    assert _COPY_ONLY_RISK in capsys.readouterr().out
    body = json.loads(Path(report["artifact"]).read_text(encoding="utf-8"))
    assert body["grader_version"] == grade52.GRADER_VERSION
    assert body["risk"]["all"] == {
        "served": 16,
        "wrong": 0,
        "wrong_ub95": pytest.approx(0.1707, abs=1e-4),
    }


def test_identical_runs_are_stable_and_pass_the_gate(tmp_path: Path) -> None:
    runs = [_variant(tmp_path / f"run{i}.jsonl") for i in range(3)]
    worst = grade52.grade_runs(runs)
    single = grade52.grade_envelopes_path(_COPY_ONLY)
    assert worst["runs"] == 3 and worst["unstable"] == 0
    assert grade52.summary_line(worst) == grade52.summary_line(single)
    assert grade52.main([*sum((["--envelopes", str(p)] for p in runs), []), "--gate"]) == 0


def test_one_wrong_run_makes_the_case_wrong_and_fails_the_gate(tmp_path: Path) -> None:
    target = _correct_ids()[0]
    runs = [
        _variant(tmp_path / "a.jsonl"),
        _variant(tmp_path / "b.jsonl", wrong={target}),
        _variant(tmp_path / "c.jsonl"),
    ]
    worst = grade52.grade_runs(runs)
    assert worst["wrong"] == 1 and worst["correct"] == 15 and worst["unstable"] == 1
    case = next(c for c in worst["cases"] if c["id"] == target)
    assert case["bucket"] == "WRONG"
    assert case["runs"] == ["CORRECT", "WRONG", "CORRECT"]
    assert grade52.main(["--envelopes", str(runs[0]), "--gate"]) == 0
    assert grade52.main([*sum((["--envelopes", str(p)] for p in runs), []), "--gate"]) == 1


def test_a_correct_case_that_abstains_once_is_an_abstain(tmp_path: Path) -> None:
    target = _correct_ids()[0]
    runs = [_variant(tmp_path / "a.jsonl"), _variant(tmp_path / "b.jsonl", abstain={target})]
    worst = grade52.grade_runs(runs)
    assert worst["correct"] == 15 and worst["abstain"] == 28 and worst["wrong"] == 0
    assert worst["unstable"] == 1
    assert grade52.main([*sum((["--envelopes", str(p)] for p in runs), []), "--gate"]) == 0


def test_runs_from_different_commits_are_not_one_score(tmp_path: Path) -> None:
    header, records = _records()
    other = json.loads(header)
    other["dms_sha"] = "d" * 40
    a = _write(tmp_path / "a.jsonl", header, records)
    b = _write(tmp_path / "b.jsonl", json.dumps(other), records)
    with pytest.raises(SystemExit, match="dms_sha"):
        grade52.grade_runs([a, b])


def test_baseline_diff_counts_moves_and_keeps_ids_out_of_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lost, regained = _correct_ids()[:2]
    base = grade52.grade_envelopes_path(_variant(tmp_path / "base.jsonl", abstain={regained}))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(Path(base["artifact"]).read_text(encoding="utf-8"), encoding="utf-8")
    capsys.readouterr()
    now = _variant(tmp_path / "now.jsonl", abstain={lost})
    rc = grade52.main(["--envelopes", str(now), "--baseline", str(baseline)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "baseline regressed=1 improved=1 moves ABSTAIN->CORRECT:1 CORRECT->ABSTAIN:1" in out
    assert lost not in out and regained not in out
    written = tmp_path / "runner" / "grade52" / "grade52.json"
    artifact = json.loads(written.read_text(encoding="utf-8"))
    assert artifact["baseline"]["moves"] == {
        "ABSTAIN->CORRECT": [regained],
        "CORRECT->ABSTAIN": [lost],
    }


def test_a_baseline_from_another_grader_version_is_refused(tmp_path: Path) -> None:
    report = grade52.grade_envelopes_path(_COPY_ONLY)
    old = json.loads(Path(report["artifact"]).read_text(encoding="utf-8"))
    old.pop("grader_version")
    with pytest.raises(SystemExit, match="grader"):
        grade52.baseline_diff(report, old)
