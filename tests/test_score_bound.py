"""A1-01 (EPIC-A1 #257): a zero-wrong scorer line must carry n and its bound.

NETIE.md rule 7: below n=300 nobody may claim "under one percent". This proves
the gate can fail (R-0007): a bare ``WRONG=0`` / ``0 confidently wrong`` with no
``answered=`` and no ``bound`` on that line or the next is rejected, and both
scorers' PASS summaries and JSON artifacts carry ``answered``/``bound_pct``.
Reporting only - no OK/LAYER/ABSTAIN/WRONG logic is exercised here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_answers  # noqa: E402
import score_bird  # noqa: E402
from score_bound import bound_line, bound_pct, zero_wrong_summary_bounded  # noqa: E402

DOCS = ROOT / "tests" / "fixtures" / "hostile_score"


def test_bound_is_rule_of_three_and_never_zero_pct() -> None:
    assert bound_pct(0) is None
    assert bound_pct(300) == pytest.approx(1.0)
    assert bound_pct(7) == pytest.approx(300 / 7)
    assert bound_line(0) == "answered=0 bound n/a (nothing answered)"
    assert "0 pct" not in bound_line(0)
    assert bound_line(7) == "answered=7 bound about 42.86 pct (rule of three, 95 pct)"


@pytest.mark.parametrize(
    "bare",
    [
        ["PASS: WRONG=0 on both paths. Not EPIC-020b COMPLETE. leftover=74/75 tables."],
        ["PASS 0 confidently wrong."],
        ["PASS 0 confidently wrong.", "     3 abstained - raise coverage by curation."],
        # n present but no bound, and bound present but no n, both fail.
        ["PASS: WRONG=0 on both paths.", "  answered=12"],
        ["PASS: WRONG=0 on both paths.", "  bound about 25.00 pct (rule of three, 95 pct)"],
        # a bound two lines down is too far: "that line or the next".
        ["PASS 0 confidently wrong.", "     abstained", bound_line(12)],
    ],
)
def test_bare_zero_wrong_summary_fails_the_gate(bare: list[str]) -> None:
    assert not zero_wrong_summary_bounded(bare)


def test_bounded_zero_wrong_summary_passes_and_wrong_gt0_is_not_gated() -> None:
    assert zero_wrong_summary_bounded(["PASS: WRONG=0 on both paths.", bound_line(9)])
    assert zero_wrong_summary_bounded([f"PASS 0 confidently wrong. {bound_line(0)}"])
    assert zero_wrong_summary_bounded(["FAIL: WRONG>0 (confidently wrong or transport error)"])


@pytest.mark.parametrize(("exact", "gen"), [(0, 0), (9, 0), (9, 4), (300, 0)])
def test_score_bird_pass_lines_carry_n_and_bound(exact: int, gen: int) -> None:
    lines = score_bird.pass_lines(exact, gen, 74)
    assert lines[0].startswith("PASS: WRONG=0 on both paths.")
    assert zero_wrong_summary_bounded(lines)
    assert f"answered={exact + gen} " in lines[1]
    if exact + gen == 0:
        assert "bound n/a (nothing answered)" in lines[1]
        assert "0 pct" not in lines[1]
    else:
        assert f"bound about {300 / (exact + gen):.2f} pct (rule of three, 95 pct)" in lines[1]


@pytest.mark.parametrize(("answered", "total"), [(0, 16), (12, 16), (16, 16)])
def test_score_answers_pass_lines_carry_n_and_bound(answered: int, total: int) -> None:
    lines = score_answers.pass_lines(answered, total)
    assert "PASS 0 confidently wrong." in lines[0]
    assert zero_wrong_summary_bounded(lines)
    assert f"answered={answered} " in lines[0]
    if answered == 0:
        assert "bound n/a (nothing answered)" in lines[0]
    else:
        assert f"bound about {300 / answered:.2f} pct (rule of three, 95 pct)" in lines[0]
    assert (total - answered > 0) == any("abstained" in line for line in lines)


def test_score_bird_path_report_carries_bound() -> None:
    report = score_bird._path_report(
        "exact_match", {"OK": 5, "LAYER": 2, "ABSTAIN": 1, "WRONG": 0}, 8
    )
    assert report["answered"] == 7
    assert report["wrong"] == 0
    assert report["bound_pct"] == pytest.approx(300 / 7)
    empty = score_bird._path_report(
        "exact_match", {"OK": 0, "LAYER": 0, "ABSTAIN": 8, "WRONG": 0}, 8
    )
    assert empty["answered"] == 0
    assert empty["bound_pct"] is None


def test_score_bird_artifact_carries_answered_wrong_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    exact = score_bird._path_report(
        "exact_match", {"OK": 3, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0}, 3
    )
    gen = score_bird._path_report(
        "generative_live", {"OK": 1, "LAYER": 1, "ABSTAIN": 1, "WRONG": 0}, 3
    )
    score_bird._write_artifact(
        {"kind": "dms.score_bird", "exact_match": exact, "generative": gen, "wrong": 0, "cases": []}
    )
    art = json.loads((tmp_path / "score_bird.json").read_text(encoding="utf-8"))
    assert art["answered"] == 5
    assert art["wrong"] == 0
    assert art["bound_pct"] == pytest.approx(60.0)
    assert "cases" not in art
    # generative BLOCKED: only exact-match answered counts; unanswered is null, never 0.
    score_bird._write_artifact(
        {
            "kind": "dms.score_bird",
            "exact_match": score_bird._path_report("exact_match", score_bird._tally(), 0),
            "generative": {"path": "generative_live", "blocked": True},
            "wrong": 0,
            "cases": [],
        }
    )
    art = json.loads((tmp_path / "score_bird.json").read_text(encoding="utf-8"))
    assert art["answered"] == 0
    assert art["bound_pct"] is None


def _pack_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "apps" / "api"),
            str(ROOT / "packages" / "core"),
            str(ROOT / "packages" / "cortex_client"),
            str(ROOT / "packages" / "executor"),
            str(ROOT / "packages" / "ledger"),
            str(ROOT / "scripts"),
        ]
    )
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_score_answers_oracle_only_self_checks_the_bound_gate() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "score_answers.py"),
            "--docs",
            str(DOCS),
            "--oracle-only",
        ],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=_pack_env(),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "zero-wrong summary carries n and rule-of-three bound: True" in proc.stdout


def test_score_bird_self_check_gates_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    assert score_bird.self_check() == score_bird.EXIT_PASS
    # R-0007: if pass_lines regressed to a bare zero, --self-check must fail.
    monkeypatch.setattr(
        score_bird, "pass_lines", lambda e, g, left: ["PASS: WRONG=0 on both paths."]
    )
    assert score_bird.self_check() == score_bird.EXIT_FAIL
