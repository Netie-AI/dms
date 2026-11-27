"""Legacy question-text routes.

``DMS_NO_WORD_RULES`` off calls these. On, measure linking does not.
A follow-up deletes this module after the armed A/B. Do not add a phrase.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from cortex_client.qualifiers import parse_rank_window, rank_window_group, rank_window_measure

from dms_executor.demo_warehouse import connect_serving
from dms_executor.ontology import Ontology

_TIME = re.compile(
    r"\b(last|this|next|month|week|year|quarter|yesterday|today|ago|ytd|qtd|"
    r"daily|weekly|monthly|quarterly|yearly|annually)\b",
    re.I,
)
_NEEDS_DIM = re.compile(r"\b(by|per|each|grouped|across)\b", re.I)
_ENTITY_PREFIX = re.compile(r"^\s*(which|list|rank)\b", re.I)
_TOP_N = re.compile(r"\btop\s+(\d{1,2})\b", re.I)
_WH_A = re.compile(r"\b(warehouse a|wh-a)\b", re.I)
_COLD = re.compile(r"cold[\s-]?storage", re.I)
_COLD_NEGATED = re.compile(
    r"(?:\b(?:not|excluding|other\s+than)\b.{0,48}cold[\s-]?storage)"
    r"|(?:non-cold)",
    re.I,
)
_STILL_UNTYPED = re.compile(
    r"\b(delayed|alerts?|storage bin|high-risk|pending shipment|risk and lead)\b",
    re.I,
)
_ABOVE_PCT = re.compile(r"above\s+(\d{1,2})\s+percent", re.I)
_UNSURE_ASK = re.compile(
    r"\b(worry about|is this good|just give me)\b",
    re.I,
)
_DIM_HINTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("by country", "supplier country"), "supplier", "country"),
    (
        ("by destination", "per destination", "by location"),
        "location",
        "location_code",
    ),
    (("by plant", "by warehouse"), "location", "location_code"),
    (
        (
            "by category",
            "per category",
            "in each category",
            "categoty",
            "categories by",
            "category sales",
        ),
        "product",
        "category",
    ),
    (("by sku", "selling sku", "skus by"), "product", "sku"),
    (("by supplier",), "supplier", "supplier_id"),
    (("by lane", "per lane"), "lane", "origin_plant_id"),
    (("by day", "per day", "each day", "daily"), "day", "day"),
    (("by week", "per week", "each week", "weekly"), "day", "week"),
    (("by month", "per month", "each month", "monthly"), "day", "month"),
    (
        ("by quarter", "per quarter", "each quarter", "quarterly"),
        "day",
        "quarter",
    ),
    (("by year", "per year", "each year", "yearly"), "day", "year"),
)


def vague_question(question: str) -> bool:
    """Existing vague-ask gate. Not measure linking."""
    return bool(_UNSURE_ASK.search(question or ""))


def asks_for_dimension(question: str) -> bool:
    q = question or ""
    return bool(_NEEDS_DIM.search(q) or _TOP_N.search(q) or _ENTITY_PREFIX.search(q))


def _rules_off() -> bool:
    """Digit 1 only. Implies ``DMS_NO_WORD_RULES`` for this locker."""
    return os.environ.get("DMS_RULES_OFF", "0").strip() == "1"


def locked_measure(question: str) -> str | None:
    """One measure the question names, or None. Not a certified-pack lookup.

    Kept so ``DMS_NO_WORD_RULES`` off stays byte-identical. Do not add a phrase.
    ``DMS_RULES_OFF`` does not let this list decide a measure.
    """
    if _rules_off():
        return None
    qn = (question or "").lower()
    if "quantity sold" in qn or "qty sold" in qn:
        return "outbound_kg"
    if "sku count" in qn or "how many sku" in qn or "how many unique sku" in qn:
        return "sku_count"
    if "shipment cost" in qn or "shipping cost" in qn or "freight" in qn:
        return "shipping_cost_myr"
    if "capacity utilisation" in qn or "capacity utilization" in qn:
        return "utilisation_pct"
    if _COLD.search(question or ""):
        return "utilisation_pct"
    if _ABOVE_PCT.search(question or "") or "90 percent" in qn:
        return "utilisation_pct"
    if "cctv" in qn or "camera" in qn:
        return "utilisation_pct"
    if "reorder" in qn:
        return "below_reorder_lots"
    if re.search(r"\brank(?:ing)?\b", qn) and "supplier" in qn:
        return "supplier_rank_score"
    if "audit" in qn and "overdue" in qn:
        return "audit_overdue"
    if "expir" in qn or "chemical" in qn:
        return "stock_value_myr"
    if (
        "stock value" in qn
        or "inventory worth" in qn
        or "value of stock" in qn
        or "worth per category" in qn
    ):
        return "stock_value_myr"
    if "total spend" in qn or "spend by" in qn:
        return "stock_value_myr"
    if "revenue" in qn or "selling sku" in qn:
        return "outbound_value_myr"
    if "categoty" in qn or (
        "sales" in qn and ("category" in qn or "categories" in qn)
    ):
        return "outbound_value_myr"
    return None


def _group_from_hints(question: str) -> list[list[str]] | None:
    qn = (question or "").lower()
    hits = [
        [obj, col]
        for needles, obj, col in _DIM_HINTS
        if any(n in qn for n in needles)
    ]
    if not hits:
        return None
    unique: list[list[str]] = []
    for pair in hits:
        if pair not in unique:
            unique.append(pair)
    if len(unique) > 1:
        return None
    return unique


def intent_slots(
    question: str,
    ontology: Ontology | None = None,
    *,
    keyword_lock: bool = True,
) -> dict[str, Any]:
    """Locked measure / group / limit / keep_gt for Cortex generate. No SQL."""
    q = question or ""
    lock = locked_measure(q) if keyword_lock else None
    if lock and ontology is not None and lock not in ontology.measures:
        lock = None
    hinted = _group_from_hints(q)
    top = _TOP_N.search(q)
    above = _ABOVE_PCT.search(q)
    out: dict[str, Any] = {}
    if lock:
        out["measure"] = lock
    if hinted:
        out["group_by"] = hinted
    if top:
        out["limit"] = int(top.group(1))
    if above:
        out["keep_gt"] = float(above.group(1))
    win = parse_rank_window(q)
    if win is not None:
        # "excluding top 3": 3 is the skipped prefix, never the limit.
        out.pop("limit", None)
        out["offset"] = win.offset
        if win.limit is not None:
            out["limit"] = win.limit
        pair = rank_window_group(win)
        if pair and not hinted:
            out["group_by"] = [list(pair)]
        if not lock and ontology is not None:
            specs = {n: m.description or "" for n, m in ontology.measures.items()}
            measure = rank_window_measure(q, specs)[0]
            if measure:
                out["measure"] = measure
    return out


def _one_value(warehouse: Path, sql: str) -> str | None:
    con = connect_serving(warehouse)
    try:
        rows = con.execute(sql).fetchall()
    except Exception:  # noqa: BLE001 -- empty retrieve, do not 503
        return None
    finally:
        con.close()
    vals = [str(r[0]) for r in rows if r and r[0] is not None]
    uniq = list(dict.fromkeys(vals))
    return uniq[0] if len(uniq) == 1 else None


def lookup_bound_values(question: str, warehouse: Path | None) -> dict[str, str]:
    """Lake encodings for typed filters. Not a certified-pack lookup."""
    if warehouse is None or not Path(warehouse).is_file():
        return {}
    qn = (question or "").lower()
    path = Path(warehouse)
    out: dict[str, str] = {}
    if _WH_A.search(question or ""):
        code = _one_value(
            path,
            "SELECT location_code FROM locations WHERE "
            "lower(CAST(location_code AS VARCHAR)) = 'wh-a' "
            "OR lower(CAST(name AS VARCHAR)) = 'warehouse a' LIMIT 3",
        )
        if code:
            out["location.location_code"] = code
    if "chemical" in qn:
        cat = _one_value(
            path,
            "SELECT DISTINCT CAST(category AS VARCHAR) FROM inventory "
            "WHERE lower(CAST(category AS VARCHAR)) LIKE '%chemical%' LIMIT 3",
        )
        if cat:
            out["lot.category"] = cat
            out["product.category"] = cat
    return out


def _has_col(context: dict[str, Any], obj: str, col: str) -> bool:
    cols = context.get("columns") or {}
    got = cols.get(obj) or []
    return col in got


def typed_filters(question: str, context: dict[str, Any]) -> list[list[Any]] | None:
    """None = needs a filter we cannot type. [] = no extra filters."""
    qn = (question or "").lower()
    bound = context.get("bound_values") or {}
    filters: list[list[Any]] = []
    if _COLD.search(question or ""):
        if not _has_col(context, "location", "is_cold_storage"):
            return None
        want_cold = not bool(_COLD_NEGATED.search(question or ""))
        filters.append(["location", "is_cold_storage", "=", want_cold])
    if re.search(r"\bexpir", qn):
        if not _has_col(context, "lot", "expiry_date"):
            return None
        from datetime import date

        filters.append(["lot", "expiry_date", "<", date.today().isoformat()])
    if _WH_A.search(question or ""):
        code = bound.get("location.location_code")
        if not code or not _has_col(context, "location", "location_code"):
            return None
        filters.append(["location", "location_code", "=", code])
    if "chemical" in qn:
        cat = bound.get("lot.category") or bound.get("product.category")
        obj = "lot" if _has_col(context, "lot", "category") else "product"
        if not cat or not _has_col(context, obj, "category"):
            return None
        filters.append([obj, "category", "=", cat])
    if "audit" in qn and "overdue" in qn:
        measures = context.get("measures") or {}
        if _has_col(context, "supplier", "last_audit_date"):
            from datetime import date, timedelta

            cutoff = (date.today() - timedelta(days=90)).isoformat()
            filters.append(["supplier", "last_audit_date", "<", cutoff])
        elif "audit_overdue" not in measures:
            # Measure FILTER already encodes the 90-day cutoff. Missing retrieve
            # column must not None-out bind when the verified measure exists.
            return None
    if ("cctv" in qn or "camera" in qn) and not _has_col(
        context, "location", "cctv_camera_id"
    ):
        return None
    if "reorder" in qn and "below_reorder_lots" not in (context.get("measures") or {}):
        return None
    return filters


def bind_plan(
    question: str,
    context: dict[str, Any] | None,
    *,
    keyword_lock: bool = True,
    lock_override: str | None = None,
) -> dict[str, Any] | None:
    """Typed plan from retrieved context. Not a certified-pack lookup.

    Time language and leftover untyped traps miss. Typed lake filters bind.
    Tied top measures are unsure. A by/per/each or top-N ask with no bound
    dimension misses rather than blocking Cortex.
    """
    from dms_executor.semantic_retrieve import _score, question_tokens

    if not isinstance(context, dict):
        return None
    q = question or ""
    if _TIME.search(q) or _STILL_UNTYPED.search(q):
        return None
    filters = typed_filters(q, context)
    if filters is None:
        return None
    measures = context.get("measures") or {}
    if not isinstance(measures, dict) or not measures:
        return None
    if keyword_lock:
        lock = locked_measure(q)
    else:
        lock = str(lock_override).strip() if lock_override else None
    entity = bool(_ENTITY_PREFIX.search(q))
    if entity and not filters and not lock:
        return None
    toks = question_tokens(q)
    ranked = sorted(
        (
            (
                _score(name, toks) * 2
                + _score(str((spec or {}).get("description") or ""), toks),
                name,
            )
            for name, spec in measures.items()
            if isinstance(spec, dict)
        ),
        reverse=True,
    )
    ranked = [(sc, name) for sc, name in ranked if sc > 0]
    if lock and lock in measures:
        ranked = [(sc, name) for sc, name in ranked if name == lock] or [(1, lock)]
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return {"unsure": True, "plan_source": "bind_plan"}
    measure = ranked[0][1]
    grain = str((measures.get(measure) or {}).get("grain") or "")
    needs_dim = bool(_NEEDS_DIM.search(q))
    top = _TOP_N.search(q)
    hinted = _group_from_hints(q)
    group_by: list[list[str]] = []
    qn = q.lower()
    if "cctv" in qn or "camera" in qn:
        if _has_col(context, "location", "cctv_camera_id"):
            group_by = [["location", "cctv_camera_id"]]
    elif _COLD.search(q) or (entity and "location" in qn):
        if _has_col(context, "location", "location_code"):
            group_by = [["location", "location_code"]]
    elif "audit" in qn and "overdue" in qn:
        group_by = [["supplier", "supplier_id"]]
    elif "expir" in qn or "chemical" in qn or "reorder" in qn:
        group_by = [["product", "sku"]]
    elif hinted:
        group_by = hinted
    else:
        objects = context.get("objects") or {}
        columns = context.get("columns") or {}
        candidates: list[tuple[int, str, str]] = []
        if isinstance(objects, dict) and isinstance(columns, dict):
            for obj_name, spec in objects.items():
                keys = list((spec or {}).get("key") or []) if isinstance(spec, dict) else []
                cols = set(keys) | set(columns.get(obj_name) or [])
                for col in cols:
                    col_s = str(col)
                    col_sc = _score(col_s, toks)
                    if col_sc <= 0:
                        continue
                    obj_sc = _score(str(obj_name), toks)
                    candidates.append((col_sc + obj_sc, str(obj_name), col_s))
        if candidates:
            dim_cands = [c for c in candidates if c[1] != grain] or candidates
            dim_cands.sort(reverse=True)
            if len(dim_cands) > 1 and dim_cands[0][0] == dim_cands[1][0] and (
                dim_cands[0][1], dim_cands[0][2]
            ) != (dim_cands[1][1], dim_cands[1][2]):
                if dim_cands[0][2] != dim_cands[1][2]:
                    return {"unsure": True, "plan_source": "bind_plan"}
            _sc, obj, col = dim_cands[0]
            group_by = [[obj, col]]
    if measure == "utilisation_pct" and not group_by:
        group_by = [["location", "location_code"]]
    if (needs_dim or top) and not group_by:
        return None
    if (
        not needs_dim
        and not top
        and not entity
        and not filters
        and measure != "utilisation_pct"
    ):
        group_by = []
    limit = int(top.group(1)) if top else 50
    win = parse_rank_window(q)
    plan: dict[str, Any] = {"measure": measure, "group_by": group_by, "limit": limit}
    if win is not None:
        plan["offset"] = win.offset
        plan["limit"] = win.limit
    if filters:
        plan["filters"] = filters
    above = _ABOVE_PCT.search(q)
    if above:
        plan["keep_gt"] = float(above.group(1))
    return {"query_plan": plan, "plan_source": "bind_plan"}
