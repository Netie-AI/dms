"""A model may say a filter is there. It may never say what the filter matches.

The reason it is safe to put a model on the ask path is not that the model is
good. It is that the model's output is a claim about the QUESTION, and every
value that reaches a filter still comes from a granted column via
``certify_pack``. These tests hold that line: a proposer that hallucinates a
country, a sector or a whole vocabulary produces an abstention naming what it
said, never a WHERE clause.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor.cca.proposer import (
    KINDS,
    CortexProposer,
    LexiconProposer,
    Proposal,
    ProposerUnavailable,
    certify_span,
    get_proposer,
)


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    db = tmp_path / "prop.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE deals (country VARCHAR, asset_class VARCHAR, amount DOUBLE)")
    con.execute(
        "INSERT INTO deals VALUES ('MY', 'COM', 10.0), ('SG', 'RES', 20.0), ('Japan', 'COM', 5.0)"
    )
    con.close()
    return db


# ---------------------------------------------------------------------------
# The invariant: a proposal is a claim, not a value.
# ---------------------------------------------------------------------------


def test_a_claimed_member_still_has_to_be_landed(lake: Path) -> None:
    res = certify_span("geo", "Malaysia", warehouse=lake, tables=["deals"])
    assert res.status == "CERTIFIED"
    # The pack claims Malaysia; the COLUMN decides the spelling that filters.
    assert "'MY'" in (res.binding_text() or "")
    assert "'Malaysia'" not in (res.binding_text() or "")


def test_a_pack_member_absent_from_the_data_abstains(lake: Path) -> None:
    res = certify_span("geo", "Cambodia", warehouse=lake, tables=["deals"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None


def test_a_span_no_pack_claims_abstains_and_says_so(lake: Path) -> None:
    """Aruba is a country. The geo pack is eleven Southeast Asian states.

    The honest answer names the gap. It does not add Aruba because a model
    mentioned it, and it does not pretend the question had no geo constraint.
    """
    res = certify_span("geo", "Aruba", warehouse=lake, tables=["deals"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert "no shipped geo pack claims it" in " ".join(res.reasons)
    assert "Aruba" in " ".join(res.reasons)


@pytest.mark.parametrize(
    "span",
    [
        "Wakanda",
        "the Republic of Nowhere",
        "'; DROP TABLE deals; --",
        "MY OR 1=1",
        "",
    ],
)
def test_an_invented_span_can_never_become_a_filter(span: str, lake: Path) -> None:
    """The hallucination case, and the injection case, are the same case here.

    Neither reaches SQL, because the only thing that can produce a filter value
    is a landed column value that certify_pack matched.
    """
    res = certify_span("geo", span, warehouse=lake, tables=["deals"])
    assert res.status == "ABSTAIN"
    assert res.binding_text() is None
    assert res.values == ()


def test_a_landed_value_the_pack_does_not_claim_is_not_swept_in(lake: Path) -> None:
    """`Japan` is in the column. It is not in the pack, so it is not in the filter."""
    res = certify_span("geo", "Malaysia", warehouse=lake, tables=["deals"])
    assert "Japan" not in res.values


def test_asset_class_span_certifies_against_the_column_encoding(lake: Path) -> None:
    res = certify_span("asset_class", "commercial", warehouse=lake, tables=["deals"])
    assert res.status == "CERTIFIED"
    assert "'COM'" in (res.binding_text() or "")


# ---------------------------------------------------------------------------
# Degradation is loud.
# ---------------------------------------------------------------------------


class _BrokenProposer:
    name = "broken"

    def propose(self, question: str) -> Proposal:
        return Proposal(degraded=True, degraded_reason="upstream timeout", source="test")


def test_a_degraded_proposal_does_not_read_as_no_filter_here() -> None:
    """The failure that looks like success is the one worth a test.

    An empty proposal and a failed proposal are the same shape and opposite
    meanings. `engages` must refuse the second.
    """
    empty = Proposal()
    broken = _BrokenProposer().propose("anything")
    assert empty.kinds == broken.kinds == ()
    assert empty.engages is False
    assert broken.engages is False
    assert broken.degraded is True and empty.degraded is False
    assert "timeout" in broken.degraded_reason


def test_proposal_flags_match_the_cascade_dialect() -> None:
    p = Proposal(kinds=("geo", "segment"), spans={"geo": "Malaysia", "segment": "agriculture"})
    flags = p.as_proposal_flags()
    assert flags == {"sense": False, "class": False, "geo": True, "segment": True}
    assert set(flags) == {"sense", "class", "geo", "segment"}
    assert len(KINDS) == 4


# ---------------------------------------------------------------------------
# Wiring. The default is the shipped word list and no credential is required.
# ---------------------------------------------------------------------------


def test_default_is_the_lexicon_and_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CCA_PROPOSER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(get_proposer(), LexiconProposer)


def test_an_unknown_proposer_is_an_error_not_a_quiet_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_CCA_PROPOSER", "gpt")
    with pytest.raises(ProposerUnavailable, match="not a recogniser"):
        get_proposer()


def test_anthropic_without_a_key_refuses_rather_than_becoming_the_lexicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_CCA_PROPOSER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProposerUnavailable, match="ANTHROPIC_API_KEY"):
        get_proposer()


def test_cortex_proposer_names_the_endpoint_it_is_waiting_for() -> None:
    with pytest.raises(ProposerUnavailable) as exc:
        CortexProposer(cortex=None)
    assert "/v1/contract/classify" in str(exc.value)


def test_cortex_proposer_accepts_an_engine_that_serves_classify() -> None:
    class _Engine:
        def classify(self, payload: dict[str, Any]) -> dict[str, Any]:
            return {
                "constraints": [{"kind": "geo", "span": "Malaysia"}],
                "polarity": "include",
                "why": "names one country",
            }

    p = CortexProposer(cortex=_Engine())
    out = p.propose("revenue in Malaysia")
    assert out.kinds == ("geo",)
    assert out.spans["geo"] == "Malaysia"
    assert out.source == "cortex"


def test_the_vendored_contract_still_has_no_classify_route() -> None:
    """Pins the reason CortexProposer is inert. Goes green when Cortex ships it."""
    import json

    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / "contract" / "openapi-1.2.0.json").read_text(encoding="utf-8"))
    assert CortexProposer.ENDPOINT not in spec.get("paths", {}), (
        "Cortex now serves classify: wire CortexProposer's real call, add its "
        "contract test, and delete this assertion."
    )


def test_lexicon_proposer_reports_what_the_word_list_finds() -> None:
    p = LexiconProposer()
    assert p.propose("lease revenue across SEA for commercial property").kinds
    assert p.propose("how many units sold last month").kinds == ()


def test_anthropic_proposer_is_not_constructed_during_import() -> None:
    """No module-level client, so importing the cascade never needs a credential."""
    import dms_executor.cca.proposer as mod

    assert isinstance(mod.AnthropicProposer, type)
    assert mod.DEFAULT_MODEL == "claude-opus-5"


# ---------------------------------------------------------------------------
# The request shape. mypy cannot check dict literals against the SDK overloads,
# and there is no key here to make a real call, so this is the only thing that
# would catch a typo in the request before it reached production.
# ---------------------------------------------------------------------------


class _FakeMessages:
    def __init__(self, payload: Any, *, usage: Any = None) -> None:
        self._payload = payload
        self._usage = usage
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        import json as _json

        raw = self._payload
        body = raw if isinstance(raw, str) else _json.dumps(raw)

        class _Block:
            type = "text"
            text = body

        class _Resp:
            content = [_Block()]
            stop_reason = "end_turn"
            usage = self._usage

        return _Resp()


class _FakeClient:
    def __init__(self, payload: Any, *, usage: Any = None) -> None:
        self.messages = _FakeMessages(payload, usage=usage)


def _proposer(payload: Any, **kw: Any):
    from dms_executor.cca.proposer import AnthropicProposer

    client = _FakeClient(payload, usage=kw.pop("usage", None))
    return AnthropicProposer(client=client, **kw), client


def test_request_pins_model_caching_effort_and_schema() -> None:
    from dms_executor.cca.proposer import SYSTEM

    p, client = _proposer({"constraints": [], "polarity": "none", "why": "x"})
    p.propose("how many units sold last month")
    sent = client.messages.calls[0]

    assert sent["model"] == "claude-opus-5"
    # The system prompt is the cache prefix: identical on every ask, so a
    # proposal costs one short question rather than the whole instruction.
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["system"][0]["text"] == SYSTEM
    assert sent["messages"] == [
        {"role": "user", "content": "how many units sold last month"}
    ]
    # Structured output, not prose to be regexed back out.
    fmt = sent["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["required"] == ["constraints", "polarity", "why"]
    assert fmt["schema"]["additionalProperties"] is False
    assert sent["output_config"]["effort"] == "low"


def test_the_prompt_never_hands_the_model_the_answer_key() -> None:
    """The model must read the question, not recite our pack.

    If a pack member leaks into the instruction, the proposer stops being an
    independent reader and starts being the word list with extra steps.
    """
    from dms_executor.cca.proposer import SYSTEM

    lowered = SYSTEM.lower()
    for member in ("malaysia", "singapore", "timor", "oil palm", "shoplot", "hdb"):
        assert member not in lowered, f"{member!r} leaked into the proposer prompt"


def test_a_hallucinated_span_survives_parsing_and_dies_at_the_pack(lake: Path) -> None:
    """End to end on the safety claim, with the model saying something false."""
    p, _ = _proposer(
        {
            "constraints": [{"kind": "geo", "span": "Atlantis"}],
            "polarity": "include",
            "why": "the question names Atlantis",
        }
    )
    out = p.propose("revenue in Atlantis")
    assert out.kinds == ("geo",)  # the model proposed it
    res = certify_span("geo", out.spans["geo"], warehouse=lake, tables=["deals"])
    assert res.status == "ABSTAIN"  # and the pack refused it
    assert res.values == ()


def test_unknown_kinds_and_blank_spans_are_dropped_not_trusted() -> None:
    p, _ = _proposer(
        {
            "constraints": [
                {"kind": "vibes", "span": "something"},
                {"kind": "geo", "span": "   "},
                {"kind": "segment", "span": "agriculture"},
            ],
            "polarity": "include",
            "why": "mixed bag",
        }
    )
    out = p.propose("q")
    assert out.kinds == ("segment",)
    assert "geo" not in out.spans


def test_unparseable_output_is_degraded_not_empty() -> None:
    p, _ = _proposer("this is not json")
    out = p.propose("q")
    assert out.degraded is True
    assert out.engages is False
    assert "unparseable" in out.degraded_reason


def test_usage_is_recorded_so_a_run_can_price_itself() -> None:
    class _Usage:
        input_tokens = 40
        output_tokens = 120
        cache_read_input_tokens = 1600
        cache_creation_input_tokens = 0

    p, _ = _proposer(
        {"constraints": [], "polarity": "none", "why": "x"}, usage=_Usage()
    )
    p.propose("q")
    assert p.last_usage == {
        "input": 40,
        "output": 120,
        "cache_read": 1600,
        "cache_write": 0,
    }


# ---------------------------------------------------------------------------
# The recorded trial. 545 labelled questions put through the shipped prompt by
# a model, saved so the claim "a model recogniser fixes the miss rate" is
# checkable by anyone, for free, without a credential.
# ---------------------------------------------------------------------------

TRIAL = Path(__file__).resolve().parents[1] / "tests/fixtures/cca_eval/proposer_trial_model.jsonl"


def _trial_rows() -> dict[str, dict[str, Any]]:
    import json

    rows: dict[str, dict[str, Any]] = {}
    for line in TRIAL.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r
    return rows


def test_recorded_trial_parses_into_proposals() -> None:
    from dms_executor.cca.proposer import _proposal_from_payload

    rows = _trial_rows()
    assert len(rows) == 545
    parsed = [_proposal_from_payload(r, source="trial") for r in rows.values()]
    assert all(set(p.spans) <= set(KINDS) for p in parsed)
    # Every span the model returned survives as a claim, and none of them is a
    # filter value. That is the invariant, restated over real model output.
    assert any(p.kinds for p in parsed)
    assert all(not p.degraded for p in parsed)


def test_recorded_trial_beats_the_word_list_on_recognition() -> None:
    """The measured reason to put a model here at all.

    Same questions, same labels, same judge. If this ever regresses, either the
    proposal parser changed meaning or the labels moved, and both are worth
    stopping for.
    """
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    import cca_engagement as ce
    from dms_executor.cca.proposer import LexiconProposer, Proposal, _proposal_from_payload

    rows = _trial_rows()
    cases = []
    for path in ce.discover_corpora():
        corpus = ce.load_labels(path)
        for case in ce.labelled_questions(corpus):
            if case["id"] in rows:
                cases.append(case)
    assert len(cases) == 545

    by_q = {ce._norm_q(str(c["question"])): rows[c["id"]] for c in cases}

    class _Recorded:
        name = "trial"
        last_usage: dict[str, int] = {}

        def propose(self, question: str) -> Proposal:
            raw = by_q.get(ce._norm_q(question))
            if raw is None:
                return Proposal(degraded=True, degraded_reason="not in trial")
            return _proposal_from_payload(raw, source="trial")

    _engages, polarity, _props = ce._cascade()
    geo_index = ce._geo_pack_index()

    def judge(proposer: Any) -> ce.Summary:
        cache: dict[str, Proposal] = {}

        def prop(q: str) -> Proposal:
            if q not in cache:
                cache[q] = proposer.propose(q)
            return cache[q]

        return ce.score(
            [
                ce.judge_case(
                    c,
                    engages=lambda q: prop(q).engages,
                    polarity_is_unsettled=polarity,
                    proposals_of=lambda q: prop(q).as_proposal_flags(),
                    geo_index=geo_index,
                )
                for c in cases
            ]
        )

    model = judge(_Recorded())
    lexicon = judge(LexiconProposer())

    # Recognition: the word list misses most named filters, the model misses none.
    assert lexicon.false_miss_rate is not None and lexicon.false_miss_rate > 0.5
    assert model.false_miss == 0, f"model missed {model.false_miss} of {model.filter_positive}"
    # And it does not buy that by refusing ordinary work.
    assert model.false_engage <= lexicon.false_engage + 1
    assert model.false_engage_rate is not None and model.false_engage_rate < 0.01
