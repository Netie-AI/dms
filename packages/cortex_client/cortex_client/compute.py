"""Off-contract Cortex compute — Insights generate then POST /dms/query.

Contract 1.2.0 has ask/submit/ledger only. Generative ontology_plan is Cortex
``POST /v1/insights`` ``generate=true`` (OpenVault FreeRoute inside Cortex).
``POST /dms/query`` remains a typed-plan fallback. DMS never invents provider
keys and never puts secrets in the body.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from cortex_client.insights import INSIGHTS_PATH

COMPUTE_PATH = "/dms/query"
ONTOLOGY_MODE = "ontology_plan"
PLAN_SOURCES = frozenset({"ontology_plan", "bind_plan", "other"})
# FreeRoute pick stays in Cortex/OpenVault. Hint only; no provider ids or tokens.
FREEROUTE_PREFERENCE = "free+normal"
_SELECT_SQL = re.compile(r"(?is)^\s*(with|select)\b")


def attach_compute_plan_source(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy Cortex route telemetry onto ``plan_source``.

    Prefer an explicit ``plan_source`` or ``mode`` from the engine. A typed
    ``query_plan`` or Insights ``query_sql`` with neither is labeled
    ``ontology_plan`` because this client requested that mode.

    ponytail: Cortex-internal keyword bind that omits mode/plan_source still
    labels ontology_plan when a typed plan is present. Upgrade: Cortex emits
    plan_source on Insights generate and /dms/query.
    """
    out = dict(payload)
    existing = str(out.get("plan_source") or "").strip().lower()
    if existing in PLAN_SOURCES:
        out["plan_source"] = existing
        return out
    mode = str(out.get("mode") or "").strip().lower()
    if mode in PLAN_SOURCES:
        out["plan_source"] = mode
        return out
    plan = out.get("query_plan")
    if isinstance(plan, dict) and str(plan.get("measure") or "").strip():
        out["plan_source"] = ONTOLOGY_MODE
        return out
    if str(out.get("query_sql") or "").strip():
        out["plan_source"] = ONTOLOGY_MODE
    return out


def typed_query_plan(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Measure-bearing plan from Cortex. Nested Insights ``generative`` is ok."""
    plan = payload.get("query_plan")
    if isinstance(plan, dict) and str(plan.get("measure") or "").strip():
        return plan
    gen = payload.get("generative")
    if isinstance(gen, dict):
        nested = gen.get("query_plan")
        if isinstance(nested, dict) and str(nested.get("measure") or "").strip():
            return nested
    return None


def insights_query_sql(payload: dict[str, Any]) -> str | None:
    """SELECT/WITH from Insights generate. Ignore warehouse ``sql_used`` alone.

    ``POST /dms/query`` sql_used is often keyword Q&A, not ontology_plan. Only
    Insights ``generative.sql`` (or sql_used on that same generate envelope)
    counts as Cortex AI SQL.
    """
    raw_gen = payload.get("generative")
    gen: dict[str, Any] = raw_gen if isinstance(raw_gen, dict) else {}
    candidates: tuple[Any, ...] = (gen.get("sql"),)
    if payload.get("phase") == "generate" or gen:
        candidates = (gen.get("sql"), payload.get("sql_used"), payload.get("sql"))
    for cand in candidates:
        sql = str(cand or "").strip()
        if sql and _SELECT_SQL.match(sql):
            return sql
    return None


def normalize_insights_compute(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Insights envelope → compute payload. Numbers stripped; SQL/plan only.

    generate+ask=false returns ABSTAIN values=[] with optional SQL. CERTIFIED
    values from EngineBridge /dms/query are not ontology_plan — drop them.
    """
    out = dict(payload)
    out["live_5000_ci"] = False
    out["values"] = []
    if out.get("unsure") is True or out.get("abstain") is True:
        return attach_compute_plan_source(out)
    plan = typed_query_plan(out)
    sql = insights_query_sql(out)
    if plan is not None:
        out["query_plan"] = plan
        return attach_compute_plan_source(out)
    if sql:
        out["query_sql"] = sql
        return attach_compute_plan_source(out)
    return None


def _auth_headers(api_key: str | None) -> dict[str, str] | None:
    if not api_key:
        return None
    key = str(api_key)
    return {"X-API-Key": key, "Authorization": f"Bearer {key}"}


def _read_dict(res: httpx.Response) -> dict[str, Any] | None:
    if res.status_code != 200:
        return None
    try:
        payload = res.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def compute_query(
    base_url: str,
    *,
    question: str,
    session_id: str | None = None,
    space_id: str | None = None,
    ontology: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any] | None:
    """POST Cortex ontology_plan compute. None on miss / unreachable / 4xx / 5xx.

    Order: ``POST /v1/insights`` generate=true ask=false (OV/FreeRoute), then
    ``POST /dms/query`` for a typed ``query_plan``. An empty key is not replaced
    with a guessed secret. ``live_5000_ci`` is never claimed here.
    """
    headers = _auth_headers(api_key)
    root = base_url.rstrip("/")
    insights_body: dict[str, Any] = {
        "intent": question,
        "question": question,
        "ask": False,
        "generate": True,
        "session_id": session_id or "demo",
        "space_id": space_id,
        "consumer": "dms",
        "mode": ONTOLOGY_MODE,
        "model_preference": FREEROUTE_PREFERENCE,
    }
    if ontology is not None:
        insights_body["ontology"] = ontology
    dms_body: dict[str, Any] = {
        "question": question,
        "session_id": session_id or "demo",
        "space_id": space_id,
        "mode": ONTOLOGY_MODE,
    }
    if ontology is not None:
        dms_body["ontology"] = ontology
    try:
        with httpx.Client(timeout=timeout) as http:
            try:
                insights_res = http.post(
                    f"{root}{INSIGHTS_PATH}",
                    json=insights_body,
                    headers=headers,
                )
            except httpx.HTTPError:
                insights_res = None
            if insights_res is not None:
                insights_payload = _read_dict(insights_res)
                if isinstance(insights_payload, dict):
                    normalized = normalize_insights_compute(insights_payload)
                    if normalized is not None:
                        return normalized
            try:
                dms_res = http.post(
                    f"{root}{COMPUTE_PATH}",
                    json=dms_body,
                    headers=headers,
                )
            except httpx.HTTPError:
                return None
    except httpx.HTTPError:
        return None
    payload = _read_dict(dms_res)
    if payload is None:
        return None
    # Warehouse sql_used on /dms/query is not ontology_plan. Typed plan only.
    if typed_query_plan(payload) is None:
        if payload.get("unsure") is True or payload.get("abstain") is True:
            return attach_compute_plan_source(payload)
        return None
    return attach_compute_plan_source(payload)


__all__ = [
    "COMPUTE_PATH",
    "FREEROUTE_PREFERENCE",
    "INSIGHTS_PATH",
    "ONTOLOGY_MODE",
    "PLAN_SOURCES",
    "attach_compute_plan_source",
    "compute_query",
    "insights_query_sql",
    "normalize_insights_compute",
    "typed_query_plan",
]
