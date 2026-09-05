"""CCA-05 — run the constraint stages before L0, or abstain naming the gap.

Why a cascade rather than a check at the end
--------------------------------------------
Every other accuracy control in this repo is a detector: it notices a wrong
number after something produced it. The ambiguous-filter class defeats
detectors, because nothing about the number looks wrong. "Rental across SEA,
commercial only" against a warehouse whose country column is encoded ``MY`` and
which has no asset-class column at all produces a real query over real rows and
a real total. It is simply not the total that was asked for, and no downstream
check can tell.

So the constraints are settled first. Each stage either binds its term to values
that are actually landed in a table this Space may read, or it abstains naming
what is missing. A stage that abstains stops the cascade: no SQL runs for that
ask as a certified answer, and the customer is told which binding was missing
rather than handed a confident wrong number.

What this module does not do, stated plainly because it was overclaimed once
---------------------------------------------------------------------------
It does not build SQL, pick a grain, or verify an ontology, and **it does not
check that the executed query used the spelling it certified.** ``live_ask``
passes the question to Cortex unmodified; ``binding_text()`` reaches the trace
and the assumptions, and nothing else. So a certified cascade and a wrong filter
can coexist, and an independent run demonstrated it: an ask naming Malaysia
returned L0_CERTIFIED over SQL filtering ``country = 'Malaysia'`` against a
column encoded ``MY``.

What this module therefore guarantees is narrower than "never a confident wrong
filter". It is: an ask whose terms cannot be bound to landed values abstains
before L0 naming the missing encoding, and an ask whose terms do bind carries a
trace naming the table, the column, the landed spellings and what was left out.
Closing the rest needs either typed constraints on the wire to Cortex or a
DMS-side check of ``sql_used`` against the certified values. Both are new work,
neither is in this epic, and neither should be smuggled in as a widening of it.

``grain``, ``ontology`` and ``sql`` stay absent from the trace rather than
claiming a certification this module did not earn (CCA-01 blocks a later
CERTIFIED stage after a missing one, which is exactly the right reading).

A question that constrains none of these stages does not engage the cascade at
all. Not engaging is not a silent pass: ``engaged`` is False, no trace is
attached, and the ask takes the path it always took.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dms_executor.cca import intent
from dms_executor.cca import proposer as proposer_module
from dms_executor.cca.asset_class import bind_asset_class
from dms_executor.cca.binder import BinderResult
from dms_executor.cca.geo import bind_geo
from dms_executor.cca.segment import bind_segment
from dms_executor.cca.sense import bind_sense
from dms_executor.constraint_cascade import ConstraintSchemaError, parse_trace

#: Env flag for the ask-path hook. Default OFF, and that is the honest setting
#: today rather than a placeholder.
#:
#: The binders are sound: a pack proposes, landed values decide, matching is
#: exact on a normalised form, and everything about that half is measured by
#: scripts/cca_eval.py. What is not sound is deciding *from free text* whether a
#: question carries a filter at all. An independent run measured the engagement
#: rule in both directions against this product's own vocabulary:
#:
#:   * 46 of 106 ordinary questions engaged the cascade and then abstained,
#:     refusing an answer the product gives today ("Show all purchases from
#:     SUP-02", "What is the commercial class of each vehicle?"). The cue words
#:     that make a term filter-shaped - all, any, no, in, only, class, market,
#:     segment, country - are among the commonest words in a business question.
#:   * 35 of 37 asks that plainly name a filter were not recognised, so the
#:     stage recorded "(no recognised term)" as CERTIFIED and the trace went
#:     green over an unconstrained filter. "Commercial lease revenue for the
#:     year" against a warehouse holding COM and RES certifies with the class
#:     never bound, which is the exact failure asset_class.py exists to prevent.
#:
#: A control that refuses 43% of ordinary work and silently passes 95% of the
#: work it exists for makes the product worse in both directions, so it does not
#: go on the customer path by default. Trunk-based with a feature flag is this
#: repo's stated way to land incomplete work, and the flag is the honest state
#: of this one: everything is built, tested and reviewable, and the engagement
#: rule needs to be measured rather than asserted before it decides a customer's
#: answer. Turn it on with DMS_CCA_CASCADE=1 to evaluate it.
_ENABLE_ENV = "DMS_CCA_CASCADE"


def cascade_enabled() -> bool:
    """Is the ask-path hook on? Read per call so a test can flip it."""
    import os

    return os.environ.get(_ENABLE_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


#: Stages this module settles, in cascade order. ``segment`` and ``asset_class``
#: both answer "what class of thing", so they share a stage and are merged into
#: one constraint below.
_CASCADE_STAGES = ("sense", "asset_class", "geo")


def _unrecognised(stage: str) -> dict[str, Any]:
    """A stage whose proposer found no term it knows in this ask.

    The evidence line says what actually happened, not what the ask did. It used
    to read "the ask places no geo constraint", which is a claim about the
    question, and it was false whenever the question named something the
    proposer does not recognise. "rental in Malaysia, commercial only" printed
    exactly that line while naming a country, beside a fully green trace.

    CERTIFIED here still has to earn itself: it says only that no filter was
    derived at this stage, so no filter at this stage can be wrong. What it
    cannot say, and no longer says, is that the customer asked for none. Leaving
    the stage out instead is worse - CCA-01 would then block every later stage,
    and "top sales across SEA" would abstain for the sole reason that it named
    no asset class.
    """
    return {
        "constraint_id": f"{stage}-none",
        "type": stage,
        "candidate": "(no recognised term)",
        "binding": None,
        "evidence": [
            f"no {stage} term this cascade recognises appears as a filter in the ask; "
            "no filter was derived and none is claimed"
        ],
        "status": "CERTIFIED",
        "reasons": [],
    }


@dataclass(frozen=True)
class CascadeOutcome:
    """What the cascade settled, and whether the ask may proceed to L0."""

    engaged: bool
    results: tuple[BinderResult, ...] = ()
    trace: tuple[dict[str, Any], ...] = ()
    blocked_at: str | None = None
    blocked_reason: str = ""

    @property
    def certified(self) -> bool:
        return self.engaged and self.blocked_at is None

    def coverage_notes(self) -> list[str]:
        """One plain sentence per bound stage. These are the buyer's sentences."""
        return [r.coverage_note() for r in self.results]

    def sources(self) -> list[dict[str, Any]]:
        """Source cards naming the column each certified filter was bound to.

        The customer asked which tables and which encodings an answer used;
        these are the rows behind that claim, so drillthrough lands somewhere
        real rather than on a sentence.
        """
        out: list[dict[str, Any]] = []
        for res in self.results:
            if not res.certified:
                continue
            for ref in res.columns:
                out.append(
                    {
                        "ref_id": f"cca_{res.stage}_{ref.replace('.', '_')}",
                        "container": ref,
                        "kind": "encoding",
                        "row_count": len(res.values),
                        "snippet": res.coverage_note(),
                    }
                )
        return out

    def abstain_text(self) -> str:
        """Say which binding was missing, in the words a steward can act on."""
        return (
            f"I cannot certify the {self.blocked_at} constraint in this question, "
            f"so I did not run a query for it. {self.blocked_reason} "
            "Land that encoding, or register a verified query for this ask, and "
            "I can answer it."
        )


