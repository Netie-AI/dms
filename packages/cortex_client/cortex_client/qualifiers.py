"""QUAL-GUARD-01 — deterministic question qualifiers and coverage.

No LLM. No network. Extract time grain / time filter / group-by / named
filter values and check they appear in the executed SQL or typed plan.
A dropped qualifier is a named abstain, never a silent ungrouped answer.

Swap: Cortex HTTP qualifier-check behind the same function names.
"""

from __future__ import annotations

import json
import re
from typing import Any

TIME_GRAINS = ("day", "week", "month", "quarter", "year")
KIND_TIME_GRAIN = "time_grain"
KIND_TIME_FILTER = "time_filter"
KIND_GROUP_BY = "group_by"
KIND_NAMED_FILTER = "named_filter"

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


# ponytail: closed rank/skip/exclusion grammar, not a parser. Ceiling: a
# window built with ROW_NUMBER() or FETCH NEXT, and unseen synonyms
# ("MoM", "all but the podium"). Upgrade: Cortex HTTP qualifier-check.
_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_NUM_ALT = r"(?:\d{1,3}|" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")"
_SKIP_RE = re.compile(
    r"\b(?:skip(?:ping)?|ignor(?:e|ing)|omit(?:ting)?|past|beyond|after)\s+"
    r"(?:the\s+)?first\s+(" + _NUM_ALT + r")\b",
    re.I,
)
_NEXT_RE = re.compile(
    r"\b(?:next|following)\s+(" + _NUM_ALT + r")\b",
    re.I,
)
_BOTTOM_RE = re.compile(
    r"\b(?:bottom|lowest|worst)\s+(" + _NUM_ALT + r")\b",
    re.I,
)
_EXCL_BOTTOM_RE = re.compile(
    r"\b(?:excluding|except(?:ing)?|without|minus)\s+(?:the\s+)?"
    r"(?:bottom|lowest|worst)\s+(" + _NUM_ALT + r")\b",
    re.I,
)
_RANGE_RE = re.compile(
    r"\b(?:ranks?|positions?|numbers?|nos?)\s*#?\s*("
    + _NUM_ALT
    + r")(?:st|nd|rd|th)?\s*(?:to|through|thru|-)\s*#?\s*("
    + _NUM_ALT
    + r")(?:st|nd|rd|th)?\b",
    re.I,
)
_EXCL_LIT_RE = re.compile(
    r"\b(?:excluding|exclude|except(?:\s+for)?|ignor(?:e|ing)|without|minus)\s+"
    r"(?:the\s+)?(?P<body>sku(?:[\s_\-]+[a-z0-9]+)+|[a-z0-9]*\d[a-z0-9-]*"
    r"|[a-z0-9]+(?:-[a-z0-9]+)+)",
    re.I,
)
_TOP_LIMIT_RE = re.compile(r"\btop\s+(" + _NUM_ALT + r")\b", re.I)
_SQL_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.I)
_SQL_OFFSET_RE = re.compile(r"\boffset\s+(\d+)\b", re.I)
_NOT_IN_RE = re.compile(r"\bnot\s+in\s*\(([^)]*)\)", re.I | re.S)
_NEQ_RE = re.compile(r"(?:<>|!=)\s*'([^']*)'", re.I)
_ORDER_RE = re.compile(
    r"\border\s+by\s+(.+?)(?:\blimit\b|\boffset\b|$)",
    re.I | re.S,
)


def _num(raw: str | None) -> int | None:
    if not raw:
        return None
    token = raw.lower()
    n = int(token) if token.isdigit() else _NUM_WORDS.get(token)
    if n is None or not 1 <= n <= 1000:
        return None
    return n


def _phrase(question: str, start: int, end: int) -> str:
    chunk = re.sub(r"\s+", " ", (question or "")[start:end]).strip(" ,.;")
    return chunk.lower()


