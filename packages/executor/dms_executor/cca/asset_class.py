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

from collections.abc import Iterable, Iterator
from dataclasses import replace
from pathlib import Path

from dms_executor.cca.binder import BinderResult, TermPack, certify_pack, norm_value

#: Canonical classes and the spellings that mean them. Declared, not learned:
#: a reviewer has to be able to read this and say "yes, a shoplot is
#: commercial". Short codes are here because they are how the encoding usually
#: lands in a warehouse column, which is exactly the case hard rule 12 names.
ASSET_CLASS_PACK = TermPack(
    name="asset_class_members",
    kind="asset class",
    column_names=(
        "asset_class",
        "property_type",
        "asset_type",
        "class",
        "segment_class",
        "building_type",
        "category_class",
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

#: The whole negation rule. A closed set of cue words, not a regex cascade that
#: grows a branch per customer phrasing. Anything not on this list reads as an
#: inclusion, which is the safe direction: an unrecognised negation lands the
#: member in the include set, where the conflict check or the binder can still
#: catch it, rather than quietly widening a filter.
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

#: How many tokens back a cue reaches. Three covers "excluding housing",
#: "ignore all residential" and "not any commercial" while keeping "ignore
#: stale rows and total commercial sales" an inclusion, which it is.
CUE_REACH = 3


def _alias_hits(tokens: list[str]) -> Iterator[tuple[int, str]]:
    """Yield (token index, canonical member) for every class term in the question.

    Longest alias wins at each position so "mixed use" is one hit and not a
    "mixed" hit followed by a stray word. Matching is on whole tokens, never on
    substrings: "commercial" must not be found inside "noncommercial-adjacent"
    prose, and a substring rule cannot tell those apart.
    """
    index = ASSET_CLASS_PACK.alias_index()
    longest = max((len(key.split()) for key in index), default=1)
    i = 0
    while i < len(tokens):
        for size in range(min(longest, len(tokens) - i), 0, -1):
            canonical = index.get(" ".join(tokens[i : i + size]))
            if canonical is not None:
                yield i, canonical
                i += size
                break
        else:
            i += 1


def parse_class_intent(question: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the class terms in a question into (included, excluded) members.

    A member named within ``CUE_REACH`` tokens after a negation cue is excluded;
    every other named member is included. The same member can land on both sides
    ("commercial only, excluding commercial") and that contradiction is kept, not
    resolved here - ``bind_asset_class`` REFUSES it, because picking a winner
    would answer a question nobody asked.
    """
    tokens = norm_value(question).split()
    include: list[str] = []
    exclude: list[str] = []
    for start, canonical in _alias_hits(tokens):
        window = tokens[max(0, start - CUE_REACH) : start]
        bucket = exclude if any(token in NEGATION_CUES for token in window) else include
        if canonical not in bucket:
            bucket.append(canonical)
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
    "CUE_REACH",
    "NEGATION_CUES",
    "bind_asset_class",
    "parse_class_intent",
]