#: Cue words that put a polarity on a filter term. Their presence anywhere in a
#: question is enough to stop this cascade certifying an inclusive filter.
#:
#: Reading polarity is not solved here and two rounds of trying made it worse.
#: A window that ignores clause boundaries pulled a cue across a comma
#: ("Excluding tax, commercial revenue by month" -> NOT IN ('COM')); a one-token
#: postfix reach missed the plainest exclusion in English ("residential is
#: excluded" -> IN ('RES'), the exact inverse of the ask); and a geo exclusion
#: nobody recognised certified the excluded country as the only included one
#: ("all of SEA other than Singapore" -> country IN ('SG')).
#:
#: An inverted filter is the worst outcome this epic has: it is a confident
#: answer to the opposite question. So a negated ask does not get a
#: certification from here at all, in either direction. It abstains and says
#: polarity is why. That
#: costs coverage on "commercial only, ignore residential", which is the epic's
#: own phrasing, and coverage is the thing this repo measures separately and can
#: recover with a registered verified query. Precision is not recoverable.
_POLARITY_CUES = frozenset(
    {
        "apart",
        "aside",
        "bar",
        "barring",
        "besides",
        "but",
        "drop",
        "dropping",
        "drops",
        "except",
        "excepting",
        "exclude",
        "excluded",
        "excludes",
        "excluding",
        "ignore",
        "ignored",
        "ignores",
        "ignoring",
        "leave",
        "less",
        "minus",
        "neither",
        "no",
        "non",
        "nor",
        "not",
        "omit",
        "omits",
        "omitted",
        "omitting",
        "only",
        "other",
        "outside",
        "removed",
        "save",
        "skip",
        "versus",
        "vs",
        "without",
    }
)


