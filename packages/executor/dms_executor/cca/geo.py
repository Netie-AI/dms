"""Stage 2 of the cascade: bind a named region or country to landed values, or abstain.

Why this mechanism exists
-------------------------
"Across SEA" is the ask a model is most willing to answer from its own memory.
It knows a list of countries, so it writes ``country IN (...)`` and returns a
number. Two failures follow, and both look identical to the buyer:

1. The list is right and the data is thin. Three of the eleven countries landed;
   the other eight were never in the warehouse. The total is real arithmetic
   over a third of the region and it is presented as "SEA".
2. The list is right and the encoding is wrong. The column holds ``MY`` and the
   filter says ``'Malaysia'``, so nothing matches and zero rows are summed.

This module fixes neither by being cleverer about countries. It fixes both by
demoting the country list to a *proposal*: ``GEO_REGION_MEMBERS`` names the
eleven states and every spelling that means them, and
``binder.certify_pack`` decides which of them a granted column actually
carries. What landed becomes the filter, in the column's own spelling. What did
not land is named in ``absent`` and spoken in ``coverage_note()`` rather than
quietly shrinking the region. When nothing landed the stage abstains, because a
region no row can evidence is not a region this Space can answer over.

The pack is never widened to make an answer look complete. A country belongs in
it because it is a state of Southeast Asia, not because a customer sells there.

Reading the question
--------------------
Three separate judgements, each of which was observed failing on its own:

* *Which words name a geography at all.* A region or country term counts only
  when ``intent`` says it is used as a filter, so "Show SEA freight cost" reads
  as shipping and not as eleven countries, while "across SEA" and "SEA
  countries" still read as the region.
* *Which geography.* A question that names Malaysia outright is the commonest
  geo ask in this domain and used to derive no filter at all, because only
  ``sea`` and ``asean`` were known. ``propose_countries`` reads the pack's own
  members, so a named country binds through exactly the same certification a
  region does.
* *Whether the ask wants that geography in or out.* An exclusion cannot be
  honoured here, so it is refused rather than inverted into an inclusion. See
  ``bind_geo``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from dms_executor.cca import intent
from dms_executor.cca.binder import BinderResult, TermPack, certify_pack, norm_value

# Membership basis, stated so a reviewer can check it instead of trusting it:
# the eleven sovereign states of Southeast Asia, which is ASEAN's ten founding
# and acceded members plus Timor-Leste. Timor-Leste was admitted as ASEAN's
# eleventh member at the 47th ASEAN Summit on 26 October 2025, so on today's
# membership SEA and ASEAN name the same eleven states and "asean" is an alias
# key onto this one pack rather than a second pack. A caller who needs the
# pre-accession ten-member ASEAN wants a separate pack with its own name and its
# own basis comment; do not reach in and delete a member from this one, because
# every other caller of "sea" would silently lose a country.
#
# Papua New Guinea, Taiwan, Hong Kong and the Pacific states are deliberately
# absent: they are neighbours or trading partners, not Southeast Asian states.
# Commercial convenience is not a membership basis.
#
# Aliases per member cover ISO 3166-1 alpha-2, alpha-3 and the alternate names a
# source system is likely to spell, because those are encodings of the same
# country and hard rule 12 is about encodings. They are matched exactly on the
# binder's normalised form, never by substring.
_SEA_MEMBERS: dict[str, tuple[str, ...]] = {
    "Brunei": ("BN", "BRN", "Brunei Darussalam", "Negara Brunei Darussalam"),
    "Cambodia": ("KH", "KHM", "Kingdom of Cambodia", "Kampuchea"),
    "Indonesia": ("ID", "IDN", "Republic of Indonesia"),
    "Laos": ("LA", "LAO", "Lao", "Lao PDR", "Lao People's Democratic Republic"),
    "Malaysia": ("MY", "MYS"),
    "Myanmar": ("MM", "MMR", "Burma", "Republic of the Union of Myanmar"),
    "Philippines": ("PH", "PHL", "The Philippines", "Republic of the Philippines"),
    "Singapore": ("SG", "SGP", "Republic of Singapore"),
    "Thailand": ("TH", "THA", "Kingdom of Thailand", "Siam"),
    "Timor-Leste": ("TL", "TLS", "East Timor", "Timor Leste", "Timor"),
    "Vietnam": ("VN", "VNM", "Viet Nam", "Socialist Republic of Viet Nam"),
}

# Column names a country encoding is known to live under. Kept to names whose
# values are countries: a hit on any of them is still only a candidate, since
# the binder discards a column whose landed values match no member. City and
# subdivision columns (city, state, province) are deliberately not listed. A
# region is a set of countries, and binding "SEA" to a city column would need a
# city-to-country map this module does not have, so half-supporting it would
# turn a missing map into a confident answer about Kuala Lumpur only.
#
# "market" was on this list and is gone. A market column is whatever a sales
# team decided a market is: a verification run put US city codes in it
# (``LA``, ``NY``, ``SF``) and this stage certified "SEA" against it, because
# ``LA`` is also the ISO code for Laos. The customer was told Laos was in their
# data when it was Los Angeles. That is hard rule 12 with a green badge, and no
# amount of value checking recovers it, because the value genuinely is a member
# alias. The name is the loosest on the list and it buys nothing: a warehouse
# that really keys sales by country has a country-named column too, and if it
# does not, abstaining names the encoding a steward has to land.
#
# "nation" and "region_country" were reviewed for the same risk and kept. Both
# name their contents unambiguously: a column called "nation" holds nations and
# a column called "region_country" holds the country part of a region hierarchy.
# Neither has the "whatever we sell into" reading that made "market" dangerous,
# so a false hit on them would have to be a mislabelled column, which no column
# name list can defend against.
_COUNTRY_COLUMNS: tuple[str, ...] = (
    "country",
    "country_code",
    "country_name",
    "iso_country",
    "nation",
    "geo_country",
    "region_country",
)

SEA_PACK = TermPack(
    name="geo_region_members.sea",
    kind="geo region",
    column_names=_COUNTRY_COLUMNS,
    members=_SEA_MEMBERS,
    note=(
        "The eleven sovereign states of Southeast Asia (ASEAN's members "
        "including Timor-Leste, acceded 26 October 2025). Proposal only: "
        "landed values decide which members an answer may claim."
    ),
)

#: Region key -> pack. "asean" is the same object as "sea" because the two names
#: currently denote the same eleven states; see the basis comment above.
GEO_REGION_MEMBERS: dict[str, TermPack] = {
    "sea": SEA_PACK,
    "asean": SEA_PACK,
}

#: Countries a SEA ask is likely to name that are *not* members of the pack.
#: Nothing is ever bound from this: it is not a membership list and it carries
#: no column encodings. Its only job is to let "top sales across SEA and Japan"
#: say "Japan is not in SEA" instead of certifying the eleven-member filter and
#: dropping the word Japan out of a sentence the buyer reads as an answer about
#: Japan. Adding a country here does not make it answerable; it makes the
#: contradiction speakable.
_ADJACENT_NON_MEMBERS: dict[str, tuple[str, ...]] = {
    "Japan": ("Nippon",),
    "China": ("Mainland China", "People's Republic of China"),
    "Hong Kong": ("Hongkong",),
    "Macau": ("Macao",),
    "Taiwan": ("Chinese Taipei",),
    "South Korea": ("Korea", "Republic of Korea"),
    "India": (),
    "Bangladesh": (),
    "Sri Lanka": (),
    "Pakistan": (),
    "Nepal": (),
    "Mongolia": (),
    "Australia": (),
    "New Zealand": (),
    "Papua New Guinea": (),
}

#: Spelled-out region names, matched on whole normalised words so that a word
#: merely containing one of them cannot trigger a region. Nobody writes
#: "Southeast Asia" about anything but the region, so these are plain in the
#: ``intent`` sense: they need no cue. Checked before the bare acronym below.
_REGION_PHRASES: dict[str, str] = {
    "south east asia": "sea",
    "southeast asia": "sea",
    "south east asian": "sea",
    "southeast asian": "sea",
    "s e asia": "sea",
    "association of southeast asian nations": "asean",
    "asean": "asean",
}

#: Acronyms read only when written as acronyms, and only next to a cue.
#:
#: The case rule alone was not enough. "Show SEA freight cost" writes SEA in
#: caps and means the ocean, and this stage used to engage on it and then
#: abstain, which reads to the customer as a refusal on a question that has
#: nothing to do with geography. So SEA is STRICT: it names the region beside a
#: cue ("across SEA", "in SEA", "SEA countries", "SEA markets", "SEA region")
#: and nowhere else. ASEAN is plain, here and in ``_REGION_PHRASES``, because it
#: collides with no English word; there is nothing else it could be.
_ACRONYMS: dict[str, str] = {"SEA": "sea"}

#: Aliases that are read only when the question writes them in their own case.
#: Every one is a two-letter code, and every two-letter code is also an ordinary
#: word once the binder has case-folded it: ``MY`` is "my", ``ID`` is "id",
#: ``LA`` is "la". Strictness cuts most of that noise, but not all of it, since
#: "in my region" puts a cue right in front of "my" and would otherwise bind
#: Malaysia. A code written lowercase in a question is not a country code.
_CASED_ALIASES: frozenset[str] = intent.strict_set(
    [alias for aliases in _SEA_MEMBERS.values() for alias in aliases if len(alias) == 2]
    + list(_ACRONYMS)
)

#: Country aliases that need a cue to count as a filter. Two-letter codes are
#: noise; full names are not, so "Malaysia" and "Viet Nam" stay plain.
_STRICT_COUNTRY_ALIASES: frozenset[str] = intent.strict_set(
    [alias for aliases in _SEA_MEMBERS.values() for alias in aliases if len(alias) == 2]
)

#: The negating half of ``intent.PREFIX_CUES``, split out rather than restated:
#: some prefix cues scope a term in ("across SEA") and some take it out
#: ("excluding Singapore"), and this stage has to tell those apart. The subset
#: check below is what keeps it from becoming a second cue vocabulary, since a
#: word that leaves ``intent`` fails the import instead of drifting quietly.
_NEGATION_CUES: frozenset[str] = frozenset(
    {
        "exclude",
        "excludes",
        "excluding",
        "excluded",
        "except",
        "excepting",
        "ignore",
        "ignores",
        "ignoring",
        "omit",
        "omits",
        "omitting",
        "without",
        "minus",
        "no",
        "non",
        "not",
    }
)
if not _NEGATION_CUES <= intent.PREFIX_CUES:
    raise RuntimeError(
        "geo negation cues must be drawn from intent.PREFIX_CUES; not there: "
        f"{sorted(_NEGATION_CUES - intent.PREFIX_CUES)}"
    )

_WORD = re.compile(r"[A-Za-z]+")


def _cased_ok(alias: str, question: str) -> bool:
    """Is a case-sensitive alias actually written in its own case here?"""
    if norm_value(alias) not in _CASED_ALIASES:
        return True
    return alias.upper() in set(_WORD.findall(question or ""))


def propose_region(question: str) -> str | None:
    """Region key the ask scopes to, or None when it scopes no region.

    A small explicit lookup rather than a pattern cascade: every spelling this
    accepts is written down and reviewable, and a spelling nobody listed reaches
    ``None``, which abstains. That is the correct direction to fail. Guessing a
    region from a loose pattern is how an ask about sea freight acquires a
    country filter.
    """
    tokens = intent.tokens_of(question)
    for phrase, key in _REGION_PHRASES.items():
        if intent.mentions(tokens, phrase):
            return key
    for word in _WORD.findall(question or ""):
        acronym = _ACRONYMS.get(word)
        if acronym is not None and intent.is_filter_shaped(tokens, word):
            return acronym
    return None


def _named_members(
    question: str, members: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """Canonical members of ``members`` the question names as a filter."""
    tokens = intent.tokens_of(question)
    found: list[str] = []
    for canonical, aliases in members.items():
        for alias in (canonical, *aliases):
            if not _cased_ok(alias, question):
                continue
            if intent.mentions(tokens, alias, strict_aliases=_STRICT_COUNTRY_ALIASES):
                found.append(canonical)
                break
    return tuple(found)


def propose_countries(question: str) -> tuple[str, ...]:
    """Pack members the ask names as a filter, in pack order.

    Naming a country outright is the commonest geo constraint in this domain and
    it used to derive nothing at all, because only region words were read. The
    trace then said no geo term was recognised, which is a claim about the
    question rather than about the data and it was false.

    An excluded country is still *named as a filter* and still comes back here;
    ``bind_geo`` is what reads polarity, because a negative filter is a
    different verdict, not a different proposal.
    """
    return _named_members(question, _SEA_MEMBERS)


def _negated_geo_terms(question: str) -> tuple[str, ...]:
    """Geo terms the ask asks to leave out, in the order the question names them.

    A negation cue within ``intent.PREFIX_REACH`` tokens before the term, which
    is the same reach ``intent`` uses to decide a term is a filter at all.
    """
    tokens = intent.tokens_of(question)
    out: list[str] = []
    for display, alias in _geo_vocabulary():
        if display in out or not _cased_ok(alias, question):
            continue
        for start in intent.phrase_positions(tokens, alias):
            before = tokens[max(0, start - intent.PREFIX_REACH) : start]
            if any(token in _NEGATION_CUES for token in before):
                out.append(display)
                break
    return tuple(out)


def _geo_vocabulary() -> list[tuple[str, str]]:
    """(display name, alias) for every geography this module can read in an ask."""
    pairs: list[tuple[str, str]] = [
        (key.upper(), phrase) for phrase, key in _REGION_PHRASES.items()
    ]
    pairs += [(key.upper(), word) for word, key in _ACRONYMS.items()]
    for members in (_SEA_MEMBERS, _ADJACENT_NON_MEMBERS):
        for canonical, aliases in members.items():
            pairs += [(canonical, alias) for alias in (canonical, *aliases)]
    return pairs


def _narrowed(pack: TermPack, members: tuple[str, ...]) -> TermPack:
    """The pack cut down to the members this ask actually names.

    Same name, so the pack a reader sees in the evidence is the one whose basis
    comment they can go and check. Narrowing rather than scanning the whole pack
    is what makes "rental in Malaysia" abstain on a Thailand-only column instead
    of certifying Thailand and calling it Malaysia.
    """
    return replace(pack, members={m: pack.members[m] for m in members})


def bind_geo(
    question: str,
    *,
    warehouse: Path | str | None,
    tables: Iterable[str],
    constraint_id: str = "geo-1",
) -> BinderResult:
    """Certify the ask's geography against landed country values, or say why not.

    The outcomes, all of them honest about what the data holds:

    * geo term excluded ("excluding Singapore") -> ABSTAIN. DMS does not build
      the executed SQL, so this stage cannot subtract anything from it. The only
      predicate it can hand over is an inclusive one, and an inclusive filter
      over an ask that said "exclude" puts the excluded rows back into the total
      and names them as covered. That was observed: "rental across SEA excluding
      Singapore" certified ``country IN ('MY','SG')`` and said Singapore was
      covered. Fail closed instead;
    * no region and no country in the ask -> ABSTAIN, nothing to bind;
    * a country named that is not in the pack, with a region -> REFUSE. "Japan
      across SEA" is a contradiction, and no column a steward lands fixes it;
    * region and/or members named, members landed -> CERTIFIED, filtering on the
      landed spellings, with the members the data lacks disclosed in ``absent``;
    * named but no granted column carries a country encoding -> ABSTAIN naming
      the missing membership binding;
    * a country column exists but no value is a member (a European book of
      business) -> ABSTAIN rather than emitting a filter matching zero rows.
    """
    excluded = _negated_geo_terms(question)
    if excluded:
        named = ", ".join(excluded)
        return BinderResult(
            stage="geo",
            constraint_id=constraint_id,
            candidate=f"not {named}",
            pack=SEA_PACK.name,
            status="ABSTAIN",
            reasons=(
                f"the ask excludes {named}, and a geo exclusion cannot be certified "
                "here: this stage does not constrain the executed query, so the only "
                "filter it can hand over is an inclusive one, which would put the "
                f"{named} rows back into the total and report them as covered",
            ),
        )

    key = propose_region(question)
    members = propose_countries(question)
    outside = _named_members(question, _ADJACENT_NON_MEMBERS)
    pack = GEO_REGION_MEMBERS[key] if key is not None else SEA_PACK
    contradiction = tuple(outside) + tuple(m for m in members if m not in pack.members)

    if key is not None and contradiction:
        named = ", ".join(contradiction)
        return BinderResult(
            stage="geo",
            constraint_id=constraint_id,
            candidate=f"{key.upper()}, {named}",
            pack=pack.name,
            status="REFUSE",
            reasons=(
                f"the ask scopes to {key.upper()} and names {named}, which is not a "
                f"member of {key.upper()}; no encoding and no data change resolves a "
                "region that excludes the country asked about",
            ),
        )

    if members:
        # A region and named countries in one ask is the intersection: "SEA,
        # Malaysia" is Malaysia, not the eleven. The contradiction branch above
        # has already refused an empty intersection.
        candidate = ", ".join(members)
        if key is not None:
            candidate = f"{candidate} in {key.upper()}"
        return certify_pack(
            stage="geo",
            constraint_id=constraint_id,
            candidate=candidate,
            pack=_narrowed(pack, members),
            warehouse=warehouse,
            tables=tables,
        )

    if key is None:
        if outside:
            named = ", ".join(outside)
            return BinderResult(
                stage="geo",
                constraint_id=constraint_id,
                candidate=named,
                pack=pack.name,
                status="ABSTAIN",
                reasons=(
                    f"the ask names {named}, which is not one of the eleven Southeast "
                    "Asian states this stage can bind, so there is no membership here "
                    "to certify",
                ),
            )
        return BinderResult(
            stage="geo",
            constraint_id=constraint_id,
            candidate="geo region",
            pack="geo_region_members",
            status="ABSTAIN",
            reasons=("the ask scopes no region, so there is no membership to certify",),
        )

    return certify_pack(
        stage="geo",
        constraint_id=constraint_id,
        candidate=key.upper(),
        pack=pack,
        warehouse=warehouse,
        tables=tables,
    )


__all__ = [
    "GEO_REGION_MEMBERS",
    "SEA_PACK",
    "bind_geo",
    "propose_countries",
    "propose_region",
]
