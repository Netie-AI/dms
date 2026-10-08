"""QUAL-GUARD-01 — deterministic question qualifiers and coverage.

No LLM. No network. Extract time grain / time filter / group-by / named
filter values and check they appear in the executed SQL or typed plan.
A dropped qualifier is a named abstain, never a silent ungrouped answer.

Swap: Cortex HTTP qualifier-check behind the same function names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

TIME_GRAINS = ("day", "week", "month", "quarter", "year")
KIND_TIME_GRAIN = "time_grain"
KIND_TIME_FILTER = "time_filter"
KIND_GROUP_BY = "group_by"
KIND_NAMED_FILTER = "named_filter"
KIND_RANK_WINDOW = "rank_window"

# ponytail: closed needle list, not a parser. Ceiling: unseen synonyms
# ("MoM", "fiscal period"). Upgrade: Cortex HTTP qualifier-check.
_GRAIN_GROUP: dict[str, list[str]] = {
    "day": ["day", "day"],
    "week": ["day", "week"],
    "month": ["day", "month"],
    "quarter": ["day", "quarter"],
    "year": ["day", "year"],
}
_DIM_GROUP: dict[str, list[str]] = {
    "supplier": ["supplier", "supplier_id"],
    "category": ["product", "category"],
    "country": ["supplier", "country"],
    "destination": ["location", "location_code"],
    "plant": ["location", "location_code"],
    "warehouse": ["location", "location_code"],
    "sku": ["product", "sku"],
    "lane": ["lane", "origin_plant_id"],
    "day": ["day", "day"],
    "week": ["day", "week"],
    "month": ["day", "month"],
    "quarter": ["day", "quarter"],
    "year": ["day", "year"],
}

# Longest needles first. "by supplier country" must win over "by supplier".
_DIM_NEEDLES: tuple[tuple[str, str], ...] = (
    ("supplier country", "country"),
    ("by destination", "destination"),
    ("per destination", "destination"),
    ("by location", "destination"),
    ("in each category", "category"),
    ("category sales", "category"),
    ("categories by", "category"),
    ("by category", "category"),
    ("per category", "category"),
    ("categoty", "category"),
    ("by plant", "plant"),
    ("by warehouse", "warehouse"),
    ("by supplier", "supplier"),
    ("by sku", "sku"),
    ("selling sku", "sku"),
    ("skus by", "sku"),
    ("by lane", "lane"),
    ("per lane", "lane"),
    ("by day", "day"),
    ("per day", "day"),
    ("each day", "day"),
    ("by week", "week"),
    ("per week", "week"),
    ("each week", "week"),
    ("by month", "month"),
    ("per month", "month"),
    ("each month", "month"),
    ("by quarter", "quarter"),
    ("per quarter", "quarter"),
    ("each quarter", "quarter"),
    ("by year", "year"),
    ("per year", "year"),
    ("each year", "year"),
)

_LY_GRAIN = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
    "quarterly": "quarter",
    "yearly": "year",
    "annually": "year",
}
_TIME_UNITS = {
    "day": "day",
    "days": "day",
    "week": "week",
    "weeks": "week",
    "month": "month",
    "months": "month",
    "quarter": "quarter",
    "quarters": "quarter",
    "year": "year",
    "years": "year",
}
_MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
)
_TIME_FILTER_RE = re.compile(
    r"\b(?P<which>last|this|next|past|previous|prior)\s+"
    r"(?:(?P<n>\d+)\s+)?"
    r"(?P<unit>days?|weeks?|months?|quarters?|years?)\b",
    re.I,
)
_LY_RE = re.compile(
    r"\b(daily|weekly|monthly|quarterly|yearly|annually)\b",
    re.I,
)
_GRAIN_PHRASE_RE = re.compile(
    r"\b(?:per|by|each)\s+(days?|weeks?|months?|quarters?|years?)\b",
    re.I,
)
_NAMED_MONTH_RE = re.compile(
    r"\b(" + "|".join(_MONTH_NAMES) + r")\b(?:\s+(19|20)\d{2})?",
    re.I,
)
_NAMED_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_QUOTED_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")
_WH_A_RE = re.compile(r"\b(warehouse a|wh-a)\b", re.I)
_WH_A_ALIASES = ("warehouse a", "wh-a", "wh_a", "warehouse_a")

# Ranking metric after by/per — not a dimension.
_MEASURE_TAILS = frozenset(
    {
        "revenue",
        "sales",
        "value",
        "score",
        "quantity",
        "qty",
        "cost",
        "spend",
        "risk",
        "lead",
        "time",
        "combined",
        "volume",
        "kg",
        "myr",
        "usd",
        "amount",
        "total",
        "count",
        "utilisation",
        "utilization",
        "weight",
    }
)


def qualifier_reason(kind: str, value: str) -> str:
    return f"unhonored_qualifier:{kind}={value}"


# --- RANK-WINDOW-01: "excluding top 3, next 5" grammar. No pack, no model. ---

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20,
}
_ORD_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "fifteenth": 15, "twentieth": 20,
}
_NUM = r"(\d{1,3}|" + "|".join(_NUM_WORDS) + r")"
_ORD = r"(?:(\d{1,3})(?:st|nd|rd|th)|(" + "|".join(_ORD_WORDS) + r"))"
_TO = r"\s*(?:-|–|—|to|through|thru|and)\s*"
_EXCL_TOP_RE = re.compile(
    r"\b(?:excluding|exclude|except(?:\s+for)?|skip(?:ping)?|ignor(?:e|ing)|"
    r"without|after|beyond|past|below|other\s+than|outside(?:\s+of)?|"
    r"not\s+(?:in\s+)?)\s+(?:the\s+)?top\s+" + _NUM + r"\b"
)
_NEXT_RE = re.compile(r"\b(?:next|following)\s+" + _NUM + r"\b|\b" + _NUM + r"\s+more\b")
_SHOW_N_RE = re.compile(
    r"\b(?:show|list|give(?:\s+me)?|get|what\s+are|which\s+are)\s+(?:the\s+)?"
    + _NUM + r"\b"
)
_RANKS_RE = re.compile(
    r"\b(?:ranks?|ranked|ranking|positions?|numbers?|nos?\.?|#)\s*#?\s*" + _NUM
    + r"(?:st|nd|rd|th)?" + _TO + r"#?\s*" + _NUM + r"(?:st|nd|rd|th)?\b"
)
_ORD_RANGE_RE = re.compile(r"\b" + _ORD + _TO + _ORD + r"\b")
# sku / skus / sku's / skus' / skys (the founder typo). Whole word only.
_ENTITY_RES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk[uy](?:'?s|s')?\b"), "sku"),
    (re.compile(r"\bsuppliers?\b"), "supplier"),
    (re.compile(r"\bcategor(?:y|ies)\b"), "category"),
    (re.compile(r"\bwarehouses?\b"), "warehouse"),
    (re.compile(r"\bplants?\b"), "plant"),
)
_BY_PHRASE_RE = re.compile(r"\bby\s+([a-z][a-z0-9 _'-]{0,40}?)\s*(?:[?.!,;]|$)")
_SELLING_RE = re.compile(
    r"\b(?:most|best|top|highest)[\s-]+sell(?:ing|er|ers)?\b"
    r"|\bsell(?:s|ing)?\s+(?:the\s+)?most\b"
    r"|\bsold\s+(?:the\s+)?most\b"
)
_TOP_RE = re.compile(r"\btop\s+" + _NUM + r"\b")
# Noun sitting on the excluded "top M" ("top 3 warehouses", "top 3 in warehouse").
_NOUN_AFTER_RE = re.compile(
    r"\s+(?:(?:of|in)\s+)?(?:the\s+)?"
    r"(sk[uy](?:'?s|s')?|suppliers?|categor(?:y|ies)|warehouses?|plants?)\b"
)
_UNIT_WORD_RE = re.compile(r"\bunits?\b")
_QTY_RE = re.compile(r"\b(?:quantity|qty|kg|kilos?|kilograms?)\b")
_REVENUE_RE = re.compile(r"\b(?:revenue|sales|turnover)\b")
# outbound_kg is weight. It is never a units measure.
QUANTITY_MEASURES = ("outbound_kg", "quantity_sold")
REVENUE_MEASURES = ("outbound_value_myr", "revenue", "sales")
# Allow-list of measure words the rank-window keyword match may lock.
# Keep in lockstep with _REVENUE_RE, _QTY_RE and _UNIT_WORD_RE.
# ponytail: closed list, not a parser. A synonym that is not listed abstains
# (lost coverage) instead of swallowing the tokens that follow it.
# Upgrade: Cortex HTTP qualifier-check.
_MEASURE_KEYWORDS = frozenset({
    "revenue", "sales", "turnover",
    "quantity", "qty", "kg", "kilo", "kilos", "kilogram", "kilograms",
    "unit", "units",
})
# Words that may sit inside an exact measure phrase ("quantity sold",
# "units and revenue", "the total revenue"). Not the question-level filler
# list: "please", "for", "with", "from", "only", "that" after "by" are leftovers.
_MEASURE_PHRASE_FILLERS = frozenset({
    "sold", "and", "the", "a", "of", "total",
})
# Closed. A token not in this list, and not part of the window, the one entity
# noun or an allow-listed measure word, is rank_window_unhandled_terms.
# "in" / "at" are not fillers: "in warehouse A" and "at WH-B" are filters we
# do not compile. This list does not forgive a token inside a by-phrase.
_FILLERS = frozenset(
    {
        "the", "of", "our", "what", "whats", "what's", "are", "is", "which",
        "me", "please", "show", "list", "give", "get", "by", "for", "to",
        "and", "or", "that", "this", "their", "do", "does", "did", "my", "we",
        "you", "how", "many", "was", "were", "be", "there", "those", "these",
        "some", "any", "all", "its", "it", "on", "with", "from", "than",
        "who", "whom", "just", "also", "only", "s",
    }
)


def _num(raw: str | None) -> int | None:
    if not raw:
        return None
    return int(raw) if raw.isdigit() else _NUM_WORDS.get(raw) or _ORD_WORDS.get(raw)


@dataclass(frozen=True)
class RankWindow:
    """Ranks ``offset+1 .. offset+limit``. ``limit`` None is not servable."""

    offset: int
    limit: int | None
    entity: str | None
    measure_phrase: str
    span: tuple[int, int]
    # Noun on the excluded "top M", when one was written there.
    excluded_entity: str | None = None

    @property
    def label(self) -> str:
        first = self.offset + 1
        return f"{first}-{self.offset + self.limit}" if self.limit else f"{first}+"


def _window_from_range(a: int | None, b: int | None) -> tuple[int, int] | None:
    if a is None or b is None or not (1 <= a <= b <= 1000) or a == 1:
        return None
    return a - 1, b - a + 1


def _entity_hits(qn: str) -> list[tuple[int, int, str]]:
    hits: list[tuple[int, int, str]] = []
    for pat, name in _ENTITY_RES:
        for m in pat.finditer(qn):
            hits.append((m.start(), m.end(), name))
    hits.sort()
    return hits


def _noun_name(raw: str) -> str:
    for pat, name in _ENTITY_RES:
        if pat.fullmatch(raw):
            return name
    return ""


def _excluded_noun(qn: str) -> tuple[str | None, tuple[int, int] | None]:
    """The entity noun written on the excluded ``top M``, if any."""
    excl = _EXCL_TOP_RE.search(qn)
    if excl is None:
        return None, None
    m = _NOUN_AFTER_RE.match(qn, excl.end())
    if m is None:
        return None, None
    return _noun_name(m.group(1)) or None, (m.start(1), m.end(1))


def _ranked_entity(
    qn: str,
) -> tuple[str | None, str | None, tuple[int, int] | None]:
    """(entity to rank, noun on the excluded top, that noun's span).

    Exactly one entity noun, or the excluded noun when it is the only one.
    Two nouns, or none, leave the entity empty; the shape check names why.
    """
    excluded, span = _excluded_noun(qn)
    ranked: list[str] = []
    for start, end, name in _entity_hits(qn):
        if span is not None and not (end <= span[0] or start >= span[1]):
            continue
        if name not in ranked:
            ranked.append(name)
    if len(ranked) == 1:
        return ranked[0], excluded, span
    if not ranked and excluded:
        return excluded, excluded, span
    return None, excluded, span


def parse_rank_window(question: str) -> RankWindow | None:
    """Rank window the question asks for, or None. ``ranks 1-5`` is plain top-N.

    ``top N excluding top M`` keeps ``limit = N - M`` when N > M. N <= M, or
    no count at all, leaves ``limit`` None (not servable). ``ranks 8 to 4``
    is not a window: the ends are not swapped.
    """
    qn = (question or "").lower()
    offset: int | None = None
    limit: int | None = None
    span: tuple[int, int] | None = None
    excl = _EXCL_TOP_RE.search(qn)
    if excl:
        offset = _num(excl.group(1))
        rest = qn[: excl.start()] + " " * (excl.end() - excl.start()) + qn[excl.end():]
        nxt = _NEXT_RE.search(rest) or _SHOW_N_RE.search(rest)
        if nxt:
            limit = _num(next(g for g in nxt.groups() if g))
            span = (min(excl.start(), nxt.start()), max(excl.end(), nxt.end()))
        else:
            # A leftover "top N" is the kept width, not a second exclusion.
            top = _TOP_RE.search(rest)
            if top is not None and offset is not None:
                n = _num(top.group(1))
                if n is not None and n > offset:
                    limit = n - offset
            span = (excl.start(), excl.end())
        if not offset or (limit is not None and limit < 1):
            return None
    else:
        rng = _RANKS_RE.search(qn)
        if rng:
            got = _window_from_range(_num(rng.group(1)), _num(rng.group(2)))
        else:
            rng = _ORD_RANGE_RE.search(qn)
            got = (
                _window_from_range(
                    _num(rng.group(1) or rng.group(2)), _num(rng.group(3) or rng.group(4))
                )
                if rng
                else None
            )
        if rng is None or got is None:
            return None
        offset, limit = got
        span = (rng.start(), rng.end())
    entity, excluded, _span = _ranked_entity(qn)
    by = _BY_PHRASE_RE.search(qn)
    phrase = by.group(1).strip() if by else ""
    if phrase.split(" ", 1)[0] in _DIM_GROUP:
        phrase = ""
    selling = _SELLING_RE.search(qn)
    if not phrase and selling is not None:
        phrase = selling.group(0)
    assert span is not None
    return RankWindow(offset, limit, entity, phrase, span, excluded)


def rank_window_group(win: RankWindow) -> tuple[str, str] | None:
    """(object, column) the window ranks, or None when no entity was named."""
    pair = _DIM_GROUP.get(win.entity or "")
    return (pair[0], pair[1]) if pair else None


def _true_units_measure(measures: dict[str, str]) -> str | None:
    """A measure whose own name or description says unit/units. kg is not one."""
    for name in sorted(measures):
        words = set(re.findall(
            r"[a-z0-9]+", f"{name} {measures[name]}".lower().replace("_", " ")
        ))
        if "unit" in words or "units" in words:
            return name
    return None


def _by_phrase_tokens(qn: str) -> list[str] | None:
    """Tokens after ``by``, or None when the question has no by-phrase."""
    by = _BY_PHRASE_RE.search(qn)
    if by is None:
        return None
    return re.findall(r"[a-z0-9]+", by.group(1))


def _by_phrase_extra_tokens(qn: str) -> list[str]:
    """Tokens after ``by`` that are not the measure or its own fillers.

    Empty when there is no by-phrase, when the phrase is only measure words,
    and when it names no known measure keyword (that path is
    ``unknown_measure``, not a dropped filter). Any other token, including
    ones that are question-level fillers, is a leftover. Allow-list: there
    is no list of places, suppliers, or direction words.
    """
    toks = _by_phrase_tokens(qn)
    if not toks or not any(t in _MEASURE_KEYWORDS for t in toks):
        return []
    return sorted({
        t for t in toks
        if t not in _MEASURE_KEYWORDS and t not in _MEASURE_PHRASE_FILLERS
    })


def _blank_allowlisted_by_tokens(by: re.Match[str]) -> list[tuple[int, int]]:
    """Spans of ``by`` plus the measure words. The rest of the phrase stays."""
    spans = [(by.start(), by.start() + len("by"))]
    base = by.start(1)
    for m in re.finditer(r"[a-z0-9]+", by.group(1)):
        tok = m.group(0)
        if tok in _MEASURE_KEYWORDS or tok in _MEASURE_PHRASE_FILLERS:
            spans.append((base + m.start(), base + m.end()))
    return spans


def _window_blanks(qn: str, win: RankWindow) -> list[tuple[int, int]]:
    """Spans the shape check may ignore. The gap between them is not ignored."""
    blanks: list[tuple[int, int]] = []
    excl = _EXCL_TOP_RE.search(qn)
    if excl is not None:
        blanks.append((excl.start(), excl.end()))
        rest = qn[: excl.start()] + " " * (excl.end() - excl.start()) + qn[excl.end():]
        nxt = _NEXT_RE.search(rest) or _SHOW_N_RE.search(rest)
        if nxt is not None:
            blanks.append((nxt.start(), nxt.end()))
        else:
            top = _TOP_RE.search(rest)
            if top is not None:
                blanks.append((top.start(), top.end()))
    else:
        blanks.append(win.span)
    for start, end, name in _entity_hits(qn):
        if name == win.entity or name == win.excluded_entity:
            blanks.append((start, end))
    selling = _SELLING_RE.search(qn)
    if selling is not None:
        blanks.append((selling.start(), selling.end()))
    by = _BY_PHRASE_RE.search(qn)
    if by is not None:
        # The regex runs to the next punctuation. Blank only the measure
        # words when anything else is in that span; an unknown measure name
        # ("profit margin") is still the whole phrase.
        if _by_phrase_extra_tokens(qn):
            blanks.extend(_blank_allowlisted_by_tokens(by))
        else:
            blanks.append((by.start(), by.end()))
    return blanks


def _mask_spans(qn: str, spans: list[tuple[int, int]]) -> str:
    masked = list(qn)
    for start, end in spans:
        for i in range(max(start, 0), min(end, len(masked))):
            masked[i] = " "
    return "".join(masked)


def _is_calendar_year(tok: str) -> bool:
    """Same year shape QUAL-GUARD already extracts as ``time_filter``."""
    return _NAMED_YEAR_RE.fullmatch(tok) is not None


def _stray_digits(text: str) -> list[str]:
    """Digit tokens that are not calendar years. Window spans are already blank."""
    return sorted({
        tok for tok in re.findall(r"[a-z0-9]+", text)
        if tok.isdigit() and not _is_calendar_year(tok)
    })


def _plain_ranking_spans(qn: str) -> list[tuple[int, int]]:
    """Number spans of a top / next / ranks phrase that is not an offset window."""
    spans: list[tuple[int, int]] = []
    for rx in (_TOP_RE, _NEXT_RE, _SHOW_N_RE, _RANKS_RE, _ORD_RANGE_RE):
        spans.extend((m.start(), m.end()) for m in rx.finditer(qn))
    return spans


def _stray_digit_reason(qn: str, spans: list[tuple[int, int]]) -> str | None:
    digits = _stray_digits(_mask_spans(qn, spans))
    if not digits:
        return None
    return "ungrounded_qualifier:" + ",".join(digits)


def rank_window_shape_reason(question: str) -> str | None:
    """Why this window cannot be compiled, or None when the grammar is clean.

    Clean means exactly one entity, the noun on ``top M`` is that entity or
    absent, nothing left after the window, the entity, and the measure words
    (not the rest of the by-phrase), and a finite limit. A token after ``by``
    that is not in ``_MEASURE_KEYWORDS`` or ``_MEASURE_PHRASE_FILLERS`` is
    ``rank_window_unhandled_terms``. A digit outside the recognised ranking
    spans (anywhere in the question, not only before ``by``) is
    ``ungrounded_qualifier``. A calendar year is not that digit: QUAL-GUARD
    ``time_filter`` still names it. No ontology and no pack.
    """
    win = parse_rank_window(question)
    qn = (question or "").lower()
    if win is None:
        # "top 5 ... not 10023" / "next 5 ... excluding 3" are not offset
        # windows. A number outside the top/next/ranks span still must not
        # fall through to a ranking that drops it.
        spans = _plain_ranking_spans(qn)
        if not spans:
            return None
        return _stray_digit_reason(qn, spans)
    _entity, excluded, span = _ranked_entity(qn)
    outside: list[str] = []
    for start, end, name in _entity_hits(qn):
        if span is not None and not (end <= span[0] or start >= span[1]):
            continue
        if name not in outside:
            outside.append(name)
    if excluded and outside and excluded not in outside:
        return f"rank_window_entity_mismatch:{excluded}!={outside[0]}"
    if len(outside) > 1:
        return "rank_window_unhandled_terms:" + ",".join(sorted(outside))
    if win.entity is None:
        return "rank_window_unhandled_terms:no_entity"
    masked = _mask_spans(qn, _window_blanks(qn, win))
    # Same allow-list as before. A word leftover still wins, and a digit that
    # sits next to one (SUP-01) stays in that list. Years stay in it too so a
    # by-phrase year is unchanged; a year outside the by-phrase is left for
    # QUAL-GUARD time_filter.
    scanned = {
        tok for tok in re.findall(r"[a-z0-9]+", masked)
        if tok not in _FILLERS and not tok.isdigit()
    }
    left = sorted(scanned.union(_by_phrase_extra_tokens(qn)))
    words = [t for t in left if not t.isdigit() or _is_calendar_year(t)]
    if words:
        return "rank_window_unhandled_terms:" + ",".join(left)
    # Window numbers are already blank. A remaining digit is not part of the
    # span. Fillers must not hide it.
    digits = _stray_digits(masked)
    if digits:
        return "ungrounded_qualifier:" + ",".join(digits)
    if win.limit is None:
        return "rank_window_open_ended"
    return None


def rank_window_measure(
    question: str, measures: dict[str, str]
) -> tuple[str | None, str | None, str]:
    """(measure, abstain_reason, reading_note) for a rank-window ask.

    Selling words ("most selling", "top selling", "best selling", "sold most")
    read as revenue, the same reading "selling sku" already locks, and the
    note says so. No measure word at all is ``ambiguous_measure:none``.
    "units" maps to a measure that says units; ``outbound_kg`` is weight, so
    with no such measure the reason is ``unknown_measure:units``.
    A by-phrase locks a keyword only when every token is that measure or
    ``_MEASURE_PHRASE_FILLERS``. Any other token is
    ``rank_window_unhandled_terms`` and is not ranked DESC. An explicit
    by-phrase that names no known measure keyword is not overridden by a
    selling word elsewhere in the question.
    """
    qn = (question or "").lower()
    extra = _by_phrase_extra_tokens(qn)
    if extra:
        return None, "rank_window_unhandled_terms:" + ",".join(extra), ""
    win = parse_rank_window(qn)
    phrase = win.measure_phrase if win else ""
    by_toks = _by_phrase_tokens(qn)
    # An explicit by-phrase that is not a known measure ("profit margin")
    # must not pick up "revenue" or "most selling" from the rest of the ask.
    by_unknown = by_toks is not None and not any(t in _MEASURE_KEYWORDS for t in by_toks)
    unit = False if by_unknown else bool(_UNIT_WORD_RE.search(qn))
    qty = False if by_unknown else bool(_QTY_RE.search(qn))
    revenue_word = False if by_unknown else bool(_REVENUE_RE.search(qn))
    selling = None if by_unknown else _SELLING_RE.search(qn)

    def _named(
        names: tuple[str, ...], reading: str, other: str, other_names: tuple[str, ...]
    ) -> tuple[str | None, str | None, str]:
        found = next((n for n in names if n in measures), None)
        if found is None:
            return None, f"unknown_measure:{names[0]}", ""
        other_name = next((n for n in other_names if n in measures), "")
        if selling is not None and not (qty or unit or revenue_word):
            why = f"'{selling.group(0)}' read as"
        else:
            why = "ranked by"
        alt = f", not {other} ({other_name})" if other_name else f", not {other}"
        return found, None, f"{why} {reading} = {found}{alt}"

    if unit and (qty or revenue_word or selling is not None):
        tag = "most_selling" if selling is not None else "units_or_revenue"
        return None, f"ambiguous_measure:{tag}", ""
    if unit:
        found = _true_units_measure(measures)
        if found is None:
            return None, "unknown_measure:units", ""
        return found, None, f"ranked by units = {found}, not kg and not revenue"
    if qty and (revenue_word or selling is not None):
        tag = "most_selling" if selling is not None else "quantity_or_revenue"
        return None, f"ambiguous_measure:{tag}", ""
    if qty:
        return _named(QUANTITY_MEASURES, "quantity sold (kg)", "revenue", REVENUE_MEASURES)
    if revenue_word or selling is not None:
        return _named(REVENUE_MEASURES, "revenue (sales value)", "quantity sold", QUANTITY_MEASURES)
    if phrase:
        toks = set(re.findall(r"[a-z0-9]+", phrase)) - {"the", "a", "of", "total"}

        def _words(text: str) -> set[str]:
            return set(re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")))

        hits = [n for n in sorted(measures) if toks and toks <= _words(n)] or [
            n for n, desc in sorted(measures.items()) if toks and toks <= _words(desc)
        ]
        if len(hits) == 1:
            return hits[0], None, f"ranked by {hits[0]} (named: {phrase})"
        if hits:
            return None, f"ambiguous_measure:{phrase}", ""
        return None, f"unknown_measure:{phrase}", ""
    return None, "ambiguous_measure:none", ""


def extract_qualifiers(question: str) -> tuple[tuple[str, str], ...]:
    """Ordered unique (kind, value) pairs. Deterministic. No model."""
    q = question or ""
    qn = q.lower()
    used: list[tuple[int, int]] = []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _take(start: int, end: int) -> bool:
        if any(not (end <= a or start >= b) for a, b in used):
            return False
        used.append((start, end))
        return True

    def _add(kind: str, value: str) -> None:
        key = (kind, value)
        if key not in seen and value:
            seen.add(key)
            out.append(key)

    win = parse_rank_window(q)
    if win is not None and _take(*win.span):
        _add(KIND_RANK_WINDOW, win.label)

    for m in _TIME_FILTER_RE.finditer(qn):
        if not _take(m.start(), m.end()):
            continue
        n = m.group("n") or "1"
        unit = _TIME_UNITS[m.group("unit").lower()]
        which = m.group("which").lower()
        _add(KIND_TIME_FILTER, f"{which}_{n}_{unit}")

    for m in _NAMED_MONTH_RE.finditer(qn):
        if m.group(0).lower() == "may" and not re.search(r"\bin\s+may\b", qn):
            continue
        if not _take(m.start(), m.end()):
            continue
        _add(KIND_TIME_FILTER, "month=" + m.group(1).lower())

    for m in _NAMED_YEAR_RE.finditer(qn):
        if not _take(m.start(), m.end()):
            continue
        _add(KIND_TIME_FILTER, "year=" + m.group(1))

    for m in _GRAIN_PHRASE_RE.finditer(qn):
        if not _take(m.start(), m.end()):
            continue
        _add(KIND_TIME_GRAIN, _TIME_UNITS[m.group(1).lower()])

    for m in _LY_RE.finditer(qn):
        if not _take(m.start(), m.end()):
            continue
        _add(KIND_TIME_GRAIN, _LY_GRAIN[m.group(1).lower()])

    dim_hits: list[tuple[int, int, str]] = []
    for needle, value in _DIM_NEEDLES:
        if value in TIME_GRAINS:
            continue
        start = 0
        while True:
            at = qn.find(needle, start)
            if at < 0:
                break
            dim_hits.append((at, at + len(needle), value))
            start = at + 1
    dim_hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    for start, end, value in dim_hits:
        if value in _MEASURE_TAILS:
            continue
        if not _take(start, end):
            continue
        _add(KIND_GROUP_BY, value)
    if win is not None and win.entity:
        _add(KIND_GROUP_BY, win.entity)

    for m in _QUOTED_RE.finditer(q):
        raw = (m.group(1) or m.group(2) or "").strip()
        if not raw:
            continue
        if not _take(m.start(), m.end()):
            continue
        _add(KIND_NAMED_FILTER, raw)

    for m in _WH_A_RE.finditer(qn):
        if not _take(m.start(), m.end()):
            continue
        _add(KIND_NAMED_FILTER, "warehouse a")

    return tuple(out)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _plan_blob(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    return json.dumps(plan, default=str).lower()


def _sql_group_blob(sql: str | None) -> str:
    if not sql:
        return ""
    m = re.search(
        r"\bgroup\s+by\b(.+?)(?:\border\s+by\b|\blimit\b|\bhaving\b|$)",
        sql,
        flags=re.I | re.S,
    )
    return (m.group(1) if m else "").lower()


def _sql_where_blob(sql: str | None) -> str:
    if not sql:
        return ""
    m = re.search(
        r"\bwhere\b(.+?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)",
        sql,
        flags=re.I | re.S,
    )
    return (m.group(1) if m else "").lower()


def _honors_time_grain(grain: str, *, sql: str | None, plan: dict[str, Any] | None) -> bool:
    g = grain.lower()
    sql_l = (sql or "").lower()
    if re.search(rf"date_trunc\s*\(\s*['\"]?{re.escape(g)}\b", sql_l):
        return True
    if re.search(rf"\b{re.escape(g)}\s*\(", sql_l):
        return True
    if g in _sql_group_blob(sql):
        return True
    blob = _plan_blob(plan)
    if g in blob:
        return True
    return False


def _honors_group_by(dim: str, *, sql: str | None, plan: dict[str, Any] | None) -> bool:
    token = dim.lower()
    aliases = {token, token.replace(" ", "_"), token.replace(" ", "")}
    if token == "supplier":
        aliases.update({"supplier_id", "supplier_name"})
    if token == "destination":
        aliases.update({"location_code", "destination"})
    if token == "warehouse":
        aliases.update({"location_code", "warehouse"})
    if token == "plant":
        aliases.update({"location_code", "plant_id"})
    group_sql = _sql_group_blob(sql)
    plan_l = _plan_blob(plan)
    hay = group_sql + " " + plan_l
    return any(a in hay for a in aliases)


def _honors_named_filter(value: str, *, sql: str | None, plan: dict[str, Any] | None) -> bool:
    aliases = [value]
    if _norm(value) in {_norm(a) for a in _WH_A_ALIASES}:
        aliases = list(_WH_A_ALIASES)
    where = _sql_where_blob(sql) + " " + (sql or "").lower()
    blob = _plan_blob(plan) + " " + where
    return any(_norm(a) and _norm(a) in _norm(blob) for a in aliases)


def _honors_time_filter(value: str, *, sql: str | None, plan: dict[str, Any] | None) -> bool:
    blob = ((sql or "") + " " + _plan_blob(plan)).lower()
    if "where" not in blob and "filter" not in blob:
        if not isinstance(plan, dict) or not plan.get("filters"):
            if not _sql_where_blob(sql):
                return False
    if value.startswith("year="):
        return value.split("=", 1)[1] in blob
    if value.startswith("month="):
        name = value.split("=", 1)[1]
        return name in blob
    unit = value.rsplit("_", 1)[-1]
    return bool(_sql_where_blob(sql)) or ("interval" in blob) or (unit in blob and "where" in blob)


_SQL_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.I)
_SQL_OFFSET_RE = re.compile(r"\boffset\s+(\d+)\b", re.I)


def _honors_rank_window(
    question: str, *, sql: str | None, plan: dict[str, Any] | None
) -> bool:
    """Outermost OFFSET/LIMIT equal the window, under a tie-broken ORDER BY.

    ponytail: reads the SQL tail, not a parse tree. Ceiling: a window built
    with ROW_NUMBER() or FETCH NEXT abstains. Upgrade: sqlglot over the tail.
    """
    win = parse_rank_window(question)
    if win is None:
        return True
    # An open window has no honest LIMIT. Do not treat "any limit" as a match.
    if win.limit is None:
        return False
    if isinstance(plan, dict) and not sql:
        off = plan.get("offset")
        lim = plan.get("limit")
        return off == win.offset and lim == win.limit
    text = (sql or "").strip().rstrip(";")
    order_at = text.lower().rfind("order by")
    tail = text[order_at:]
    if order_at < 0 or tail.count(")") > tail.count("("):
        return False
    offs = _SQL_OFFSET_RE.findall(tail)
    lims = _SQL_LIMIT_RE.findall(tail)
    if offs != [str(win.offset)]:
        return False
    if win.limit is not None and lims != [str(win.limit)]:
        return False
    keys = re.split(r"\b(?:limit|offset)\b", tail[len("order by"):], flags=re.I)[0]
    return "," in keys


def _positive_offset(plan: dict[str, Any] | None, sql: str | None) -> int | None:
    if sql:
        nums = [int(n) for n in _SQL_OFFSET_RE.findall(sql) if int(n) > 0]
        return nums[-1] if nums else None
    if isinstance(plan, dict):
        off = plan.get("offset")
        if type(off) is int and off > 0:
            return off
    return None


def unhonored_qualifier_reason(
    question: str,
    *,
    sql: str | None = None,
    plan: dict[str, Any] | None = None,
) -> str | None:
    """First uncovered qualifier as ``unhonored_qualifier:<kind>=<value>``.

    Also ``rank_window_open_ended`` when the question's window has no limit,
    and ``unrequested_offset:<n>`` when a plan or SQL carries OFFSET and the
    question asked for no rank window. Main dropped that offset; keeping it
    and serving it is a different question than the one asked.
    """
    win = parse_rank_window(question)
    if win is None:
        n = _positive_offset(plan, sql)
        if n is not None:
            return f"unrequested_offset:{n}"
    elif win.limit is None:
        return "rank_window_open_ended"
    for kind, value in extract_qualifiers(question):
        ok = False
        if kind == KIND_RANK_WINDOW:
            ok = _honors_rank_window(question, sql=sql, plan=plan)
        elif kind == KIND_TIME_GRAIN:
            ok = _honors_time_grain(value, sql=sql, plan=plan)
        elif kind == KIND_GROUP_BY:
            ok = _honors_group_by(value, sql=sql, plan=plan)
        elif kind == KIND_NAMED_FILTER:
            ok = _honors_named_filter(value, sql=sql, plan=plan)
        elif kind == KIND_TIME_FILTER:
            ok = _honors_time_filter(value, sql=sql, plan=plan)
        if not ok:
            return qualifier_reason(kind, value)
    return None


def _group_has(group: list[Any], token: str) -> bool:
    return token.lower() in json.dumps(group, default=str).lower()


def apply_qualifiers_to_retry_plan(
    plan: dict[str, Any], question: str
) -> dict[str, Any]:
    """Add extracted grains/dims onto a ranked retry plan. Does not invent filters."""
    out = dict(plan)
    group = list(out.get("group_by") or [])
    if not isinstance(group, list):
        group = []
    win = parse_rank_window(question)
    if win is not None:
        out["offset"] = win.offset
        if win.limit is not None:
            out["limit"] = win.limit
        else:
            out.pop("limit", None)
    for kind, value in extract_qualifiers(question):
        pair: list[str] | None = None
        if kind == KIND_TIME_GRAIN:
            pair = list(_GRAIN_GROUP.get(value) or [])
        elif kind == KIND_GROUP_BY:
            pair = list(_DIM_GROUP.get(value) or [])
        if pair and not _group_has(group, pair[-1]):
            group.append(pair)
    out["group_by"] = group
    return out


def retry_plan_covers_qualifiers(plan: dict[str, Any] | None, question: str) -> bool:
    """False when sending this retry plan would drop an extracted qualifier."""
    if not isinstance(plan, dict):
        return False
    return unhonored_qualifier_reason(question, plan=plan) is None


__all__ = [
    "KIND_GROUP_BY",
    "KIND_NAMED_FILTER",
    "KIND_RANK_WINDOW",
    "KIND_TIME_FILTER",
    "KIND_TIME_GRAIN",
    "TIME_GRAINS",
    "RankWindow",
    "apply_qualifiers_to_retry_plan",
    "extract_qualifiers",
    "parse_rank_window",
    "qualifier_reason",
    "rank_window_group",
    "rank_window_measure",
    "rank_window_shape_reason",
    "retry_plan_covers_qualifiers",
    "unhonored_qualifier_reason",
]
