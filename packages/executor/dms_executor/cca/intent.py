"""Is this word being used as a filter, or is it just in the sentence?

The problem
-----------
An independent verification run put 36 ordinary questions from this product's
own domain through the cascade. Twenty-five engaged it and then abstained:

    Total purchase cost by supplier          -> blocked at sense
    Revenue per tenant this quarter          -> blocked at sense
    What is commercial performance vs target -> blocked at asset_class
    Which farms are late on delivery?        -> blocked at asset_class
    Show SEA freight cost                    -> blocked at geo

None of those asks a class, tenure, segment or region filter. Every one of them
contains a word that can name one. A control that refuses correct work is a
failure, not a win, and 25 of 36 is not a control, it is an outage.

The rule
--------
Two kinds of term, decided per alias rather than per stage:

``plain``   the word names a filter wherever it appears. "residential",
            "condominium", "agricultural", "Southeast Asia". Nobody writes those
            about something else.
``strict``  the word names a filter only next to a cue that makes it one.
            "commercial", "rent", "purchase", "housing", "farms", "SEA". Each is
            an ordinary business noun in this domain, and each was observed
            costing a real answer.

A strict term counts when a cue sits within two tokens before it ("across SEA",
"ignore all residential", "in agricultural") or one token after it ("commercial
only", "SEA countries", "residential excluded").

One token after, not two, is load-bearing: "commercial vehicles in the fleet"
puts "in" two tokens after "commercial" and means nothing of the kind.

This is a closed cue list, deliberately. It is not a branch per customer
phrasing, and it does not grow when a question is missed. A missed filter costs
coverage, which is measured separately and can be recovered by a steward
registering the question. A wrongly recognised one costs an answer that worked.
"""

from __future__ import annotations

from collections.abc import Sequence

from dms_executor.cca.binder import norm_value

#: Cues that make the word after them a filter. "from" is deliberately absent:
#: "total purchases from SUP-02" is a supplier question, not a tenure one.
PREFIX_CUES = frozenset(
    {
        "across",
        "in",
        "within",
        "only",
        "just",
        "exclusively",
        "ignore",
        "ignores",
        "ignoring",
        "exclude",
        "excludes",
        "excluding",
        "excluded",
        "except",
        "excepting",
        "omit",
        "omits",
        "omitting",
        "without",
        "minus",
        "no",
        "non",
        "not",
        "any",
        "all",
        "restricted",
        "limited",
        "scoped",
    }
)

#: Cues that make the word before them a filter.
POSTFIX_CUES = frozenset(
    {
        "only",
        "exclusively",
        "excluded",
        # asset_class treats these three as postfix negations. Without them here
        # a strict alias is not filter-shaped at all, so "commercial omitted"
        # would be read as no filter rather than as an exclusion, and the two
        # halves of one rule would disagree about the same sentence.
        "omitted",
        "removed",
        "included",
        "countries",
        "country",
        "markets",
        "market",
        "region",
        "regions",
        "nations",
        "properties",
        "property",
        "segment",
        "segments",
        "sector",
        "sectors",
        "class",
        "classes",
    }
)

#: How far a cue reaches. Before is looser than after because English puts
#: quantifiers between the two ("ignore all residential"), while a word two
#: tokens ahead of a cue is usually just a noun ("commercial vehicles in ...").
PREFIX_REACH = 2
POSTFIX_REACH = 1


def tokens_of(question: str) -> list[str]:
    return norm_value(question).split()


def phrase_positions(tokens: Sequence[str], phrase: str) -> list[int]:
    """Start indexes where ``phrase`` occurs as a whole-token run."""
    want = norm_value(phrase).split()
    if not want:
        return []
    n = len(want)
    return [i for i in range(len(tokens) - n + 1) if list(tokens[i : i + n]) == want]


def is_filter_shaped(tokens: Sequence[str], phrase: str) -> bool:
    """Does ``phrase`` appear next to a cue that makes it a filter?"""
    want = norm_value(phrase).split()
    for start in phrase_positions(tokens, phrase):
        before = tokens[max(0, start - PREFIX_REACH) : start]
        if any(t in PREFIX_CUES for t in before):
            return True
        end = start + len(want)
        after = tokens[end : end + POSTFIX_REACH]
        if any(t in POSTFIX_CUES for t in after):
            return True
    return False


def mentions(
    tokens: Sequence[str], alias: str, *, strict_aliases: frozenset[str] = frozenset()
) -> bool:
    """Whole-token match, with a cue required for a strict alias.

    Never a substring match: "let" inside "letter" and "rent" inside "current"
    are not tenure words, and a substring rule cannot tell those apart.
    """
    if not phrase_positions(tokens, alias):
        return False
    if norm_value(alias) in strict_aliases:
        return is_filter_shaped(tokens, alias)
    return True


def strict_set(aliases: Sequence[str]) -> frozenset[str]:
    """Normalise a strict-alias declaration once, at import."""
    return frozenset(norm_value(a) for a in aliases if norm_value(a))


__all__ = [
    "POSTFIX_CUES",
    "POSTFIX_REACH",
    "PREFIX_CUES",
    "PREFIX_REACH",
    "is_filter_shaped",
    "mentions",
    "phrase_positions",
    "strict_set",
    "tokens_of",
]