def polarity_is_unsettled(question: str) -> bool:
    """Does this ask put a polarity on its filters that we cannot read reliably?"""
    return bool(set(intent.tokens_of(question)) & _POLARITY_CUES)


def _polarity_abstain(res: BinderResult) -> BinderResult:
    """Demote an inclusive certification on a negated ask. Fail closed."""
    return BinderResult(
        stage=res.stage,
        constraint_id=res.constraint_id,
        candidate=res.candidate,
        pack=res.pack,
        status="ABSTAIN",
        matched=res.matched,
        absent=res.absent,
        columns=res.columns,
        tables=res.tables,
        unmatched_sample=res.unmatched_sample,
        reasons=(
            *res.reasons,
            "the ask puts a polarity on this filter (an exclusion, an exception "
            "or a comparison) and this stage cannot read polarity reliably, so it "
            "will not certify an inclusive filter over it",
        ),
        polarity=res.polarity,
    )


def _proposals(question: str) -> dict[str, bool]:
    """Which stages this ask constrains, per the configured recogniser.

    The default recogniser is the shipped word list and this returns exactly
    what it always did. DMS_CCA_PROPOSER swaps in a model instead, which
    changes only WHO NOTICES a filter: every value that reaches a predicate
    still comes from a granted column through certify_pack, so a model cannot
    invent one. See cca/proposer.py for why that split is the whole safety
    argument.
    """
    return proposer_module.get_proposer().propose(question).as_proposal_flags()


def engages(question: str) -> bool:
    """Does this ask constrain any cascade stage? Cheap, no database read."""
    return any(_proposals(question).values())


def _merge_class(
    class_res: BinderResult | None, segment_res: BinderResult | None
) -> BinderResult | None:
    """One asset_class constraint from the class and segment binders.

    Both answer "what class of thing". When only one fired it is the answer.
    When both fired, a non-certified verdict wins: an ask that named a segment
    we could bind and a class we could not is not two thirds answerable, it is
    unanswerable, and the reason the customer needs is the one that failed.
    """
    present = [r for r in (class_res, segment_res) if r is not None]
    if not present:
        return None
    for res in present:
        if not res.certified:
            return res
    if len(present) == 1:
        return present[0]
    first, second = present
    return BinderResult(
        stage="asset_class",
        constraint_id=f"{first.constraint_id}+{second.constraint_id}",
        candidate=f"{first.candidate} + {second.candidate}",
        pack=f"{first.pack}+{second.pack}",
        status="CERTIFIED",
        matched={**dict(first.matched), **dict(second.matched)},
        absent=tuple(first.absent) + tuple(second.absent),
        columns=tuple(dict.fromkeys(first.columns + second.columns)),
        tables=tuple(dict.fromkeys(first.tables + second.tables)),
        unmatched_sample=first.unmatched_sample,
        reasons=(),
        polarity=first.polarity,
        # Both halves keep their own column. Composing them into one predicate
        # over columns[0] would list the segment column's values against the
        # class column's name.
        binding_override=" AND ".join(
            b for b in (first.binding_text(), second.binding_text()) if b
        ),
    )