def exclusion_literals(question: str) -> tuple[str, ...]:
    """SKU-shaped tokens in an exclusion clause. Not 'excluding top N'."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _EXCL_LIT_RE.finditer(question or ""):
        body = re.sub(r"\s+", " ", m.group("body")).strip()
        key = body.lower()
        if body and key not in seen:
            seen.add(key)
            out.append(body)
    return tuple(out)


def _norm_code(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", (value or "").lower())


def match_exclusion_literal(literal: str, known: set[str]) -> str | None:
    """Canonical known value for a question literal, or None if it is not in the data."""
    raw = re.sub(r"[\s_]+", "-", literal.strip())
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    low = raw.lower()
    by_low = {k.lower(): k for k in known}
    if low in by_low:
        return by_low[low]
    want = _norm_code(low)
    for key in known:
        if _norm_code(key) == want:
            return key
    tail = low.split("-")[-1]
    hits = [k for k in known if k.lower() == tail or k.lower().endswith("-" + tail)]
    if len(hits) == 1:
        return hits[0]
    return None


def _primary_desc(sql: str) -> bool | None:
    m = _ORDER_RE.search(sql or "")
    if not m:
        return None
    first = m.group(1).split(",")[0]
    if re.search(r"\bdesc\b", first, re.I):
        return True
    return False


def _window_limits(sql: str | None, plan: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """(offset, limit) from SQL, else from a typed plan. Missing offset is 0 only with a limit."""
    if sql:
        lims = _SQL_LIMIT_RE.findall(sql)
        offs = _SQL_OFFSET_RE.findall(sql)
        if not lims:
            return None, None
        off = int(offs[-1]) if offs else 0
        return off, int(lims[-1])
    if isinstance(plan, dict):
        lim = plan.get("limit")
        if not isinstance(lim, int):
            return None, None
        off = plan.get("offset", 0)
        if not isinstance(off, int):
            off = 0
        return off, lim
    return None, None


def _honors_window(
    *,
    sql: str | None,
    plan: dict[str, Any] | None,
    offset: int,
    limit: int,
    asc: bool,
) -> bool:
    off, lim = _window_limits(sql, plan)
    if off != offset or lim != limit:
        return False
    if sql:
        desc = _primary_desc(sql)
        if desc is None:
            return False
        return (not desc) if asc else desc
    if isinstance(plan, dict):
        # Compile default is DESC. An explicit order_desc False is ASC.
        desc = plan.get("order_desc", True) is not False
        return (not desc) if asc else desc
    return False


def _negated_codes(sql: str | None, plan: dict[str, Any] | None) -> set[str]:
    found: set[str] = set()
    text = sql or ""
    for m in _NOT_IN_RE.finditer(text):
        for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", m.group(1)):
            found.add(a or b)
    found.update(_NEQ_RE.findall(text))
    if isinstance(plan, dict):
        for item in plan.get("filters") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 4 and str(item[2]) in {"<>", "!="}:
                found.add(str(item[3]))
    return found


def _code_in(literal: str, found: set[str]) -> bool:
    want = _norm_code(literal)
    if not want:
        return False
    for item in found:
        got = _norm_code(item)
        if got == want or got.endswith(want) or want.endswith(got):
            return True
    return False


def _limit_matches(question: str, sql: str | None, plan: dict[str, Any] | None) -> bool:
    m = _TOP_LIMIT_RE.search(question or "")
    if not m:
        return True
    want = _num(m.group(1))
    if want is None:
        return False
    _off, lim = _window_limits(sql, plan)
    return lim == want


def _reversed_window(question: str) -> tuple[int, int, str] | str | None:
    m = _RANGE_RE.search(question or "")
    if not m:
        return None
    a, b = _num(m.group(1)), _num(m.group(2))
    phrase = _phrase(question, m.start(), m.end())
    if a is None or b is None or a == b:
        return f"rank_window_ambiguous:{phrase}"
    if a < b:
        # Forward range belongs to the rank-window lane. This guard stays quiet.
        return None
    return b - 1, a - b + 1, phrase


def _bottom_window(question: str) -> tuple[int, int, str] | str | None:
    q = question or ""
    excl = _EXCL_BOTTOM_RE.search(q)
    if not excl:
        return None
    n = _num(excl.group(1))
    outer = None
    for m in _BOTTOM_RE.finditer(q):
        if not (m.end() <= excl.start() or m.start() >= excl.end()):
            continue
        outer = m
        break
    end = excl.end() if outer is None else max(excl.end(), outer.end())
    start = excl.start() if outer is None else min(excl.start(), outer.start())
    phrase = _phrase(q, start, end)
    k = _num(outer.group(1)) if outer is not None else None
    if k is None or n is None or k <= n:
        return f"rank_window_ambiguous:{phrase}"
    return n, k - n, phrase


def _skip_window(question: str) -> tuple[int, int] | str | None:
    q = question or ""
    skip = _SKIP_RE.search(q)
    if not skip:
        return None
    nxt = _NEXT_RE.search(q)
    n = _num(skip.group(1))
    m = _num(nxt.group(1)) if nxt else None
    if n is None or m is None:
        end = skip.end() if nxt is None else max(skip.end(), nxt.end())
        start = skip.start() if nxt is None else min(skip.start(), nxt.start())
        return f"rank_window_ambiguous:{_phrase(q, start, end)}"
    return n, m


def rank_words_present(question: str) -> bool:
    """True when the question uses a rank, skip, or SKU-exclusion word."""
    return (
        _RANGE_RE.search(question or "") is not None
        or _EXCL_BOTTOM_RE.search(question or "") is not None
        or _SKIP_RE.search(question or "") is not None
        or bool(exclusion_literals(question))
    )


def rank_words_unhonored(
    question: str,
    *,
    sql: str | None = None,
    plan: dict[str, Any] | None = None,
    known_values: set[str] | None = None,
) -> str | None:
    """Named abstain when a rank/skip/exclusion word is not in the compiled SQL.

    A forward ``ranks 4 to 8`` is not this guard's (the rank-window lane owns
    it). A reversed range that the SQL already serves as the low-to-high
    window is honoured. An exclusion literal is honoured only when a negative
    predicate names it; ``known_values`` is the data check.
    """
    rev = _reversed_window(question)
    if isinstance(rev, str):
        return rev
    if rev is not None:
        offset, limit, _phrase_txt = rev
        if _honors_window(sql=sql, plan=plan, offset=offset, limit=limit, asc=False):
            return None
        return "rank_window_reversed"
    bot = _bottom_window(question)
    if isinstance(bot, str):
        return bot
    if bot is not None:
        offset, limit, _phrase_txt = bot
        if _honors_window(sql=sql, plan=plan, offset=offset, limit=limit, asc=True):
            return None
        return f"unhonored_qualifier:rank_window=asc,offset={offset},limit={limit}"
    skip = _skip_window(question)
    if isinstance(skip, str):
        return skip
    if skip is not None:
        offset, limit = skip
        if _honors_window(sql=sql, plan=plan, offset=offset, limit=limit, asc=False):
            return None
        return f"unhonored_qualifier:rank_window=offset={offset},limit={limit}"
    literals = exclusion_literals(question)
    if not literals:
        return None
    found = _negated_codes(sql, plan)
    limit_ok = _limit_matches(question, sql, plan)
    for lit in literals:
        if known_values is not None and match_exclusion_literal(lit, known_values) is None:
            return f"unhandled_exclusion:{lit}"
        if known_values is None and not _code_in(lit, found):
            # No data check and the SQL does not name the literal: not validated.
            return f"unhandled_exclusion:{lit}"
        if not _code_in(lit, found) or not limit_ok:
            return f"unhonored_qualifier:exclusion={lit}"
    return None


def unhonored_qualifier_reason(
    question: str,
    *,
    sql: str | None = None,
    plan: dict[str, Any] | None = None,
    known_values: set[str] | None = None,
) -> str | None:
    """First uncovered qualifier as ``unhonored_qualifier:<kind>=<value>``.

    Rank, skip, and entity-exclusion words use the same gate. A dropped one
    is a named abstain (``rank_window_reversed``, ``rank_window_ambiguous``,
    ``unhandled_exclusion``, or ``unhonored_qualifier``).
    """
    gap = rank_words_unhonored(
        question, sql=sql, plan=plan, known_values=known_values
    )
    if gap:
        return gap
    for kind, value in extract_qualifiers(question):
        ok = False
        if kind == KIND_TIME_GRAIN:
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
    "KIND_TIME_FILTER",
    "KIND_TIME_GRAIN",
    "TIME_GRAINS",
    "apply_qualifiers_to_retry_plan",
    "exclusion_literals",
    "extract_qualifiers",
    "match_exclusion_literal",
    "qualifier_reason",
    "rank_words_present",
    "rank_words_unhonored",
    "retry_plan_covers_qualifiers",
    "unhonored_qualifier_reason",
]
