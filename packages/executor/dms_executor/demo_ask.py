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


def _excluded_skus(question: str) -> list[str]:
    found = re.findall(
        r"\b(?:ignor(?:e|ing)|exclud(?:e|ing)|without|except)\s+"
        r"(?:the\s+)?([A-Za-z][\w-]*)",
        question,
        flags=re.I,
    )
    out: list[str] = []
    for token in found:
        t = token.strip().upper()
        if t in {"THE", "A", "AN", "SKU", "AND", "OR", "FROM", "BY"}:
            continue
        if t not in out:
            out.append(t)
    return out


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
    factor = _scale_factor(q)

    if factor is not None and factor != 0 and (
        "revenue" in q or "sales" in q or "total" in q or "divid" in q
    ):
        return _scale_revenue(factor, space_id=space_id)

    window = _rank_window(question)
    wants_rank = bool(
        (re.search(r"\b(top|best|highest|most)\b", q) and re.search(r"\b(sell|sold|revenue|sales)\b", q))
        or (window and re.search(r"\b(sku|skus|revenue|sales|sell)\b", q))
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
            exclude=_excluded_skus(question),
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
    return {
        "answer_id": "ans_demo_revenue",
        "text": f"Total outbound revenue was {_money(total)}.",
        "values": [
            {"id": "v_rev", "value": round(total, 2), "unit": "MYR", "label": "Total revenue"}
        ],
        "badge": "L2_VALIDATED",
        "sql_used": sql,
        "assumptions": [
            "outbound transactions only",
            "revenue = quantity_kg × unit_cost_myr",
            "demo warehouse — not a live Cortex answer",
        ],
        "as_of": _as_of(),
        "space_id": space_id,
        "ask_mode": "demo",
        "contributing_sources": _SOURCES,
        "rows": [{"metric": "total_revenue", "revenue_myr": round(total, 2)}],
        "chart": {
            "kind": "bar",
            "x": "metric",
            "y": "revenue_myr",
            "title": "Total outbound revenue",
        },
        "suggestions": SUGGESTIONS,
    }


def _scale_revenue(factor: float, *, space_id: str | None) -> dict[str, Any]:
    total = total_outbound_revenue()
    scaled = total / factor
    sql = (
        "SELECT COALESCE(SUM(quantity_kg * unit_cost_myr), 0)::DOUBLE AS revenue_myr "
        "FROM transactions WHERE txn_type = 'outbound'"
    )
    return {
        "answer_id": "ans_demo_scale",
        "text": (
            f"Total outbound revenue was {_money(total)}. "
            f"Divided by {factor:g} that is {_money(scaled)}."
        ),
        "values": [
            {"id": "v_rev", "value": round(total, 2), "unit": "MYR", "label": "Total revenue"},
            {
                "id": "v_scaled",
                "value": round(scaled, 2),
                "unit": "MYR",
                "label": f"Revenue ÷ {factor:g}",
            },
        ],
        "badge": "L2_VALIDATED",
        "sql_used": sql,
        "assumptions": [
            "outbound transactions only",
            f"scale factor = {factor:g} applied in DMS demo router (L2)",
            "demo warehouse — not a certified Cortex metric",
        ],
        "as_of": _as_of(),
        "space_id": space_id,
        "ask_mode": "demo",
        "contributing_sources": _SOURCES,
        "rows": [
            {"label": "Total", "revenue_myr": round(total, 2)},
            {"label": f"÷ {factor:g}", "revenue_myr": round(scaled, 2)},
        ],
        "chart": {
            "kind": "bar",
            "x": "label",
            "y": "revenue_myr",
            "title": f"Revenue vs ÷ {factor:g}",
        },
        "suggestions": SUGGESTIONS,
    }


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
    return {
        "answer_id": "ans_demo_top_sku",
        "text": text,
        "values": [
            {
                "id": "v_top",
                "value": round(float(top["revenue_myr"]), 2),
                "unit": "MYR",
                "label": f"{top['sku']} revenue",
            }
        ],
        "badge": "L2_VALIDATED",
        "sql_used": " ".join(sql.split()),
        "assumptions": assumptions,
        "as_of": _as_of(),
        "space_id": space_id,
        "ask_mode": "demo",
        "contributing_sources": _SOURCES,
        "rows": [
            {"sku": r["sku"], "revenue_myr": round(float(r["revenue_myr"]), 2)} for r in rows
        ],
        "chart": {
            "kind": "hbar",
            "x": "sku",
            "y": "revenue_myr",
            "title": f"SKU revenue ranks {rank_start}–{rank_start + len(rows) - 1}",
        },
        "suggestions": SUGGESTIONS,
    }


def _capacity(*, space_id: str | None) -> dict[str, Any]:
    sql = """
        SELECT name AS location,
               ROUND(100.0 * current_load_kg / NULLIF(capacity_kg, 0), 1)::DOUBLE AS util_pct
        FROM locations
        ORDER BY util_pct DESC
    """
    rows = execute_sql(sql)
    peak = rows[0] if rows else {"location": "?", "util_pct": 0.0}
    return {
        "answer_id": "ans_demo_capacity",
        "text": (
            f"Warehouse capacity utilisation peaks at {peak['location']} "
            f"({float(peak['util_pct']):.1f}%)."
        ),
        "values": [
            {
                "id": "v_util",
                "value": float(peak["util_pct"]),
                "unit": "%",
                "label": f"{peak['location']} utilisation",
            }
        ],
        "badge": "L2_VALIDATED",
        "sql_used": " ".join(sql.split()),
        "assumptions": ["demo warehouse locations table"],
        "as_of": _as_of(),
        "space_id": space_id,
        "ask_mode": "demo",
        "contributing_sources": [
            {
                "ref_id": "ref_loc",
                "container": "locations",
                "kind": "sql",
                "row_count": len(rows),
                "contribution": 1.0,
                "origin_uri": "duckdb://dms_demo/locations",
            }
        ],
        "rows": [
            {"location": r["location"], "util_pct": float(r["util_pct"])} for r in rows
        ],
        "chart": {
            "kind": "hbar",
            "x": "location",
            "y": "util_pct",
            "title": "Capacity utilisation %",
        },
        "suggestions": SUGGESTIONS,
    }


def _below_reorder(*, space_id: str | None) -> dict[str, Any]:
    sql = """
        SELECT sku, location_id, quantity_kg, reorder_level_kg
        FROM inventory
        WHERE quantity_kg < reorder_level_kg
        ORDER BY sku
    """
    rows = execute_sql(sql)
    if not rows:
        return {
            "answer_id": "ans_demo_reorder_ok",
            "text": "No SKUs are below reorder level in the demo warehouse.",
            "values": [],
            "badge": "L2_VALIDATED",
            "sql_used": " ".join(sql.split()),
            "assumptions": ["demo inventory"],
            "as_of": _as_of(),
            "space_id": space_id,
            "ask_mode": "demo",
            "contributing_sources": _SOURCES,
            "rows": [],
            "suggestions": SUGGESTIONS,
        }
    names = ", ".join(r["sku"] for r in rows)
    return {
        "answer_id": "ans_demo_reorder",
        "text": f"SKUs below reorder level: {names}.",
        "values": [],
        "badge": "L2_VALIDATED",
        "sql_used": " ".join(sql.split()),
        "assumptions": ["demo inventory"],
        "as_of": _as_of(),
        "space_id": space_id,
        "ask_mode": "demo",
        "contributing_sources": _SOURCES,
        "rows": [
            {
                "sku": r["sku"],
                "location_id": r["location_id"],
                "quantity_kg": float(r["quantity_kg"]),
                "reorder_level_kg": float(r["reorder_level_kg"]),
            }
            for r in rows
        ],
        "suggestions": SUGGESTIONS,
    }


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
        return {
            "answer_id": "ans_demo_alerts_none",
            "text": "No active alerts in the demo warehouse network.",
            "values": [],
            "badge": "L2_VALIDATED",
            "sql_used": " ".join(sql.split()),
            "assumptions": ["demo alerts"],
            "as_of": _as_of(),
            "space_id": space_id,
            "ask_mode": "demo",
            "contributing_sources": [],
            "rows": [],
            "suggestions": SUGGESTIONS,
        }
    high = sum(1 for r in rows if r["severity"] == "high")
    return {
        "answer_id": "ans_demo_alerts",
        "text": (
            f"{len(rows)} active alerts across the network "
            f"({high} high severity)."
        ),
        "values": [
            {
                "id": "v_alerts",
                "value": float(len(rows)),
                "label": "Active alerts",
            }
        ],
        "badge": "L2_VALIDATED",
        "sql_used": " ".join(sql.split()),
        "assumptions": ["unresolved alerts only", "demo warehouse"],
        "as_of": _as_of(),
        "space_id": space_id,
        "ask_mode": "demo",
        "contributing_sources": [
            {
                "ref_id": "ref_alerts",
                "container": "alerts",
                "kind": "sql",
                "row_count": len(rows),
                "contribution": 1.0,
                "origin_uri": "duckdb://dms_demo/alerts",
            }
        ],
        "rows": [
            {
                "alert_id": r["alert_id"],
                "severity": r["severity"],
                "location_id": r["location_id"],
                "message": r["message"],
            }
            for r in rows
        ],
        "suggestions": SUGGESTIONS,
    }


def _abstain(*, space_id: str | None) -> dict[str, Any]:
    return {
        "answer_id": "ans_demo_abstain",
        "text": (
            "I cannot answer that from the demo warehouse without inventing a number. "
            "Try one of the suggested questions."
        ),
        "values": [],
        "badge": "ABSTAIN",
        "assumptions": ["demo router abstained — 0 confidently wrong"],
        "as_of": _as_of(),
        "space_id": space_id,
        "ask_mode": "demo",
        "contributing_sources": [],
        "rows": [],
        "suggestions": SUGGESTIONS,
    }


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