def run_cascade(
    question: str,
    *,
    warehouse: Path | str | None,
    tables: Iterable[str],
) -> CascadeOutcome:
    """Settle sense, class and geo before anything is executed.

    Stops at the first stage that does not certify. Later stages are not run at
    all, so their absence from the trace is a fact about the cascade rather than
    a gap in it.
    """
    asked = _proposals(question)
    if not any(asked.values()):
        return CascadeOutcome(engaged=False)

    table_list = list(tables)
    results: list[BinderResult] = []
    trace: list[dict[str, Any]] = []

    for stage in _CASCADE_STAGES:
        res: BinderResult | None
        if stage == "sense":
            res = (
                bind_sense(question, warehouse=warehouse, tables=table_list)
                if asked["sense"]
                else None
            )
        elif stage == "asset_class":
            class_res = (
                bind_asset_class(question, warehouse=warehouse, tables=table_list)
                if asked["class"]
                else None
            )
            segment_res = (
                bind_segment(question, warehouse=warehouse, tables=table_list)
                if asked["segment"]
                else None
            )
            res = _merge_class(class_res, segment_res)
        else:
            res = (
                bind_geo(question, warehouse=warehouse, tables=table_list)
                if asked["geo"]
                else None
            )

        if res is None:
            trace.append(_unrecognised(stage))
            continue
        # No carve-out for a result that already reads as an exclusion. The
        # eval gate caught that exemption immediately: "excluding tax,
        # commercial property revenue by month" derives exclude(Commercial),
        # because the cue belongs to tax and the window does not see the comma.
        # A polarity this stage cannot read is unreliable in both directions.
        if res.certified and polarity_is_unsettled(question):
            res = _polarity_abstain(res)
        results.append(res)
        trace.append(res.to_constraint())
        if not res.certified:
            reason = "; ".join(res.reasons) or f"{stage} did not certify"
            return CascadeOutcome(
                engaged=True,
                results=tuple(results),
                trace=tuple(trace),
                blocked_at=stage,
                blocked_reason=reason,
            )

    return CascadeOutcome(engaged=True, results=tuple(results), trace=tuple(trace))


def cascade_abstain_envelope(
    outcome: CascadeOutcome,
    *,
    question: str,
    space_id: str | None = None,
    session_id: str | None = None,
    grounded_tables: Sequence[str] | None = None,
) -> dict[str, Any]:
    """The answer a blocked cascade returns. No values, no SQL, no green badge."""
    from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

    env = build_answer_envelope(
        answer_id=f"cca_{abs(hash((question, outcome.blocked_at))) % 10**10}",
        text=outcome.abstain_text(),
        badge="ABSTAIN",
        abstained=True,
        values=[],
        sql_used=None,
        assumptions=[
            f"CCA-05: {outcome.blocked_at} did not certify",
            *outcome.coverage_notes(),
        ],
        space_id=space_id,
        session_id=session_id,
        question=question,
        grounded_tables=list(grounded_tables or []),
        constraint_trace=list(outcome.trace),
        cascade_path=True,
    )
    assert_envelope_valid(env)
    return env


def attach_cascade(env: dict[str, Any], outcome: CascadeOutcome) -> dict[str, Any]:
    """Ride the certified trace along on an answer the normal path produced.

    Fail closed: a trace that does not parse under CCA-01 demotes the answer to
    an abstention rather than shipping a green badge beside a broken trace.
    """
    if not outcome.engaged:
        return env
    try:
        env["constraint_trace"] = parse_trace(list(outcome.trace))
    except ConstraintSchemaError as exc:
        env["constraint_trace"] = []
        env["badge"] = "ABSTAIN"
        env["abstained"] = True
        env["values"] = []
        env["rows"] = []
        env["sql_used"] = None
        env["text"] = f"Constraint cascade trace did not parse: {exc}. No answer is certified."
        env.setdefault("assumptions", []).append(f"CCA-05: {exc}")
        return env
    notes = outcome.coverage_notes()
    if notes:
        env.setdefault("assumptions", []).extend(notes)

    # E7 says a non-empty contributing_sources needs a drillthrough token, and
    # the token is minted by whichever path actually ran the query. So the
    # encoding cards attach only to an answer that already has one. On an answer
    # with no token the columns are not lost: the constraint trace names every
    # bound column and the values it matched, which is the record a steward
    # reviews. Inventing a token here would make a card that drills through to
    # nothing.
    extra = outcome.sources()
    if extra and env.get("drillthrough_token"):
        from dms_executor.envelope import normalize_contributing_sources

        existing = list(env.get("contributing_sources") or [])
        env["contributing_sources"] = existing + normalize_contributing_sources(extra)

    from dms_executor.envelope import assert_envelope_valid

    assert_envelope_valid(env)
    return env


__all__ = [
    "CascadeOutcome",
    "cascade_enabled",
    "polarity_is_unsettled",
    "attach_cascade",
    "cascade_abstain_envelope",
    "engages",
    "run_cascade",
]
