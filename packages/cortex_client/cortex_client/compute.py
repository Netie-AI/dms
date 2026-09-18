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
INSIGHTS_REACHED = "insights_reached"
INSIGHTS_STATUSES = frozenset({"CERTIFIED", "ABSTAIN", "REFUSE"})
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
    Insights ``generative.sql`` that Cortex marked ok/valid (or sql_used on that
    same generate envelope) counts as Cortex AI SQL. Refused generate SQL is not.
    """
    raw_gen = payload.get("generative")
    gen: dict[str, Any] = raw_gen if isinstance(raw_gen, dict) else {}
    if gen.get("ok") is False and gen.get("valid") is not True:
        return None
    candidates: tuple[Any, ...] = (gen.get("sql"),)
    if payload.get("phase") == "generate" or gen:
        candidates = (gen.get("sql"), payload.get("sql_used"), payload.get("sql"))
    for cand in candidates:
        sql = str(cand or "").strip()
        if sql and _SELECT_SQL.match(sql):
            return sql
    return None


def insights_was_reached(payload: dict[str, Any] | None) -> bool:
    """True when Cortex Insights answered (including unarmed/401 REFUSE)."""
    if not isinstance(payload, dict):
        return False
    if payload.get(INSIGHTS_REACHED) is True:
        return True
    if str(payload.get("phase") or "") in {"generate", "ontology", "ask"}:
        return True
    if isinstance(payload.get("generative"), dict):
        return True
    return str(payload.get("status") or "").upper() in INSIGHTS_STATUSES


def query_plan_from_insights_ranking(
    payload: dict[str, Any] | None,
    allowed_measures: set[str] | None = None,
) -> dict[str, Any] | None:
    """Typed plan from Cortex Insights metric ranking. Exact top id only.

    Cortex retrieve_ontology ranks pack metric ids. When generate is unarmed
    (or SQL is missing) the top ranked id that exists on the DMS ontology is
    still Cortex ontology_plan, not local bind_plan keyword matching.

    Do not skip a Cortex-only top id (stock_value_by_category) to a weaker
    DMS id (sku_count) — that answers the wrong question.
    """
    if not isinstance(payload, dict):
        return None
    onto = payload.get("ontology")
    if not isinstance(onto, dict):
        return None
    allowed = {str(m) for m in (allowed_measures or ()) if str(m).strip()}
    for row in onto.get("metrics") or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "").strip()
        if not mid:
            continue
        if allowed and mid not in allowed:
            return None
        return {
            "query_plan": {"measure": mid, "group_by": [], "filters": []},
            "plan_source": ONTOLOGY_MODE,
        }
    return None


def normalize_insights_compute(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Insights envelope → compute payload. Numbers stripped; SQL/plan only.

    generate+ask=false returns ABSTAIN values=[] with optional SQL. CERTIFIED
    values from EngineBridge /dms/query are not ontology_plan — drop them.
    """
    out = dict(payload)
    out["live_5000_ci"] = False
    out["values"] = []
    out[INSIGHTS_REACHED] = True
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


def insights_miss_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Insights answered without SQL/plan. Caller must not bind_plan over it."""
    out = dict(payload)
    out["live_5000_ci"] = False
    out["values"] = []
    out[INSIGHTS_REACHED] = True
    return attach_compute_plan_source(out)


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


def _insights_envelope(res: httpx.Response | None) -> dict[str, Any] | None:
    """Insights JSON on 200, or 401/403 A-0009 REFUSE body. None if unreachable."""
    if res is None:
        return None
    try:
        payload = res.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or "").upper()
    if res.status_code == 200 or status in INSIGHTS_STATUSES:
        return payload
    return None


def _has_ranked_metrics(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    onto = payload.get("ontology")
    if not isinstance(onto, dict):
        return False
    for row in onto.get("metrics") or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            return True
    return False


def _merge_ontology_ranking(
    base: dict[str, Any] | None, ranking: dict[str, Any]
) -> dict[str, Any]:
    """Keep POST status (401/REFUSE); attach GET /ontology ranking."""
    out = dict(base or {})
    out["ontology"] = ranking.get("ontology") or ranking
    if ranking.get("phase"):
        out.setdefault("phase", ranking.get("phase"))
    out[INSIGHTS_REACHED] = True
    return out


def _insights_ontology_get(
    http: httpx.Client,
    root: str,
    question: str,
    headers: dict[str, str] | None,
) -> dict[str, Any] | None:
    """YAML ranking. No FreeRoute. Used when generate 401 omits ontology."""
    get = getattr(http, "get", None)
    if not callable(get):
        return None
    try:
        res = get(
            f"{root}{INSIGHTS_PATH}/ontology",
            params={"q": question},
            headers=headers,
        )
    except (httpx.HTTPError, TypeError):
        return None
    return _insights_envelope(res)


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
    """POST Cortex ontology_plan compute. None on transport miss only.

    Order: ``POST /v1/insights`` generate=true ask=false (OV/FreeRoute),
    ``GET /v1/insights/ontology`` when generate omits ranking (A-0009 401),
    then ``POST /dms/query`` for a typed ``query_plan``. Insights 200 REFUSE
    / 401 still count as reached so isolated gen does not bind_plan over them.
    An empty key is not replaced with a guessed secret. ``live_5000_ci`` is
    never claimed here.
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
    insights_payload: dict[str, Any] | None = None
    dms_res: httpx.Response | None = None
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
            insights_payload = _insights_envelope(insights_res)
            if isinstance(insights_payload, dict):
                normalized = normalize_insights_compute(insights_payload)
                if normalized is not None:
                    return normalized
            # A-0009 401 has no ranking. GET /ontology is YAML, no OV generate.
            if not _has_ranked_metrics(insights_payload):
                ranking = _insights_ontology_get(http, root, question, headers)
                if ranking is not None:
                    insights_payload = _merge_ontology_ranking(
                        insights_payload, ranking
                    )
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
                if insights_payload is not None:
                    return insights_miss_payload(insights_payload)
                return None
    except httpx.HTTPError:
        return None
    if dms_res is None:
        if insights_payload is not None:
            return insights_miss_payload(insights_payload)
        return None
    payload = _read_dict(dms_res)
    if payload is not None:
        if typed_query_plan(payload) is not None:
            return attach_compute_plan_source(payload)
        if payload.get("unsure") is True or payload.get("abstain") is True:
            return attach_compute_plan_source(payload)
    # Insights answered (unarmed REFUSE / 401 / no SQL). Do not look like a
    # transport miss — isolated gen must not bind_plan over that.
    if insights_payload is not None:
        return insights_miss_payload(insights_payload)
    return None


__all__ = [
    "COMPUTE_PATH",
    "FREEROUTE_PREFERENCE",
    "INSIGHTS_PATH",
    "INSIGHTS_REACHED",
    "ONTOLOGY_MODE",
    "PLAN_SOURCES",
    "attach_compute_plan_source",
    "compute_query",
    "insights_miss_payload",
    "insights_query_sql",
    "insights_was_reached",
    "normalize_insights_compute",
    "query_plan_from_insights_ranking",
    "typed_query_plan",
]
