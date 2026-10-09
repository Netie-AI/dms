"""Independently labelled engagement rates, and a gate that has been seen to fail.

The binder golden (tests/test_cca_eval.py) cannot decide whether the ask-path
hook may turn on. These tests pin the other instrument: product questions
harvested from surfaces that predate the cascade, labelled by someone who did
not write the lexicon, scored as false-engage and false-miss.

A high rate here is the measurement, not a failure. Failing CI on the rates
would train the next edit to retune intent.py until the numbers look good.
What must go red: a corpus that is not a measurement (no labels, lexicon or
scored-pack sources), a scorer that cannot tell the two rates apart, drift that
fails to force HOLD, and a default-on hook while the rates are not shippable.
What must NOT go red: CI on an unrelated PR that adds a demo question. That is
drift, printed and held, not a failure someone else pays for (R-0005).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cca_engagement  # noqa: E402


@pytest.fixture(scope="module")
def harvested() -> list[dict[str, str]]:
    return cca_engagement.harvest()


@pytest.fixture(scope="module")
def corpus() -> dict[str, Any]:
    return cca_engagement.load_labels()


def test_harvest_is_product_questions_not_the_lexicon(harvested: list[dict[str, str]]) -> None:
    assert len(harvested) >= cca_engagement.MIN_ORDINARY
    sources = {row["source"].replace("\\", "/") for row in harvested}
    assert sources, "harvest produced no sources"
    for src in sources:
        for frag in cca_engagement._BANNED_SOURCE_FRAGMENTS:
            assert frag not in src, src
        for frag in cca_engagement._SCORED_PACK_SOURCES:
            assert frag not in src, f"scored pack in the harvest: {src}"
    questions = [row["question"] for row in harvested]
    assert len(questions) == len(set(q.casefold() for q in questions))


def test_labels_are_consistent_and_name_an_independent_labeler(
    harvested: list[dict[str, str]], corpus: dict[str, Any]
) -> None:
    errors = cca_engagement.validate_corpus(corpus, harvested)
    assert errors == [], errors
    labeler = corpus["labeler"]
    assert "not lexicon author" in str(labeler.get("role", "")).casefold()
    did_not = labeler["did_not_read"]
    joined = " ".join(did_not) if isinstance(did_not, list) else str(did_not)
    assert "dms_executor/cca" in joined.replace("\\", "/")


def test_the_scorer_distinguishes_false_engage_from_false_miss() -> None:
    """A gate nobody has seen fail is not evidence. Hand it both defects."""

    def engages_on_lease(question: str) -> bool:
        return "lease" in question.casefold()

    def no_polarity(_question: str) -> bool:
        return False

    def proposals(question: str) -> dict[str, bool]:
        hit = "lease" in question.casefold()
        return {"sense": hit, "class": False, "segment": False, "geo": False}

    engaged = cca_engagement.judge_case(
        {
            "id": "ordinary_with_purchase",
            "question": "Show all purchases from SUP-02",
            "carries_filter": False,
            "filters": [],
            "polarity": "none",
        },
        engages=lambda q: True,
        polarity_is_unsettled=no_polarity,
        proposals_of=lambda q: {"sense": True, "class": False, "segment": False, "geo": False},
    )
    missed = cca_engagement.judge_case(
        {
            "id": "named_geo",
            "question": "goods from Malaysian suppliers",
            "carries_filter": True,
            "filters": [{"kind": "geo", "term": "Malaysian", "polarity": "include"}],
            "polarity": "include",
        },
        engages=engages_on_lease,
        polarity_is_unsettled=no_polarity,
        proposals_of=proposals,
    )
    quiet = cca_engagement.judge_case(
        {
            "id": "top5",
            "question": "Top 5 selling SKUs by revenue",
            "carries_filter": False,
            "filters": [],
            "polarity": "none",
        },
        engages=engages_on_lease,
        polarity_is_unsettled=no_polarity,
        proposals_of=proposals,
    )

    assert engaged.outcome == cca_engagement.FALSE_ENGAGE
    assert missed.outcome == cca_engagement.FALSE_MISS
    assert quiet.outcome == cca_engagement.OK_ORDINARY
    summary = cca_engagement.score([engaged, missed, quiet])
    assert summary.false_engage == 1
    assert summary.false_miss == 1
    assert summary.ordinary == 2
    assert summary.filter_positive == 1
    assert summary.shippable is False


def test_an_unlabelled_product_question_holds_the_flag_and_is_printed(
    harvested: list[dict[str, str]],
    corpus: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Drift may not shrink the rates silently, and may not fail CI either.

    Seen failing first: before this, a new curated_ceo question on main turned
    every run red. Now the same gap is printed, counted, and blocks SHIP.
    """
    dropped = json.loads(json.dumps(corpus))
    lost = dropped["cases"].pop(0)
    results, summary = cca_engagement.evaluate(dropped)
    loaded = [("engagement_labels.json", dropped, results, summary)]

    gaps = cca_engagement.drift(harvested, loaded)
    assert any(lost["question"].strip() in g for g in gaps), gaps
    assert cca_engagement.validate_corpus(dropped, harvested) == []

    payload = cca_engagement.combined_report(loaded, gaps)
    out = capsys.readouterr().out
    assert payload["shippable"] is False
    assert payload["drift"] == gaps
    assert "DRIFT" in out and "HOLD" in out and "SHIP  " not in out


