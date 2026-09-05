"""Stage 2 of the cascade: bind a named region to landed country values, or abstain.

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
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

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
_COUNTRY_COLUMNS: tuple[str, ...] = (
    "country",
    "country_code",
    "country_name",
    "iso_country",
    "nation",
    "geo_country",
    "market",
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

#: Spelled-out region names, matched on whole normalised words so that a word
#: merely containing one of them cannot trigger a region. Checked before the
#: bare acronym below.
_REGION_PHRASES: dict[str, str] = {
    "south east asia": "sea",
    "southeast asia": "sea",
    "south east asian": "sea",
    "southeast asian": "sea",
    "s e asia": "sea",
    "association of southeast asian nations": "asean",
    "asean": "asean",
}

#: Acronyms read only when written as acronyms. Lowercase "sea" in an English
#: question is almost always the ocean ("sea freight", "by sea"), and reading
#: that as a region would attach a country filter to a question that never asked
#: for one. "ASEAN" collides with no English word, so it also appears above and
#: is recognised in any case.
_ACRONYMS: dict[str, str] = {"SEA": "sea", "ASEAN": "asean"}

_WORD = re.compile(r"[A-Za-z]+")


def _has_run(tokens: list[str], phrase: list[str]) -> bool:
    """True when ``phrase`` appears as consecutive whole tokens of ``tokens``."""
    span = len(phrase)
    if span == 0 or span > len(tokens):
        return False
    return any(tokens[i : i + span] == phrase for i in range(len(tokens) - span + 1))


def propose_region(question: str) -> str | None:
    """Region key the ask scopes to, or None when it scopes no region.

    A small explicit lookup rather than a pattern cascade: every spelling this
    accepts is written down and reviewable, and a spelling nobody listed reaches
    ``None``, which abstains. That is the correct direction to fail. Guessing a
    region from a loose pattern is how an ask about sea freight acquires a
    country filter.
    """
    tokens = norm_value(question).split()
    for phrase, key in _REGION_PHRASES.items():
        if _has_run(tokens, phrase.split()):
            return key
    for word in _WORD.findall(question or ""):
        acronym = _ACRONYMS.get(word)
        if acronym is not None:
            return acronym
    return None


def bind_geo(
    question: str,
    *,
    warehouse: Path | str | None,
    tables: Iterable[str],
    constraint_id: str = "geo-1",
) -> BinderResult:
    """Certify the ask's region against landed country values, or abstain saying why.

    Four outcomes, all of them honest about what the data holds:

    * no region in the ask -> ABSTAIN, nothing to bind;
    * region named and members landed -> CERTIFIED, filtering on the landed
      spellings, with the members the data lacks disclosed in ``absent``;
    * region named but no granted column carries a country encoding -> ABSTAIN
      naming the missing membership binding;
    * region named, a country column exists, but no value is a member (a
      European book of business) -> ABSTAIN rather than emitting a filter that
      would match zero rows.
    """
    key = propose_region(question)
    if key is None:
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
        pack=GEO_REGION_MEMBERS[key],
        warehouse=warehouse,
        tables=tables,
    )


__all__ = [
    "GEO_REGION_MEMBERS",
    "SEA_PACK",
    "bind_geo",
    "propose_region",
]
