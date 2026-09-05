"""Certify what a tenure ask *means* before anything downstream trusts it.

Why this mechanism exists
-------------------------
Sense is stage 0 of the cascade, and it is the stage whose failure is invisible.
"What was rent last quarter" reads as a commercial lease book in one dataset and
as residential housing rent in another; a warehouse that carries both encodings
answers either question with a plausible number under a green badge, and nothing
downstream can tell that the wrong book was summed. Geo, grain and SQL are all
correct *given* a sense, so a wrong sense is arithmetic performed on the wrong
rows.

So sense is never guessed. A declared, reviewable pack proposes the vocabulary
(``SENSE_PACKS``); ``propose_senses`` is a lexicon lookup over exactly that
vocabulary, not a growing regex cascade, so the words a question may use are the
words a reviewer already read. Whether a proposed sense *exists* is decided by
``binder.certify_pack`` against landed values, the same one matching rule every
other stage uses.

Ambiguity is decided against the data, not the question. Two senses proposed but
only one landed is not ambiguous, it is answerable, and abstaining there would be
a refusal to answer a question the data can answer. Two senses proposed and both
landed is ambiguous, and this module refuses to pick: it abstains with a hint
naming the candidate senses and the landed spellings that would separate them.
Picking one silently is how a confident L0 number gets attached to the wrong
half of a book.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path

from dms_executor.cca.binder import BinderResult, TermPack, certify_pack, norm_value

#: The tenure/rental sense family. One pack, three canonical senses, because a
#: reviewer has to be able to read the whole proposal in one screen. Aliases are
#: the spellings a *question* may use and the spellings a *column* may land;
#: they are the same list on purpose, so the lexicon and the value binding can
#: never drift apart.
TENURE = TermPack(
    name="sense_tenure",
    kind="tenure/transaction",
    column_names=(
        "transaction_type",
        "txn_type",
        "deal_type",
        "tenure",
        "contract_type",
        "sale_type",
        "listing_type",
    ),
    members={
        "Lease": ("lease", "leasing", "leased", "rental", "rent", "tenancy", "let", "tenant"),
        "Buy": ("buy", "sale", "sold", "purchase", "freehold", "acquisition"),
        "HousingRent": (
            "housing rent",
            "residential rent",
            "home rental",
            "hdb rent",
            "tenancy residential",
        ),
    },
    note="Lease and HousingRent share the word 'rent' by design; the data decides.",
)

SENSE_PACKS: tuple[TermPack, ...] = (TENURE,)

#: Stage name in ``constraint_cascade.STAGES``. Sense is first, so a sense
#: verdict needs no certified priors and parses as a trace of one.
STAGE = "sense"


def _phrase_at(tokens: Sequence[str], phrase: Sequence[str], start: int) -> bool:
    return tuple(tokens[start : start + len(phrase)]) == tuple(phrase)


def _mentions(tokens: Sequence[str], alias: str) -> bool:
    """Whole-token phrase match, never substring.

    Substring matching is the same mistake ``binder`` refuses on values: "let"
    inside "letter" and "rent" inside "current" are not tenure words. Multi-word
    aliases match as a contiguous run so "housing rent" is one phrase, not two
    accidental hits.
    """
    phrase = norm_value(alias).split()
    if not phrase:
        return False
    return any(_phrase_at(tokens, phrase, i) for i in range(len(tokens) - len(phrase) + 1))


def propose_senses(question: str) -> tuple[str, ...]:
    """Which canonical senses the question's own words point at.

    Proposal only. A sense named here still has to be found in a granted column
    before it can bind. Overlapping vocabulary deliberately proposes more than
    one sense - "housing rent" points at HousingRent and the bare word "rent"
    points at Lease - because resolving that overlap is the data's job, not a
    precedence rule invented here.
    """
    tokens = norm_value(question).split()
    if not tokens:
        return ()
    out: list[str] = []
    for pack in SENSE_PACKS:
        for canonical, aliases in pack.members.items():
            if canonical in out:
                continue
            if any(_mentions(tokens, alias) for alias in (canonical, *aliases)):
                out.append(canonical)
    return tuple(out)


def _narrow(pack: TermPack, senses: Sequence[str]) -> TermPack:
    """The declared pack restricted to the senses this ask proposed.

    Certifying the whole family would bind Buy rows into a lease question just
    because the column carries them. The pack stays the reviewed artifact; only
    its member list shrinks.
    """
    return replace(
        pack,
        name=f"{pack.name}[{','.join(senses)}]",
        members={s: pack.members[s] for s in senses},
    )


def _no_sense_named(constraint_id: str, tables: Sequence[str]) -> BinderResult:
    return BinderResult(
        stage=STAGE,
        constraint_id=constraint_id,
        candidate="no sense named",
        pack=", ".join(p.name for p in SENSE_PACKS),
        status="ABSTAIN",
        tables=tuple(tables),
        reasons=(
            "the ask names no lease, buy or housing-rent sense, so there is "
            "nothing to certify at stage sense; name one of the declared "
            "senses to proceed",
        ),
    )


def _ambiguous(
    landed: Sequence[tuple[BinderResult, str]],
    *,
    constraint_id: str,
    candidate: str,
    proposed: Sequence[str],
    tables: Sequence[str],
) -> BinderResult:
    """ABSTAIN naming every landed candidate and the spellings that separate them.

    The hint is the whole point: an abstention that only says "ambiguous" costs
    the buyer a round trip, while one that says Lease is spelled ``LEASE`` and
    HousingRent is spelled ``HOUSING_RENT`` in this column lets them re-ask once
    and be right.
    """
    names = [canonical for _, canonical in landed]
    hints: list[str] = []
    matched: dict[str, tuple[str, ...]] = {}
    columns: list[str] = []
    unmatched: list[str] = []
    for result, canonical in landed:
        spellings = result.matched[canonical]
        matched[canonical] = spellings
        where = ", ".join(result.columns) or ", ".join(result.tables)
        hints.append(f"{canonical} as {', '.join(repr(v) for v in spellings)} in {where}")
        columns += [c for c in result.columns if c not in columns]
        unmatched += [v for v in result.unmatched_sample if v not in unmatched]
    return BinderResult(
        stage=STAGE,
        constraint_id=constraint_id,
        candidate=candidate,
        pack=", ".join(p.name for p in SENSE_PACKS),
        status="ABSTAIN",
        matched=matched,
        absent=tuple(s for s in proposed if s not in matched),
        columns=tuple(columns),
        tables=tuple(tables),
        unmatched_sample=tuple(unmatched),
        reasons=(
            f"the ask reads as {len(names)} senses at once: {', '.join(names)}; "
            "this data carries both, so no sense is certified and no number is stated",
            f"to disambiguate, name one of: {'; '.join(hints)}",
        ),
    )


def bind_sense(
    question: str,
    *,
    warehouse: Path | str | None,
    tables: Iterable[str],
    constraint_id: str = "sense-1",
) -> BinderResult:
    """CERTIFY the ask's tenure sense against landed values, or ABSTAIN saying why.

    Status rules, in the order they are decided:

    * no sense proposed -> ABSTAIN, the ask names no tenure sense;
    * proposals exist but no granted column carries the vocabulary -> ABSTAIN
      with the binder's own missing column/value wording;
    * exactly one proposed sense landed -> CERTIFIED, bound to that sense's
      landed spellings only, saying which proposals the data did not carry;
    * two or more proposed senses landed -> ABSTAIN with a disambiguation hint.
      Never a pick.
    """
    table_list = tuple(dict.fromkeys(str(t).split(".")[-1] for t in tables))
    proposed = propose_senses(question)
    if not proposed:
        return _no_sense_named(constraint_id, table_list)

    candidate = " + ".join(proposed)
    results: list[BinderResult] = []
    landed: list[tuple[BinderResult, str]] = []
    for pack in SENSE_PACKS:
        senses = [s for s in proposed if s in pack.members]
        if not senses:
            continue
        result = certify_pack(
            stage=STAGE,
            constraint_id=constraint_id,
            candidate=candidate,
            pack=_narrow(pack, senses),
            warehouse=warehouse,
            tables=table_list,
        )
        results.append(result)
        landed += [(result, canonical) for canonical in result.matched]

    if not landed:
        # Nothing landed: the binder already said whether the column is missing
        # or present-but-empty of this vocabulary. Reuse that wording verbatim
        # rather than paraphrasing it into a second, drifting sentence.
        return results[0]

    if len(landed) > 1:
        return _ambiguous(
            landed,
            constraint_id=constraint_id,
            candidate=candidate,
            proposed=proposed,
            tables=table_list,
        )

    result, canonical = landed[0]
    absent = tuple(s for s in proposed if s != canonical)
    if not absent:
        return result
    # One sense landed out of several proposed. That is an answerable ask, but
    # the reader is owed the fact that the other reading simply is not in this
    # data - otherwise the narrowing looks like a choice the system made.
    return replace(
        result,
        absent=absent,
        reasons=(
            f"bound to {canonical} alone: the other proposed "
            f"{'sense is' if len(absent) == 1 else 'senses are'} "
            f"{', '.join(absent)}, absent from this data",
        ),
    )


__all__ = ["SENSE_PACKS", "STAGE", "TENURE", "bind_sense", "propose_senses"]
