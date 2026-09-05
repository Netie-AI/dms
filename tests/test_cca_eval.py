"""CCA-06 - the cascade eval corpus runs in CI, and the gate can actually fail.

A gate nobody has seen fail is not evidence, so three of these tests hand the
scorer a corpus it must reject: a case that certifies where the corpus demanded
an abstention, a case that binds a value the corpus forbids, and a case that
abstains where the corpus said it is answerable. The first two are the
confidently-wrong class the epic exists to prevent; the third is a real
regression of a different kind, and it is asserted separately because a gate
that adds refusals and wrong answers together stops meaning anything.

Scope, stated so nobody reads more into a green run than it carries: this
exercises ``run_cascade`` against fixture warehouses. It does not post to
/v1/chat/ask and never reaches Cortex, so it says nothing about the badge or the
rendered answer. That is what tests/test_cca_cascade.py asserts (rule 10a).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cca_eval  # noqa: E402

#: The classes the ticket names, and the least each must carry. Asserted so a
#: later edit cannot delete a whole failure class and still report 100 pct.
REQUIRED_CLASSES: dict[str, int] = {
    "geo certified": 4,
    "geo abstain": 2,
    "asset class certified": 3,
    "asset class abstain": 3,
    "sense certified": 2,
    "sense abstain": 2,
    "segment certified": 2,
    "segment abstain": 2,
    "non-engagement": 3,
}


@pytest.fixture(scope="module")
def corpus() -> dict[str, Any]:
    return cca_eval.load_corpus()


def _mutated(corpus: dict[str, Any], case_id: str, **fields: Any) -> dict[str, Any]:
    """A deep copy of the corpus with one case edited. Never touches the real file."""
    copy = json.loads(json.dumps(corpus))
    for case in copy["cases"]:
        if case["id"] == case_id:
            case.update(fields)
            return copy
    raise AssertionError(f"no such case: {case_id}")


def test_corpus_declares_every_failure_class_the_ticket_names(
    corpus: dict[str, Any],
) -> None:
    cases = corpus["cases"]
    assert len(cases) >= 24
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"

    counts: dict[str, int] = {}
    for case in cases:
        counts[str(case["class"])] = counts.get(str(case["class"]), 0) + 1
    for kind, least in REQUIRED_CLASSES.items():
        assert counts.get(kind, 0) >= least, f"{kind} has {counts.get(kind, 0)}, needs {least}"

    for case in cases:
        # Every case says which failure it catches, because a case whose reason
        # nobody wrote down is the first one a future edit deletes.
        assert str(case.get("why", "")).strip(), f"{case['id']} has no why"
        assert case["warehouse"] in corpus["warehouses"], case["id"]
        if case["expect"] == "ABSTAIN":
            assert case.get("expect_stage"), f"{case['id']} must name the blocking stage"
        if case["expect"] == "CERTIFIED":
            assert case.get("expect_members"), f"{case['id']} must name the members it binds"


def test_every_certified_case_is_answered_and_nothing_is_confidently_wrong(
    corpus: dict[str, Any], tmp_path: Path
) -> None:
    results, summary = cca_eval.evaluate(corpus, tmp_path)

    assert summary.wrong == 0, summary.failures
    assert summary.coverage_misses == 0, summary.failures
    assert summary.engagement_misses == 0, summary.failures
    assert summary.stage_misses == 0, summary.failures
    assert summary.precision_on_answered == 1.0
    assert summary.coverage == 1.0
    assert summary.passed is True
    assert summary.total == len(corpus["cases"])
    # Non-engagement cases are outside precision, not silently counted as passes.
    assert summary.not_answered >= 3
    assert summary.answered + summary.not_answered < summary.total, (
        "a corpus with no abstentions in it would be measuring only the easy half"
    )
    assert all(r.detail for r in results)


def test_gate_fails_when_the_cascade_certifies_an_ask_the_corpus_says_it_must_not(
    corpus: dict[str, Any], tmp_path: Path
) -> None:
    """The headline: one confidently-wrong binding must turn the whole run red."""
    broken = _mutated(
        corpus,
        "cascade_all_stages_certified",
        expect="ABSTAIN",
        expect_stage="geo",
        expect_members=None,
    )
    _, summary = cca_eval.evaluate(broken, tmp_path)

    assert summary.wrong == 1
    assert summary.coverage_misses == 0, "a wrong answer must not be reported as a refusal"
    assert summary.precision_on_answered < 1.0
    assert summary.passed is False

    # And the CLI exits non-zero on it, which is what CI actually reads.
    path = tmp_path / "broken_corpus.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    assert cca_eval.main(["--corpus", str(path), "--json"]) == 1


def test_gate_fails_when_a_forbidden_value_lands_in_the_binding(
    corpus: dict[str, Any], tmp_path: Path
) -> None:
    """The precision half: certifying is not enough, the filter has to be right.

    Forbidding a value the binder does bind is the in-memory stand-in for the
    real defect - a pack that quietly matched Japan into SEA, or Condo into a
    commercial filter - which cannot be staged without breaking the pack itself.
    """
    broken = _mutated(corpus, "geo_iso2_certified", must_not_bind=["MY", "Japan"])
    results, summary = cca_eval.evaluate(broken, tmp_path)

    assert summary.wrong == 1
    flagged = [r for r in results if r.outcome == cca_eval.WRONG]
    assert flagged[0].case_id == "geo_iso2_certified"
    assert "MY" in flagged[0].detail
    assert summary.passed is False


def test_a_coverage_miss_fails_the_run_but_is_never_counted_as_wrong(
    corpus: dict[str, Any], tmp_path: Path
) -> None:
    broken = _mutated(
        corpus,
        "geo_abstain_no_country_column",
        expect="CERTIFIED",
        expect_members=["Malaysia"],
        expect_stage=None,
    )
    _, summary = cca_eval.evaluate(broken, tmp_path)

    assert summary.coverage_misses == 1
    assert summary.wrong == 0
    assert summary.precision_on_answered == 1.0, (
        "precision grades answers, and an abstention is not an answer"
    )
    assert summary.coverage < 1.0
    assert summary.passed is False


def test_the_runner_says_what_it_does_not_prove(capsys: pytest.CaptureFixture[str]) -> None:
    """The honest statement is part of the output, not a comment nobody reads."""
    assert cca_eval.main([]) == 0
    out = capsys.readouterr().out
    assert "/v1/chat/ask" in out
    assert "reaches Cortex" in out
    assert "precision-on-answered" in out
    assert "coverage" in out
