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
)
_UNITS_RE = re.compile(r"\b(?:units?|quantity|qty|volume)\b")
_REVENUE_RE = re.compile(r"\b(?:revenue|sales|turnover)\b")
# Candidate DMS measure names per reading, most specific first.
UNITS_MEASURES = ("outbound_kg", "quantity_sold", "units_sold")
REVENUE_MEASURES = ("outbound_value_myr", "revenue", "sales")


def _num(raw: str | None) -> int | None:
    if not raw:
        return None
    return int(raw) if raw.isdigit() else _NUM_WORDS.get(raw) or _ORD_WORDS.get(raw)


@dataclass(frozen=True)
class RankWindow:
    """Ranks ``offset+1 .. offset+limit`` (``limit`` None = every rank after)."""

    offset: int
    limit: int | None
    entity: str | None
    measure_phrase: str
    span: tuple[int, int]

    @property
    def label(self) -> str:
        first = self.offset + 1
        return f"{first}-{self.offset + self.limit}" if self.limit else f"{first}+"


def _window_from_range(a: int | None, b: int | None) -> tuple[int, int] | None:
    if a is None or b is None or not (1 <= a <= b <= 1000) or a == 1:
        return None
    return a - 1, b - a + 1


def parse_rank_window(question: str) -> RankWindow | None:
    """Rank window the question asks for, or None. ``ranks 1-5`` is plain top-N."""
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
    entity = next((name for pat, name in _ENTITY_RES if pat.search(qn)), None)
    by = _BY_PHRASE_RE.search(qn)
    phrase = by.group(1).strip() if by else ""
    if phrase.split(" ", 1)[0] in _DIM_GROUP:
        phrase = ""
    if not phrase and _SELLING_RE.search(qn):
        phrase = "most selling"
    assert span is not None
    return RankWindow(offset, limit, entity, phrase, span)


def rank_window_group(win: RankWindow) -> tuple[str, str] | None:
    """(object, column) the window ranks, or None when no entity was named."""
    pair = _DIM_GROUP.get(win.entity or "")
    return (pair[0], pair[1]) if pair else None


def rank_window_measure(
    question: str, measures: dict[str, str]
) -> tuple[str | None, str | None, str]:
    """(measure, abstain_reason, reading_note) for a rank-window ask.

    ``measures`` maps DMS measure name to its description. Units words pick
    quantity sold, revenue words pick revenue. "Most selling" and no measure
    at all read as revenue - the same reading "selling sku" already locks -
    and the note says so. Both readings named is a named ambiguity.
    """
    qn = (question or "").lower()
    win = parse_rank_window(qn)
    phrase = win.measure_phrase if win else ""
    units = bool(_UNITS_RE.search(qn))
    revenue = bool(_REVENUE_RE.search(qn))
    if units and revenue:
        tag = "most_selling" if _SELLING_RE.search(qn) else "units_or_revenue"
        return None, f"ambiguous_measure:{tag}", ""
    if not (units or revenue) and phrase not in ("", "most selling"):
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
    if units:
        names, reading = UNITS_MEASURES, "quantity sold (units, kg)"
        other = "revenue"
    else:
        names, reading = REVENUE_MEASURES, "revenue (sales value)"
        other = "units sold"
    found = next((n for n in names if n in measures), None)
    if found is None:
        return None, f"unknown_measure:{names[0]}", ""
    other_name = next(
        (n for n in (REVENUE_MEASURES if units else UNITS_MEASURES) if n in measures), ""
    )
    why = (
        "'most selling' read as"
        if phrase == "most selling"
        else "no measure named; read as"
        if not phrase and not (units or revenue)
        else "ranked by"
    )
    alt = f", not {other} ({other_name})" if other_name else f", not {other}"
    return found, None, f"{why} {reading} = {found}{alt}"


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
    if isinstance(plan, dict) and not sql:
        off = plan.get("offset")
        lim = plan.get("limit")
        return off == win.offset and (win.limit is None or lim == win.limit)
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


def unhonored_qualifier_reason(
    question: str,
    *,
    sql: str | None = None,
    plan: dict[str, Any] | None = None,
) -> str | None:
    """First uncovered qualifier as ``unhonored_qualifier:<kind>=<value>``."""
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
    "retry_plan_covers_qualifiers",
    "unhonored_qualifier_reason",
]
