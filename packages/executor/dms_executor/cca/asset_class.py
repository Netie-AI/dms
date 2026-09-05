"""CCA-03 - asset class intent (commercial / residential), certified against landed data.

Why this exists
---------------
"Commercial only" and "ignore residential" are two shapes of one ask, and both
are answerable in a way that is confidently wrong. A granted column called
``asset_class`` may spell the classes ``COM`` / ``RES``, or ``Office`` /
``Condo``, or not carry the distinction at all. Take the question's own word as
the filter value and the query matches nothing, then returns a number. Drop the
class term because no encoding was found and the query covers every class, then
returns a number. Both arrive under a green badge and neither is visible in the
SQL, so neither is caught by a test that reads the SQL.

This module therefore does two narrow things and refuses a third. It reads the
question for class terms using a declared, reviewable pack of aliases, and it
certifies those terms against a granted column's distinct landed values through
``binder.certify_pack`` - the one matching rule for the whole cascade. It never
invents an encoding, and it never lets an unbound class term pass as "no filter
was needed".

The exclude-only decision
-------------------------
"Ignore residential" is certified only when ``Residential`` is itself landed in
the column. Where the column carries no residential value this module ABSTAINS
rather than certifying an exclusion that removes nothing. The data admits two
readings there - "there is genuinely no residential in this Space" and "this
column encodes residential in a spelling the pack does not know" - and nothing
in the column separates them. Acting on the first reading keeps residential
rows inside a total the caller explicitly asked to have them removed from,
which is the failure this epic exists to prevent; abstaining costs an answer
and names the encoding a steward has to add. The caller reads which happened
off the result: CERTIFIED with polarity ``exclude`` means real landed rows are
being removed and the reasons say which spellings, ABSTAIN means the exclusion
could not be shown to remove anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from dms_executor.cca.binder import BinderResult, TermPack, certify_pack
from dms_executor.cca.intent import (
    POSTFIX_REACH,
    PREFIX_REACH,
    mentions,
    phrase_positions,
    strict_set,
    tokens_of,
)

#: Canonical classes and the spellings that mean them. Declared, not learned:
#: a reviewer has to be able to read this and say "yes, a shoplot is
#: commercial". Short codes are here because they are how the encoding usually
#: lands in a warehouse column, which is exactly the case hard rule 12 names.
ASSET_CLASS_PACK = TermPack(
    name="asset_class_members",
    kind="asset class",
    # Only column names that mean an asset class specifically. A loose name is
    # not a cheap extra chance to bind; it is a chance to bind the wrong thing.
    # ``segment_class`` holding ('Retail','Enterprise','Wholesale') certified
    # "commercial property" as ``segment_class IN ('Retail')`` - a customer
    # segment answering a property question, under a green badge. ``class`` and
    # ``category_class`` are the same shape of name (fare class, risk class,
    # ticket class, product category) and carry no claim about property at all.
    # Scanning them was dropped rather than left for coverage_note to disclose.
    column_names=(
        "asset_class",
        "property_type",
        "asset_type",
        "building_type",
    ),
    members={
        "Commercial": (
            "commercial",
            "office",
            "retail",
            "shop",
            "shoplot",
            "industrial",
            "warehouse",
            "cbd",
            "com",
        ),
        "Residential": (
            "residential",
            "housing",
            "apartment",
            "condo",
            "condominium",
            "landed",
            "terrace",
            "hdb",
            "res",
        ),
        "MixedUse": ("mixed use", "mixed development", "mixed"),
    },
    note="Class membership is proposed here and decided by the column's landed values.",
)

#: The negation rule, kept private rather than reusing ``intent.PREFIX_CUES``,
#: because negation is narrower than "this word makes the next one a filter":
#: ``across``, ``in``, ``only`` and ``all`` are filter cues and say nothing
#: about polarity, so borrowing that set whole would turn "across all
#: commercial" into an exclusion. It stays a closed list, not a regex cascade
#: that grows a branch per customer phrasing. Anything not on it reads as an
#: inclusion, where the conflict check or the binder can still catch it.
NEGATION_CUES = frozenset(
    {
        "exclude",
        "excludes",
        "excluded",
        "excluding",
        "except",
        "excepting",
        "ignore",
        "ignores",
        "ignoring",
        "omit",
        "omits",
        "omitting",
        "drop",
        "drops",
        "dropping",
        "without",
        "minus",
        "no",
        "non",
        "not",
    }
)

# Reach comes from intent.py, so "is this word a filter" and "is this filter
# negated" answer over the same window. A cue two tokens ahead of the member is
# governing something else: "no matter if commercial" is not an exclusion of
# commercial, and a three-token reach certified exactly that inversion.

#: Negations that follow the member instead of leading it. English puts these
#: after the noun ("residential excluded"), and searching backward only read
#: that as an inclusion of Residential while the ask says the opposite - an
#: answer inverted from the question, under a green badge.
POSTFIX_NEGATIONS = frozenset({"excluded", "omitted", "removed"})

#: The other half of the same phrase shape. In "residential excluded,
#: commercial included" the negation belongs to residential, and reading it
#: forward onto commercial inverts the ask a second time, so a marker sitting
#: on the member itself outranks a cue that governs an earlier one.
POSTFIX_INCLUSIONS = frozenset({"included", "only", "exclusively"})

#: Words that join two class terms into one phrase about the same thing. Not a
#: third cue list about polarity: these carry none of their own, they only say
#: that whatever holds for one term holds for the other.
COORDINATORS = frozenset({"and", "or", "nor"})

#: What counts as a class term *in a question*, which is not the same list as
#: what counts as one *in a column*.
#:
#: The pack above must know that a column spells Commercial as ``COM`` and that
#: a warehouse is a commercial property, because those are the encodings it
#: certifies against. Reading a question with that same list is how "capacity of
#: warehouse A" - a location in a logistics product - became an asset-class
#: filter and abstained on an ask that had answered fine for months. A control
#: that refuses correct work is a failure, not a win (R-0005).
#:
#: So the bare common nouns (warehouse, office, retail, shop, industrial,
#: landed, mixed) are here only in their property-shaped forms, and the ones
#: that are still ordinary business nouns in that form are declared strict
#: below rather than dropped.
QUESTION_ALIASES: dict[str, tuple[str, ...]] = {
    "Commercial": (
        "commercial",
        "commercial property",
        "commercial space",
        "commercial unit",
        "office space",
        "office property",
        "office unit",
        "retail space",
        "retail property",
        "retail lot",
        "shoplot",
        "shop lot",
        "warehouse property",
        "warehouse space",
        "industrial property",
        "industrial space",
    ),
    "Residential": (
        "residential",
        "residential property",
        "housing",
        "apartment",
        "condo",
        "condominium",
        "hdb",
        "landed property",
        "terrace house",
    ),
    "MixedUse": ("mixed use", "mixed development", "mixed use property"),
}

#: Aliases that name a class only next to a filter cue (``intent.mentions``).
#: Every one of these was observed costing an answer that works today: "What is
#: commercial performance versus target?", "Show commercial vehicles in the
#: fleet", "How much warehouse space is free?", "Total housing allowance paid
#: last year". In this product's domain they are ordinary business nouns first
#: and asset classes second, so "commercial only" and "across all housing"
#: engage while a bare mention does not. The rest of the lexicon stays plain:
#: nobody writes "residential", "condominium" or "shoplot" about something that
#: is not a property class.
STRICT_ALIASES = strict_set(
    (
        "commercial",
        "housing",
        "warehouse space",
        "office space",
        "industrial space",
        "retail space",
    )
)

_QUESTION_PACK = TermPack(
    name=f"{ASSET_CLASS_PACK.name}.question",
    kind=ASSET_CLASS_PACK.kind,
    column_names=ASSET_CLASS_PACK.column_names,
    members=QUESTION_ALIASES,
    note="Question-side lexicon only. Value matching uses ASSET_CLASS_PACK.",
)


@dataclass(frozen=True)
class _Term:
    """One class word in the question, with what the sentence does to it."""

    start: int
    end: int
    member: str
    #: ``intent.mentions``: is this word being used as a filter here at all.
    used: bool
    #: Polarity stated by a cue on this term itself, or None if unstated.
    stated: str | None


def _stated_polarity(tokens: Sequence[str], start: int, end: int) -> str | None:
    """The polarity a cue puts on this term, or None when nothing states one.

    Order matters. A marker on the term itself is read before a cue in front of
    it, because that cue may belong to the previous term: "residential
    excluded, commercial included" is one exclusion and one inclusion, and
    reading the exclusion forward inverts half the ask.
    """
    after = tokens[end : end + POSTFIX_REACH]
    if any(token in POSTFIX_INCLUSIONS for token in after):
        return "include"
    if any(token in POSTFIX_NEGATIONS for token in after):
        return "exclude"
    before = tokens[max(0, start - PREFIX_REACH) : start]
    if any(token in NEGATION_CUES for token in before):
        return "exclude"
    return None


def _class_terms(tokens: Sequence[str]) -> list[_Term]:
    """Every class word in the question, filter or not, left to right.

    ``intent.mentions`` decides ``used``; this only locates the words and
    resolves overlaps, longest alias first, so "commercial property" is one
    term and not "commercial" plus a stray noun. Matching is on whole tokens,
    never substrings: "commercial" must not be found inside "noncommercial".

    Words that are *not* filters are kept rather than dropped because a
    coordination is evidence about all of its members - see ``_coordinations``.
    """
    terms: list[_Term] = []
    for alias, member in _QUESTION_PACK.alias_index().items():
        used = mentions(tokens, alias, strict_aliases=STRICT_ALIASES)
        size = len(alias.split())
        terms += [
            _Term(at, at + size, member, used, _stated_polarity(tokens, at, at + size))
            for at in phrase_positions(tokens, alias)
        ]
    terms.sort(key=lambda term: (term.start, term.start - term.end))
    kept: list[_Term] = []
    consumed = 0
    for term in terms:
        if term.start >= consumed:
            kept.append(term)
            consumed = term.end
    return kept


def _coordinations(tokens: Sequence[str], terms: Sequence[_Term]) -> list[list[_Term]]:
    """Group class words that a conjunction joins into one phrase.

    Two class words joined by "and" or "or" are being used the same way, and
    that is the only evidence available for either of them: "excluding
    commercial and residential" states one polarity for both, and "no matter if
    commercial or residential" states that neither is a filter. A cue sitting
    between two terms is not a join - "for commercial property, ignore
    residential" carries opposite polarities - so the gap has to be empty or a
    conjunction, not merely short.
    """
    groups: list[list[_Term]] = []
    for term in terms:
        gap = tokens[groups[-1][-1].end : term.start] if groups else None
        if gap is not None and (not gap or (len(gap) == 1 and gap[0] in COORDINATORS)):
            groups[-1].append(term)
        else:
            groups.append([term])
    return groups


def parse_class_intent(question: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the class terms in a question into (included, excluded) members.

    The same member can land on both sides ("commercial only, excluding
    commercial") and that contradiction is kept, not resolved here -
    ``bind_asset_class`` REFUSES it, because picking a winner would answer a
    question nobody asked.
    """
    tokens = tokens_of(question)
    include: list[str] = []
    exclude: list[str] = []
    for group in _coordinations(tokens, _class_terms(tokens)):
        shared = next((term.stated for term in group if term.stated), None)
        if shared is None and any(not term.used for term in group):
            # "no matter if commercial or residential": commercial is not a
            # filter here, nothing states a polarity, and residential is joined
            # to it. Reading residential alone bound the answer to the one
            # class the ask was explicitly indifferent about.
            continue
        for term in group:
            if not term.used:
                continue
            bucket = exclude if (term.stated or shared) == "exclude" else include
            if term.member not in bucket:
                bucket.append(term.member)
    return tuple(include), tuple(exclude)