def test_drift_holds_even_when_the_rates_would_ship(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rates cannot outvote the coverage hole."""
    monkeypatch.setattr(
        cca_engagement.Summary, "shippable", property(lambda self: True), raising=True
    )
    results, summary = cca_engagement.evaluate(corpus)
    loaded = [("engagement_labels.json", corpus, results, summary)]
    assert cca_engagement.combined_report(loaded, [])["shippable"] is True
    assert cca_engagement.combined_report(loaded, ["demo q: new"])["shippable"] is False


def test_a_scored_pack_case_is_rejected(
    harvested: list[dict[str, str]], corpus: dict[str, Any]
) -> None:
    for path in ("scripts/score_answers.py", "tests/fixtures/curated_ceo/questions.yaml"):
        broken = json.loads(json.dumps(corpus))
        broken["cases"][0]["source"] = {"path": path, "id": "sneak"}
        errors = cca_engagement.validate_corpus(broken, harvested)
        assert any("scored pack" in e for e in errors), (path, errors)


def test_a_lexicon_sourced_case_is_rejected(
    harvested: list[dict[str, str]], corpus: dict[str, Any]
) -> None:
    broken = json.loads(json.dumps(corpus))
    broken["cases"][0]["source"] = {
        "path": "tests/fixtures/cca_eval/corpus.json",
        "id": "sneak",
    }
    errors = cca_engagement.validate_corpus(broken, harvested)
    assert any("lexicon" in e or "golden" in e for e in errors)


def test_both_rates_are_numbers_and_the_hook_stays_off(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    results, summary = cca_engagement.evaluate(corpus)
    assert results
    assert summary.ordinary >= cca_engagement.MIN_ORDINARY
    assert summary.filter_positive >= 1
    assert summary.false_engage_rate is not None
    assert summary.false_miss_rate is not None
    # The flag's criterion, not a vibe. Today's harvest has one filter-positive
    # question, so shippable is false even if that one were caught.
    assert summary.shippable is False

    from dms_executor.cca.cascade import cascade_enabled

    monkeypatch.delenv("DMS_CCA_CASCADE", raising=False)
    assert cascade_enabled() is False


def test_cli_reports_both_rates_and_does_not_fail_the_run_on_them(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cca_engagement.main([]) == 0
    out = capsys.readouterr().out
    assert "false-engage" in out
    assert "false-miss" in out
    assert "/v1/chat/ask" in out
    assert "HOLD" in out or "SHIP" in out


def test_in_scope_floor_blocks_a_corpus_that_measures_the_wrong_thing() -> None:
    """A filter the packs never claimed is not evidence about the cue rule.

    1232 questions from five public NL2SQL benchmarks produced 47 filter
    positives, every one geo, and 46 of those named the United States, Aruba
    or Europe. That clears MIN_FILTER almost six times over and says nothing
    about whether the cascade can tell a filter from a grouping. The floor
    exists so a corpus drawn from outside this product's domain cannot satisfy
    the criterion while leaving the question untouched.
    """
    from cca_engagement import MIN_IN_SCOPE_FILTER, Summary

    plenty = dict(
        total=200,
        ordinary=150,
        filter_positive=40,
        uncertain=0,
        false_engage=0,
        false_miss=0,
        polarity=0,
        ordinary_ok=150,
        filter_ok=40,
    )
    # Both rates perfect, both old floors cleared, no in-scope positive.
    assert Summary(**plenty, in_scope_positive=0).shippable is False
    assert Summary(**plenty, in_scope_positive=MIN_IN_SCOPE_FILTER - 1).shippable is False
    assert Summary(**plenty, in_scope_positive=MIN_IN_SCOPE_FILTER).shippable is True


def test_the_shipped_corpora_do_not_clear_the_in_scope_floor() -> None:
    """The live measurement, pinned. This is the sentence the flag turns on."""
    import cca_engagement as ce

    pooled_in_scope = 0
    pooled_positive = 0
    for path in ce.discover_corpora():
        corpus = ce.load_labels(path)
        if not ce.is_independent(corpus):
            continue
        _results, summary = ce.evaluate(corpus, corpus_id=path.name)
        pooled_in_scope += summary.in_scope_positive
        pooled_positive += summary.filter_positive

    assert pooled_positive >= ce.MIN_FILTER, "the filter floor is met on paper"
    assert pooled_in_scope < ce.MIN_IN_SCOPE_FILTER, (
        "if this goes green, the corpus finally contains filters the packs claim "
        "and the miss rate becomes a real number for the first time. Read it before "
        "touching the flag."
    )
