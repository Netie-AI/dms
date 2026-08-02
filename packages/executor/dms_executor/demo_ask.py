"""L2 demo ask router — computes from demo warehouse. Never claims certified.

Intents: total revenue, scale/divide, top SKUs, capacity. Unknown → ABSTAIN.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from dms_executor.demo_warehouse import (
    DEMO_TABLES,
    execute_sql,
    total_outbound_revenue,
)
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

SUGGESTIONS = [
    "What was total revenue?",
    "Divide the revenue by 5",
    "Top 5 selling SKUs by revenue",
    "Show warehouse capacity utilisation",
    "Which SKUs are below reorder level?",
    "List active alerts across the warehouse network",
]

_SOURCES = [
    {
        "ref_id": "ref_txn",
        "container": "transactions",
        "kind": "sql",
        "row_count": 10,
        "contribution": 1.0,
        "origin_uri": "duckdb://dms_demo/transactions",
    },
    {
        "ref_id": "ref_loc",
        "container": "locations",
        "kind": "sql",
        "row_count": 3,
        "contribution": 0.0,
        "origin_uri": "duckdb://dms_demo/locations",
    },
]


def _as_of() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pack(**kwargs: Any) -> dict[str, Any]:
    """Demo envelopes — only via build_answer_envelope (Phase 0 single constructor)."""
    kwargs.setdefault("ask_mode", "demo")
    kwargs.setdefault("as_of", _as_of())
    kwargs.setdefault("suggestions", SUGGESTIONS)
    env = build_answer_envelope(**kwargs)
    assert_envelope_valid(env)
    return env


def _money(n: float) -> str:
    return f"RM {n:,.2f}"


def _scale_factor(question: str) -> float | None:
    q = question.lower().strip()
    m = re.search(r"\bdivid(?:e|ed|ing)\b.*?\bby\s+(\d+(?:\.\d+)?)", q)
    if m:
        return float(m.group(1))
    m = re.search(r"(?:/|÷)\s*(\d+(?:\.\d+)?)", q)
    if m:
        return float(m.group(1))
    m = re.search(r"\bone\s+fifth\b", q)
    if m:
        return 5.0
    m = re.search(r"\b(?:scale|split)\b.*?\b(?:by|/)\s*(\d+(?:\.\d+)?)", q)
    if m:
        return float(m.group(1))
    return None


def _top_n(question: str, default: int = 5) -> int:
    m = re.search(r"\btop\s+(\d+)\b", question.lower())
    if m:
        return max(1, min(20, int(m.group(1))))
    return default


_EXCLUSION_STOP = re.compile(
    r"\b(?:what|show|list|give|find|get|top|bottom|best|worst|highest|lowest|"
    r"ranked?|numbers?|ranks?|selling|sold)\b",
    re.I,
)
_EXCLUSION_SKIP = frozenset(
    {"THE", "A", "AN", "SKU", "AND", "OR", "FROM", "BY", "OF", "ALL", "ANY"}
)


def _excluded_skus(question: str) -> list[str]:
    """Parse all named SKUs in an exclusion clause (comma / and / or lists)."""
    out: list[str] = []
    for m in re.finditer(
        r"\b(?:ignor(?:e|ing)|exclud(?:e|ing)|remov(?:e|ing)|drop(?:ping)?|without|except)\s+(?:the\s+)?(.+)",
        question,
        flags=re.I,
    ):
        clause = m.group(1)
        stop = _EXCLUSION_STOP.search(clause)
        if stop:
            clause = clause[: stop.start()]
        for token in re.split(r"\s*(?:,|/|\band\b|\bor\b)\s*", clause, flags=re.I):
            t = token.strip().strip("'\"")
            tm = re.match(r"^([A-Za-z0-9][\w-]*)$", t)
            if not tm:
                continue
            t = tm.group(1).upper()
            if t in _EXCLUSION_SKIP or len(t) < 2:
                continue
            if t not in out:
                out.append(t)
    return out


def _resolve_exclude_skus(tokens: list[str]) -> list[str]:
    """Map bare tokens like BETA → SKU-BETA against live distinct SKUs."""
    if not tokens:
        return []
    rows = execute_sql("SELECT DISTINCT sku FROM transactions WHERE sku IS NOT NULL")
    known = [str(r["sku"]) for r in rows if r.get("sku") is not None]
    by_lower = {k.lower(): k for k in known}

    def norm(s: str) -> str:
        return re.sub(r"[\s_\-]+", "", s.lower())

    by_norm = {norm(k): k for k in known}
    resolved: list[str] = []
    for token in tokens:
        lower = token.lower()
        if lower in by_lower:
            val = by_lower[lower]
        elif norm(token) in by_norm:
            val = by_norm[norm(token)]
        else:
            contained = [k for k in known if lower in k.lower() or k.lower() in lower]
            if len(contained) != 1:
                # Honest: do not claim an exclusion that cannot resolve.
                continue
            val = contained[0]
        up = val.upper()
        if up not in resolved:
            resolved.append(up)
    return resolved


def _rank_window(question: str) -> tuple[int, int] | None:
    q = question.lower()
    m = (
        re.search(
            r"\b(?:number|numbers|nos?|ranks?|positions?)\s*"
            r"(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b",
            q,
        )
        or re.search(
            r"\b(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b"
            r".*\b(?:sku|skus|rank|revenue|sales)\b",
            q,
        )
        or re.search(
            r"\b(?:sku|skus|rank|revenue|sales)\b.*\b"
            r"(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b",
            q,
        )
    )
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    if start > end:
        start, end = end, start
    if start < 1 or end > 1000:
        return None
    return start, end


def answer_demo_question(question: str, *, space_id: str | None = None) -> dict[str, Any]:
    """Return UI answer envelope from DuckDB. Badge is always L2 or ABSTAIN."""
    q = question.lower().strip()

    # Mirror Cortex live path: predictive asks must never invent a historical total.
    if re.search(r"\b(forecast|predict|projection|what if|hypothetical|next quarter)\b", q):
        return _abstain(space_id=space_id)

    factor = _scale_factor(q)

    if factor is not None and factor != 0 and (
        "revenue" in q or "sales" in q or "total" in q or "divid" in q
    ):
        return _scale_revenue(factor, space_id=space_id)

    window = _rank_window(question)
    # Bare "top 10" / "what about top 10" counts as sales rank (demo has no anaphora).
    # Accept common typos: revnue, revanue.
    sales_kw = re.search(r"\b(sell|sold|revenue|sales|sku|skus|revnue|revanue)\b", q)
    wants_rank = bool(
        (re.search(r"\b(top|best|highest|most)\b", q) and sales_kw)
        or re.search(r"\btop\s+\d+\b", q)
        or (window and sales_kw)
    )
    if wants_rank:
        n = _top_n(q)
        offset = 0
        if window:
            start, end = window
            offset = start - 1
            n = end - start + 1
        return _top_skus(
            n,
            offset=offset,
            exclude=_resolve_exclude_skus(_excluded_skus(question)),
            space_id=space_id,
        )

    if "capacity" in q or "utilisation" in q or "utilization" in q:
        return _capacity(space_id=space_id)

    if re.search(r"\b(revenue|sales)\b", q) or re.search(
        r"\btotal\b.*\b(revenue|sales)\b", q
    ):
        return _total_revenue(space_id=space_id)

    if "sku" in q and ("below" in q or "reorder" in q):
        return _below_reorder(space_id=space_id)

    if "alert" in q:
        return _active_alerts(space_id=space_id)

    return _abstain(space_id=space_id)


def _total_revenue(*, space_id: str | None) -> dict[str, Any]:
    sql = (
        "SELECT COALESCE(SUM(quantity_kg * unit_cost_myr), 0)::DOUBLE AS revenue_myr "
        "FROM transactions WHERE txn_type = 'outbound'"
    )
    total = total_outbound_revenue()
    return _pack(
        answer_id="ans_demo_revenue",
        text=f"Total outbound revenue was {_money(total)}.",
        values=[
            {"id": "v_rev", "value": round(total, 2), "unit": "MYR", "label": "Total revenue"}
        ],
        badge="L2_VALIDATED",
        sql_used=sql,
        assumptions=[
            "outbound transactions only",
            "revenue = quantity_kg × unit_cost_myr",
            "demo warehouse — not a live Cortex answer",
        ],
        space_id=space_id,
        contributing_sources=_SOURCES,
        rows=[{"metric": "total_revenue", "revenue_myr": round(total, 2)}],
        chart={
            "kind": "bar",
            "x": "metric",
            "y": "revenue_myr",
            "title": "Total outbound revenue",
        },
    )


def _scale_revenue(factor: float, *, space_id: str | None) -> dict[str, Any]:
    total = total_outbound_revenue()
    scaled = total / factor
    sql = (
        "SELECT COALESCE(SUM(quantity_kg * unit_cost_myr), 0)::DOUBLE AS revenue_myr "
        "FROM transactions WHERE txn_type = 'outbound'"
    )
    return _pack(
        answer_id="ans_demo_scale",
        text=(
            f"Total outbound revenue was {_money(total)}. "
            f"Divided by {factor:g} that is {_money(scaled)}."
        ),
        values=[
            {"id": "v_rev", "value": round(total, 2), "unit": "MYR", "label": "Total revenue"},
            {
                "id": "v_scaled",
                "value": round(scaled, 2),
                "unit": "MYR",
                "label": f"Revenue ÷ {factor:g}",
            },
        ],
        badge="L2_VALIDATED",
        sql_used=sql,
        assumptions=[
            "outbound transactions only",
            f"scale factor = {factor:g} applied in DMS demo router (L2)",
            "demo warehouse — not a certified Cortex metric",
        ],
        space_id=space_id,
        contributing_sources=_SOURCES,
        rows=[
            {"label": "Total", "revenue_myr": round(total, 2)},
            {"label": f"÷ {factor:g}", "revenue_myr": round(scaled, 2)},
        ],
        chart={
            "kind": "bar",
            "x": "label",
            "y": "revenue_myr",
            "title": f"Revenue vs ÷ {factor:g}",
        },
    )


def _top_skus(
    n: int,
    *,
    offset: int = 0,
    exclude: list[str] | None = None,
    space_id: str | None,
) -> dict[str, Any]:
    exclude = [s.upper() for s in (exclude or []) if s]
    where_extra = ""
    if exclude:
        quoted = ", ".join("'" + s.replace("'", "''") + "'" for s in exclude)
        where_extra = f" AND UPPER(sku) NOT IN ({quoted})"
    sql = f"""
        SELECT sku,
               SUM(quantity_kg * unit_cost_myr)::DOUBLE AS revenue_myr
        FROM transactions
        WHERE txn_type = 'outbound'{where_extra}
        GROUP BY sku
        ORDER BY revenue_myr DESC, sku ASC
        LIMIT {int(n)} OFFSET {int(max(0, offset))}
    """
    rows = execute_sql(sql)
    if not rows:
        return _abstain(space_id=space_id)
    top = rows[0]
    rank_start = int(max(0, offset)) + 1
    text = (
        f"SKUs by outbound revenue ranks {rank_start}–{rank_start + len(rows) - 1} — "
        f"#{rank_start} {top['sku']} at {_money(float(top['revenue_myr']))}."
    )
    assumptions = ["outbound only", "demo warehouse"]
    if exclude:
        assumptions.append("excluded: " + ", ".join(exclude))
    if offset:
        assumptions.append(f"rank window offset={offset}")
    return _pack(
        answer_id="ans_demo_top_sku",
        text=text,
        values=[
            {
                "id": "v_top",
                "value": round(float(top["revenue_myr"]), 2),
                "unit": "MYR",
                "label": f"{top['sku']} revenue",
            }
        ],
        badge="L2_VALIDATED",
        sql_used=" ".join(sql.split()),
        assumptions=assumptions,
        space_id=space_id,
        contributing_sources=_SOURCES,
        rows=[
            {"sku": r["sku"], "revenue_myr": round(float(r["revenue_myr"]), 2)} for r in rows
        ],
        chart={
            "kind": "hbar",
            "x": "sku",
            "y": "revenue_myr",
            "title": f"SKU revenue ranks {rank_start}–{rank_start + len(rows) - 1}",
        },
    )


def _capacity(*, space_id: str | None) -> dict[str, Any]:
    sql = """
        SELECT name AS location,
               ROUND(100.0 * current_load_kg / NULLIF(capacity_kg, 0), 1)::DOUBLE AS util_pct
        FROM locations
        ORDER BY util_pct DESC
    """
    rows = execute_sql(sql)
    peak = rows[0] if rows else {"location": "?", "util_pct": 0.0}
    raw_util = peak.get("util_pct", 0.0)
    util_pct = float(raw_util) if isinstance(raw_util, (int, float, str)) else 0.0
    return _pack(
        answer_id="ans_demo_capacity",
        text=(
            f"Warehouse capacity utilisation peaks at {peak['location']} "
            f"({util_pct:.1f}%)."
        ),
        values=[
            {
                "id": "v_util",
                "value": util_pct,
                "unit": "%",
                "label": f"{peak['location']} utilisation",
            }
        ],
        badge="L2_VALIDATED",
        sql_used=" ".join(sql.split()),
        assumptions=["demo warehouse locations table"],
        space_id=space_id,
        contributing_sources=[
            {
                "ref_id": "ref_loc",
                "container": "locations",
                "kind": "sql",
                "row_count": len(rows),
                "contribution": 1.0,
                "origin_uri": "duckdb://dms_demo/locations",
            }
        ],
        rows=[
            {"location": r["location"], "util_pct": float(r["util_pct"])} for r in rows
        ],
        chart={
            "kind": "hbar",
            "x": "location",
            "y": "util_pct",
            "title": "Capacity utilisation %",
        },
    )


def _below_reorder(*, space_id: str | None) -> dict[str, Any]:
    sql = """
        SELECT sku, location_id, quantity_kg, reorder_level_kg
        FROM inventory
        WHERE quantity_kg < reorder_level_kg
        ORDER BY sku
    """
    rows = execute_sql(sql)
    if not rows:
        return _pack(
            answer_id="ans_demo_reorder_ok",
            text="No SKUs are below reorder level in the demo warehouse.",
            values=[],
            badge="L2_VALIDATED",
            sql_used=" ".join(sql.split()),
            assumptions=["demo inventory"],
            space_id=space_id,
            contributing_sources=_SOURCES,
            rows=[],
        )
    names = ", ".join(r["sku"] for r in rows)
    return _pack(
        answer_id="ans_demo_reorder",
        text=f"SKUs below reorder level: {names}.",
        values=[],
        badge="L2_VALIDATED",
        sql_used=" ".join(sql.split()),
        assumptions=["demo inventory"],
        space_id=space_id,
        contributing_sources=_SOURCES,
        rows=[
            {
                "sku": r["sku"],
                "location_id": r["location_id"],
                "quantity_kg": float(r["quantity_kg"]),
                "reorder_level_kg": float(r["reorder_level_kg"]),
            }
            for r in rows
        ],
    )


def _active_alerts(*, space_id: str | None) -> dict[str, Any]:
    sql = """
        SELECT alert_id, severity, location_id, message
        FROM alerts
        WHERE resolved = FALSE
        ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 alert_id
    """
    rows = execute_sql(sql)
    if not rows:
        return _pack(
            answer_id="ans_demo_alerts_none",
            text="No active alerts in the demo warehouse network.",
            values=[],
            badge="L2_VALIDATED",
            sql_used=" ".join(sql.split()),
            assumptions=["demo alerts"],
            space_id=space_id,
            contributing_sources=[],
            rows=[],
        )
    high = sum(1 for r in rows if r["severity"] == "high")
    return _pack(
        answer_id="ans_demo_alerts",
        text=(
            f"{len(rows)} active alerts across the network "
            f"({high} high severity)."
        ),
        values=[
            {
                "id": "v_alerts",
                "value": float(len(rows)),
                "label": "Active alerts",
            }
        ],
        badge="L2_VALIDATED",
        sql_used=" ".join(sql.split()),
        assumptions=["unresolved alerts only", "demo warehouse"],
        space_id=space_id,
        contributing_sources=[
            {
                "ref_id": "ref_alerts",
                "container": "alerts",
                "kind": "sql",
                "row_count": len(rows),
                "contribution": 1.0,
                "origin_uri": "duckdb://dms_demo/alerts",
            }
        ],
        rows=[
            {
                "alert_id": r["alert_id"],
                "severity": r["severity"],
                "location_id": r["location_id"],
                "message": r["message"],
            }
            for r in rows
        ],
    )


def _abstain(*, space_id: str | None) -> dict[str, Any]:
    return _pack(
        answer_id="ans_demo_abstain",
        text=(
            "I cannot answer that from the demo warehouse without inventing a number. "
            "Try one of the suggested questions."
        ),
        values=[],
        badge="ABSTAIN",
        abstained=True,
        assumptions=["demo router abstained — 0 confidently wrong"],
        space_id=space_id,
        contributing_sources=[],
        rows=[],
    )


def demo_session_acl(*, session_id: str, space_id: str | None = None) -> dict[str, Any]:
    """Facts for live bind: allowlist all demo tables."""
    return {
        "session_id": session_id,
        "org_id": "tenant_demo",
        "space_id": space_id,
        "row_predicates": {t: "TRUE" for t in DEMO_TABLES},
        "allowed_paths": [],
        "pool_id": "default",
        "ttl_seconds": 900,
    }