def _candidate_label(include: tuple[str, ...], exclude: tuple[str, ...]) -> str:
    """What the caller asked for, in the words an audit reader can check."""
    parts = list(include) + [f"not {member}" for member in exclude]
    return ", ".join(parts) if parts else "asset class"


def _narrowed(members: tuple[str, ...]) -> TermPack:
    """The pack cut down to the members this ask actually names.

    Narrowing rather than scanning the whole pack is what makes "the column has
    residential but no commercial" an ABSTAIN for a commercial-only ask. A full
    pack scan would match Residential, report CERTIFIED, and hand back a
    binding that has nothing to do with the question.
    """
    return replace(
        ASSET_CLASS_PACK,
        members={m: ASSET_CLASS_PACK.members[m] for m in members},
    )


def bind_asset_class(
    question: str,
    *,
    warehouse: Path | str | None,
    tables: Iterable[str],
    constraint_id: str = "asset_class-1",
) -> BinderResult:
    """Certify the ask's asset-class filter against landed values, or say why not.

    ABSTAIN when the question names no class at all. That is the case worth
    stating plainly: a "commercial only" total computed over every class is
    still a total, still plausible, and wrong by exactly the residential rows.
    Silence about the class is not the same as there being no class filter, so
    an unnamed class does not certify.
    """
    include, exclude = parse_class_intent(question)
    candidate = _candidate_label(include, exclude)

    if not include and not exclude:
        return BinderResult(
            stage="asset_class",
            constraint_id=constraint_id,
            candidate=candidate,
            pack=ASSET_CLASS_PACK.name,
            status="ABSTAIN",
            reasons=(
                "the ask names no asset class, so no class filter can be certified; "
                "answering it over every class would state a commercial number that "
                "includes residential rows",
            ),
        )

    contradiction = tuple(m for m in include if m in exclude)
    if contradiction:
        return BinderResult(
            stage="asset_class",
            constraint_id=constraint_id,
            candidate=candidate,
            pack=ASSET_CLASS_PACK.name,
            status="REFUSE",
            reasons=(
                f"the ask both includes and excludes {', '.join(contradiction)}; "
                "no encoding and no data change resolves a self-contradicting filter",
            ),
        )

    # An include set is the stronger statement: "commercial only, excluding
    # residential" is already answered by binding Commercial, and binding the
    # exclusion as well would be a second filter nobody can read off the ask.
    polarity = "include" if include else "exclude"
    named = include if include else exclude
    result = certify_pack(
        stage="asset_class",
        constraint_id=constraint_id,
        candidate=candidate,
        pack=_narrowed(named),
        warehouse=warehouse,
        tables=tables,
        polarity=polarity,
    )

    if result.status != "CERTIFIED":
        if polarity == "exclude":
            # Say the quiet part: this is not "nothing to exclude, carry on".
            return replace(
                result,
                reasons=(
                    *result.reasons,
                    f"an exclusion of {', '.join(named)} that matches no landed value "
                    "is a no-op, and a no-op exclusion cannot be told apart from an "
                    "encoding this pack does not know, so the class is not bound",
                ),
            )
        return result

    landed = ", ".join(repr(v) for v in result.values)
    if polarity == "exclude":
        note = (
            f"{', '.join(named)} is landed in {result.columns[0]} as {landed}; "
            "the exclusion removes rows that exist"
        )
    else:
        note = f"{', '.join(named)} is landed in {result.columns[0]} as {landed}"
        if exclude:
            note += (
                f". Exclusion of {', '.join(exclude)} is implied by the include "
                "filter and was not bound separately"
            )
    return replace(result, reasons=(*result.reasons, note))


__all__ = [
    "ASSET_CLASS_PACK",
    "COORDINATORS",
    "NEGATION_CUES",
    "POSTFIX_INCLUSIONS",
    "POSTFIX_NEGATIONS",
    "STRICT_ALIASES",
    "bind_asset_class",
    "parse_class_intent",
]
