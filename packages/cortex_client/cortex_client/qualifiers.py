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
    "extract_qualifiers",
    "qualifier_reason",
    "retry_plan_covers_qualifiers",
    "unhonored_qualifier_reason",
]
