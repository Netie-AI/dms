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
KIND_GRAIN = "grain"

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
# Answer grain named by a list ask ("which locations ...", "list chemicals
# ...") or a warehouse-level show ("show warehouse capacity utilisation").
# The answer rows must be keyed by that entity; a table grouped by some other
# column (capacity_kg) answers a different question.
_GRAIN_NOUNS: dict[str, str] = {
    "location": "warehouse",
    "locations": "warehouse",
    "warehouse": "warehouse",
    "warehouses": "warehouse",
    "supplier": "supplier",
    "suppliers": "supplier",
    "sku": "sku",
    "skus": "sku",
    "item": "sku",
    "items": "sku",
    "product": "sku",
    "products": "sku",
    "chemical": "sku",
    "chemicals": "sku",
}
_LIST_GRAIN_RE = re.compile(
    r"^\s*(?:which|list)\s+(?:[\w-]+\s+){0,2}?(" + "|".join(_GRAIN_NOUNS) + r")\b",
    re.I,
)
_SHOW_GRAIN_RE = re.compile(
    r"^\s*show\s+(?:the\s+|each\s+|every\s+)?(warehouses?|locations?)\b(?!\s+a\b)",
    re.I,
)
# Measure inputs: grouping by one of these is never an entity grain.
_MEASURE_INPUT_COL_RE = re.compile(
    r"\b[a-z_]*(?:capacity_kg|current_load_kg|quantity_kg|reorder_level_kg|"
    r"unit_cost_myr|cost_myr|amount|risk_score|lead_time_days)\b"
)

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

    grain_m = _LIST_GRAIN_RE.search(q) or _SHOW_GRAIN_RE.search(q)
    if grain_m:
        noun = grain_m.group(1).lower()
        _add(KIND_GRAIN, _GRAIN_NOUNS.get(noun, _GRAIN_NOUNS.get(noun.rstrip("s"), "")))

    return tuple(out)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _plan_blob(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    return json.dumps(plan, default=str).lower()


# dms#290 R3: coverage is clause-scoped and word-bounded. A grain word in a
# measure name, a filter value in the SELECT list, "day" inside lead_time_days,
# or "last N months" over an unrelated WHERE no longer counts as honoured.
_CLAUSE_START = {
    "group": re.compile(r"\bgroup\s+by\b", re.I),
    "where": re.compile(r"\bwhere\b", re.I),
    "having": re.compile(r"\bhaving\b", re.I),
    "select": re.compile(r"\bselect\b", re.I),
}
_CLAUSE_STOP = re.compile(
    r"\b(?:group\s+by|order\s+by|having|limit|qualify|window|union|intersect|except|from)\b"
    r"|;",
    re.I,
)
_WHERE_STOP = re.compile(
    r"\b(?:group\s+by|order\s+by|having|limit|qualify|window|union|intersect|except)\b|;",
    re.I,
)
_ORDINAL_RE = re.compile(r"(?:^|,)\s*\d+\s*(?=,|$)")
_CLOCK_ANCHOR_RE = re.compile(
    r"\b(current_date|current_timestamp|now\s*\(|today\s*\(|get_current_timestamp\s*\()"
    r"|\bdate\s*'\d{4}-\d{2}-\d{2}'|'\d{4}-\d{2}-\d{2}",
    re.I,
)
_MONTH_NUM = {
    name: i
    for i, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}


def _word(hay: str, token: str) -> bool:
    """Token as a whole identifier/word. ``day`` does not match ``lead_time_days``."""
    t = token.lower()
    if not t:
        return False
    return re.search(rf"(?<![a-z0-9_]){re.escape(t)}(?![a-z0-9_])", hay.lower()) is not None


def _clauses(sql: str | None, kind: str) -> str:
    """Every ``kind`` clause body, cut at the next clause keyword at its own depth.

    Paren-aware: ``WHERE CAST(d AS DATE) < x`` keeps the whole predicate, and a
    subquery's clause ends at its closing paren.
    """
    if not sql:
        return ""
    stop = _WHERE_STOP if kind in {"where", "having"} else _CLAUSE_STOP
    if kind == "having":
        stop = re.compile(r"\b(?:order\s+by|limit|qualify|window|union|intersect|except)\b|;", re.I)
    out: list[str] = []
    for m in _CLAUSE_START[kind].finditer(sql):
        i = m.end()
        depth = 0
        j = i
        while j < len(sql):
            ch = sql[j]
            if ch == "'":
                k = sql.find("'", j + 1)
                j = len(sql) if k < 0 else k + 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and stop.match(sql, j) and (j == 0 or not sql[j - 1].isalnum()):
                break
            j += 1
        out.append(sql[i:j])
    return " ".join(out).lower()


def _sql_group_blob(sql: str | None) -> str:
    return _clauses(sql, "group")


def _sql_where_blob(sql: str | None) -> str:
    """Every WHERE and HAVING clause. Filters live there, not in SELECT."""
    return (_clauses(sql, "where") + " " + _clauses(sql, "having")).strip()


def _sql_select_blob(sql: str | None) -> str:
    return _clauses(sql, "select")


def _group_key_blob(sql: str | None) -> str:
    """GROUP BY text, plus the SELECT list when GROUP BY uses ordinals or ALL."""
    group = _sql_group_blob(sql)
    if not group.strip():
        return ""
    if _ORDINAL_RE.search(group) or _word(group, "all"):
        return group + " " + _sql_select_blob(sql)
    return group


def _plan_group_blob(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    return json.dumps(plan.get("group_by") or [], default=str).lower()


def _plan_filter_blob(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    return json.dumps(plan.get("filters") or [], default=str).lower()


def _grain_expr(hay: str, grain: str) -> bool:
    g = re.escape(grain.lower())
    return (
        re.search(rf"date_trunc\s*\(\s*['\"]{g}s?['\"]", hay, re.I) is not None
        or re.search(rf"(?<![a-z0-9_]){g}\s*\(", hay, re.I) is not None
        or re.search(rf"\b(?:extract|date_part|datepart)\s*\(\s*['\"]?{g}\b", hay, re.I)
        is not None
        or re.search(rf"time_bucket\s*\(\s*interval\s*'?\s*\d*\s*{g}", hay, re.I)
        is not None
    )


def _honors_time_grain(grain: str, *, sql: str | None, plan: dict[str, Any] | None) -> bool:
    g = grain.lower()
    if sql:
        keys = _group_key_blob(sql)
        if keys and (_grain_expr(keys, g) or _word(keys, g)):
            return True
    if isinstance(plan, dict):
        if _word(_plan_group_blob(plan), g):
            return True
        for key in ("time_grain", "grain"):
            if str(plan.get(key) or "").strip().lower() == g:
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
    hay = _group_key_blob(sql) + " " + _plan_group_blob(plan)
    return any(_word(hay, a) for a in aliases)


def _honors_grain(grain: str, *, sql: str | None, plan: dict[str, Any] | None) -> bool:
    """Answer rows keyed by the asked entity, and not grouped by a measure input.

    SQL: the key is grouped or (ungrouped list SQL) selected. Plan: group_by
    carries it. A GROUP BY on capacity_kg / quantity_kg is a grain mismatch
    even when the key also appears.
    """
    group_sql = _sql_group_blob(sql)
    plan_group = _plan_group_blob(plan)
    if _MEASURE_INPUT_COL_RE.search(group_sql) or _MEASURE_INPUT_COL_RE.search(plan_group):
        return False
    aliases = {
        "warehouse": ("location_code", "warehouse", "location_id"),
        "supplier": ("supplier_id", "supplier_name"),
        "sku": ("sku",),
    }.get(grain, (grain,))
    if sql:
        hay = _group_key_blob(sql) if group_sql.strip() else _sql_select_blob(sql)
        if any(_word(hay, a) for a in aliases):
            return True
    if isinstance(plan, dict):
        return any(_word(plan_group, a) for a in aliases)
    return False


def _honors_named_filter(value: str, *, sql: str | None, plan: dict[str, Any] | None) -> bool:
    """The value is a predicate (WHERE/HAVING or plan filters), not a label."""
    aliases = [value]
    if _norm(value) in {_norm(a) for a in _WH_A_ALIASES}:
        aliases = list(_WH_A_ALIASES)
    hay = _norm(_sql_where_blob(sql) + " " + _plan_filter_blob(plan))
    return any(_norm(a) and re.search(rf"\b{re.escape(_norm(a))}\b", hay) for a in aliases)


def _honors_time_filter(value: str, *, sql: str | None, plan: dict[str, Any] | None) -> bool:
    where = _sql_where_blob(sql)
    pfilt = _plan_filter_blob(plan)
    if value.startswith("year="):
        year = value.split("=", 1)[1]
        return _word(where, year) or _word(pfilt, year)
    if value.startswith("month="):
        name = value.split("=", 1)[1]
        num = _MONTH_NUM.get(name)
        if _word(where, name) or _word(pfilt, name):
            return True
        if num is None:
            return False
        mm = f"{num:02d}"
        return (
            re.search(rf"'\d{{4}}-{mm}(?:-\d{{2}})?'", where) is not None
            or re.search(
                rf"(?:month\s*\([^)]*\)|(?:extract|date_part)\s*\(\s*'?month\b[^)]*\))"
                rf"\s*=\s*'?0?{num}\b",
                where,
            )
            is not None
        )
    # last/this/past N <unit>: a date predicate anchored on the clock or a
    # date literal, windowed in that unit. Any WHERE is not enough.
    unit = value.rsplit("_", 1)[-1]
    if where and _CLOCK_ANCHOR_RE.search(where):
        u = re.escape(unit)
        if re.search(rf"\binterval\b\s*'?\s*\d*\s*{u}s?\b", where):
            return True
        if re.search(rf"date_trunc\s*\(\s*['\"]{u}s?['\"]", where):
            return True
        if re.search(rf"date_(?:sub|add|diff)\s*\(\s*['\"]{u}s?['\"]", where):
            return True
    return _word(pfilt, unit) or _word(pfilt, unit + "s")


def unhonored_qualifier_reason(
    question: str,
    *,
    sql: str | None = None,
    plan: dict[str, Any] | None = None,
) -> str | None:
    """First uncovered qualifier as ``unhonored_qualifier:<kind>=<value>``."""
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
        elif kind == KIND_GRAIN:
            ok = _honors_grain(value, sql=sql, plan=plan)
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
        elif kind in (KIND_GROUP_BY, KIND_GRAIN):
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
    "KIND_GRAIN",
    "KIND_GROUP_BY",
    "KIND_NAMED_FILTER",
    "KIND_TIME_FILTER",
    "KIND_TIME_GRAIN",
    "TIME_GRAINS",
    "apply_qualifiers_to_retry_plan",
    "extract_qualifiers",
    "qualifier_reason",
    "retry_plan_covers_qualifiers",
    "unhonored_qualifier_reason",
]
