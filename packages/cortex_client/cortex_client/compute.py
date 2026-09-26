"""Off-contract Cortex compute helper — Insights generate, then leftover /dms/query.

Contract 1.2.0 has ask/submit/ledger only. This module is not a planner.
``POST /dms/query`` ignores ``mode`` and ``ontology`` on Cortex origin/main
(CortexOS/api/dms_query.py; KB F-0055) and does not return a typed
``query_plan.measure``. A 200 is the engine's own /dms/query answer, not a
FreeRoute generate+validate plan built from the context DMS sent.

``POST /v1/insights`` ``generate=true`` is Cortex Insights (OpenVault keys stay
in Cortex). It may REFUSE when unarmed; it is not a Cortex-internal
generate+validate path that always emits a typed plan.

Ask lanes call ``compute_insights`` (generate + ontology ranking + one ranked
retry). They never POST ``/dms/query``. ``compute_query`` still exists for
CONTRACT-FAKE-01. DMS never invents provider keys and never puts secrets in
the body.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

from cortex_client.insights import (
    INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT,
    INSIGHTS_FAIL_BEARER_MISSING,
    INSIGHTS_PATH,
    generate_bearer_refuse,
)

COMPUTE_PATH = "/dms/query"
ONTOLOGY_MODE = "ontology_plan"
PLAN_SOURCES = frozenset({"ontology_plan", "bind_plan", "other"})
PLAN_ORIGIN_GENERATE_SQL = "generate_sql"
PLAN_ORIGIN_ONTOLOGY_RANKING = "ontology_ranking"
PLAN_ORIGINS = frozenset({PLAN_ORIGIN_GENERATE_SQL, PLAN_ORIGIN_ONTOLOGY_RANKING})
INSIGHTS_REACHED = "insights_reached"
INSIGHTS_STATUSES = frozenset({"CERTIFIED", "ABSTAIN", "REFUSE"})
#: Product-lane Insights bound (default). Not CortexClient's 120s contract
#: timeout and not the pre-GEN-03 45s /dms/query stall. One httpx timeout for
#: the Client. Was 8s, which is shorter than a measured Cortex FreeRoute
#: generate (13-53s on the 52-question prove): 13/52 asks died as
#: insights_timeout before Cortex answered. Override per deploy with
#: ``DMS_INSIGHTS_ASK_TIMEOUT_SECONDS`` (Settings field of the same name).
INSIGHTS_ASK_TIMEOUT_SECONDS = 60.0
INSIGHTS_ASK_TIMEOUT_ENV = "DMS_INSIGHTS_ASK_TIMEOUT_SECONDS"
INSIGHTS_FAIL_UNARMED = "insights_unarmed"
INSIGHTS_FAIL_REFUSED = "insights_refused"
INSIGHTS_FAIL_UNAUTHORIZED = "insights_unauthorized"
INSIGHTS_FAIL_TIMEOUT = "insights_timeout"
INSIGHTS_FAIL_EMPTY = "insights_no_sql_no_ranking"
INSIGHTS_FAIL_REASONS = frozenset(
    {
        INSIGHTS_FAIL_UNARMED,
        INSIGHTS_FAIL_REFUSED,
        INSIGHTS_FAIL_UNAUTHORIZED,
        INSIGHTS_FAIL_TIMEOUT,
        INSIGHTS_FAIL_EMPTY,
        INSIGHTS_FAIL_BEARER_MISSING,
        INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT,
    }
)
_HTTP_STATUS_KEY = "_insights_http_status"
#: Stamped when generate timed out but the no-model ontology ranking answered.
GENERATE_TIMED_OUT = "generate_timed_out"


def insights_ask_timeout_seconds(raw: str | float | None = None) -> float:
    """Configured Insights ask bound: explicit value, env, else the default.

    Non-numeric, zero, negative or non-finite values fall back to the default
    rather than disabling the bound: a hung engine must still fail.
    """
    val: Any = raw if raw is not None else os.environ.get(INSIGHTS_ASK_TIMEOUT_ENV)
    if val is None or (isinstance(val, str) and not val.strip()):
        return INSIGHTS_ASK_TIMEOUT_SECONDS
    try:
        num = float(val)
    except (TypeError, ValueError):
        return INSIGHTS_ASK_TIMEOUT_SECONDS
    if not (num > 0) or num == float("inf"):
        return INSIGHTS_ASK_TIMEOUT_SECONDS
    return num


# FreeRoute pick stays in Cortex/OpenVault. Hint only; no provider ids or tokens.
FREEROUTE_PREFERENCE = "free+normal"
_SELECT_SQL = re.compile(r"(?is)^\s*(with|select)\b")
_TOP_FROM_ID = re.compile(r"(?:^|_)top(\d+)(?:_|$)", re.I)
_PACK_DIM: tuple[tuple[str, str, str], ...] = (
    ("by_category", "product", "category"),
    ("by_destination", "location", "location_code"),
    ("by_country", "supplier", "country"),
    ("by_sku", "product", "sku"),
    ("by_location", "location", "location_code"),
    ("sales_top", "product", "sku"),
    ("cold_storage", "location", "location_code"),
    ("expired", "product", "sku"),
    ("chemicals", "product", "sku"),
    ("cctv", "location", "cctv_camera_id"),
    ("supplier_rank", "supplier", "supplier_id"),
    ("low_stock", "product", "sku"),
    ("audit_overdue", "supplier", "supplier_id"),
)
_RANK_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "by",
        "cq",
        "per",
        "and",
        "or",
        "to",
        "for",
        "in",
        "on",
        "our",
        "we",
        "is",
        "are",
        "id",
        "wh",
        "do",
        "have",
        "how",
        "many",
        "what",
        "which",
        "show",
        "list",
    }
)


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


def _measure_plan(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and str(raw.get("measure") or "").strip():
        return raw
    return None


def typed_query_plan(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Measure-bearing plan from Cortex. Nested Insights ``generative`` is ok."""
    got = _measure_plan(payload.get("query_plan"))
    if got is not None:
        return got
    gen = payload.get("generative")
    if isinstance(gen, dict):
        got = _measure_plan(gen.get("query_plan")) or _measure_plan(gen.get("plan"))
        if got is not None:
            return got
        climb = gen.get("climb")
        if isinstance(climb, dict):
            got = _measure_plan(climb.get("query_plan")) or _measure_plan(
                climb.get("plan")
            )
            if got is not None:
                return got
    climb = payload.get("climb")
    if isinstance(climb, dict):
        return _measure_plan(climb.get("query_plan")) or _measure_plan(climb.get("plan"))
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


def ranked_measure_tokens(name: str) -> set[str]:
    """Stem tokens for Cortex pack id <-> DMS measure resolve. Not embeddings."""
    out: set[str] = set()
    blob = re.sub(r"([a-z])(\d)", r"\1 \2", (name or "").lower().replace("_", " "))
    for raw in re.findall(r"[a-z0-9]+", blob):
        if raw in _RANK_STOP or len(raw) < 2:
            continue
        out.add(raw)
        if len(raw) > 3 and raw.endswith("s"):
            out.add(raw[:-1])
    # Certified VQ-01 typo synonym of category (cq_top3_category_sales).
    # Without this, overlay ties sales_top5 vs top3_category on
    # "top 3 categoty sales" (top+sales only).
    if "categoty" in out:
        out.add("category")
    # Cortex metrics.yaml parent-SQL of stock_value_by_category uses
    # "inventory worth" / "worth per category", not the literal "stock value".
    if "worth" in out:
        out.add("value")
    return out


def resolve_ranked_measure(
    metric_id: str,
    allowed_measures: set[str] | None = None,
    *,
    aliases: dict[str, str] | None = None,
    specs: dict[str, str] | None = None,
    prefer: str | None = None,
) -> str | None:
    """Map a Cortex ranked pack id onto one DMS ontology measure.

    Same-intent only: exact id, ``cq_`` strip, spine alias, or >=2 token
    overlap on name+description. Do not skip a Cortex-only top id
    (stock_value_by_category) to a weaker unrelated id (sku_count).
    ``prefer`` is the question-locked measure; a different resolve abstains.
    """
    mid = str(metric_id or "").strip()
    if not mid:
        return None
    allowed = {str(m) for m in (allowed_measures or ()) if str(m).strip()}
    keys = [mid]
    if mid.lower().startswith("cq_"):
        keys.append(mid[3:])
    alias_map = {
        str(k).strip(): str(v).strip()
        for k, v in (aliases or {}).items()
        if str(k).strip() and str(v).strip()
    }
    lock = str(prefer or "").strip()
    if lock and allowed and lock not in allowed:
        lock = ""
    if not allowed:
        return lock or mid
    resolved: str | None = None
    for key in keys:
        mapped = alias_map.get(key)
        if mapped and mapped in allowed:
            resolved = mapped
            break
        if key in allowed:
            resolved = key
            break
    if resolved is None:
        toks = ranked_measure_tokens(mid)
        scored: list[tuple[int, str]] = []
        for name in sorted(allowed):
            blob = name + " " + str((specs or {}).get(name) or "")
            sc = len(toks & ranked_measure_tokens(blob))
            if sc >= 2:
                scored.append((sc, name))
        scored.sort(key=lambda row: (-row[0], row[1]))
        if len(scored) == 1 or (
            len(scored) > 1 and scored[0][0] > scored[1][0]
        ):
            resolved = scored[0][1]
    if lock:
        if resolved == lock:
            return lock
        if len(ranked_measure_tokens(mid) & ranked_measure_tokens(lock)) >= 1:
            return lock
        return None
    return resolved


def first_ranked_metric_id(payload: dict[str, Any] | None) -> str | None:
    """First Cortex Insights ranked pack metric id, or None."""
    if not isinstance(payload, dict):
        return None
    onto = payload.get("ontology")
    if not isinstance(onto, dict):
        return None
    for row in onto.get("metrics") or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "").strip()
        if mid:
            return mid
    return None


def pack_id_shape(metric_id: str) -> dict[str, Any]:
    """Group/limit/keep_gt encoded in a Cortex pack metric id. No SQL."""
    mid = str(metric_id or "").strip().lower()
    if mid.startswith("cq_"):
        mid = mid[3:]
    group_by: list[list[str]] = []
    for needle, obj, col in _PACK_DIM:
        if needle in mid:
            group_by = [[obj, col]]
            break
    if not group_by:
        toks = set(re.findall(r"[a-z0-9]+", mid.replace("_", " ")))
        if "category" in toks or "categoty" in toks:
            group_by = [["product", "category"]]
        elif "destination" in toks:
            group_by = [["location", "location_code"]]
        elif "country" in toks:
            group_by = [["supplier", "country"]]
    out: dict[str, Any] = {"group_by": group_by}
    top = _TOP_FROM_ID.search(mid)
    if top:
        out["limit"] = int(top.group(1))
    if "above_90" in mid or "above90" in mid:
        out["keep_gt"] = 90.0
    if "audit_overdue" in mid:
        # COUNT FILTER is 0 for on-time audits; drop those rows.
        out["keep_gt"] = 0.0
    return out


def overlay_pack_id_from_question(
    question: str | None,
    *,
    prefer: str | None,
    aliases: dict[str, str] | None,
    allowed: set[str] | None = None,
    specs: dict[str, str] | None = None,
) -> str | None:
    """Cortex pack id when YAML ranking exhausted the prefer lock.

    Prefer lock required. Alias dest must equal that lock. Score the pack-id
    tokens, or dest name+spec when the id is opaque (``low_stock_wh_a`` vs
    "below reorder"). Pack ids are Cortex catalog names on the spine, not a
    local ``bind_plan``. Do not call this after a Cortex-only overlapping abort.
    """
    lock = str(prefer or "").strip()
    q = str(question or "").strip()
    if not lock or not q:
        return None
    allowed_m = {str(m) for m in (allowed or ()) if str(m).strip()}
    if allowed_m and lock not in allowed_m:
        return None
    qtoks = ranked_measure_tokens(q)
    if not qtoks:
        return None
    dest_blob = lock + " " + str((specs or {}).get(lock) or "")
    dest_sc = len(qtoks & ranked_measure_tokens(dest_blob))
    scored: list[tuple[int, int, str]] = []
    for key, dest in (aliases or {}).items():
        src = str(key or "").strip()
        dst = str(dest or "").strip()
        if not src or dst != lock:
            continue
        if allowed_m and dst not in allowed_m:
            continue
        key_sc = len(qtoks & ranked_measure_tokens(src))
        if key_sc < 1 and dest_sc < 2:
            continue
        scored.append((key_sc, dest_sc, src))
    if not scored:
        return None
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return scored[0][2]


def ranking_is_noise(
    metric_id: str,
    *,
    question: str | None = None,
    prefer: str | None = None,
) -> bool:
    """Walk past this Cortex id. False = abort (intended metric, missing on DMS).

    No prefer: walk only when the id shares no content tokens with the ask.
    With a question-locked measure: walk ids that do not overlap that lock,
    even if they share a question token (sku_count on a revenue/SKU-list ask).
    """
    mid_toks = ranked_measure_tokens(metric_id)
    lock = str(prefer or "").strip()
    if lock:
        return not bool(mid_toks & ranked_measure_tokens(lock))
    qtoks = ranked_measure_tokens(question or "")
    if not qtoks:
        return False
    return not bool(mid_toks & qtoks)


def generate_retry_eligible(payload: dict[str, Any] | None) -> bool:
    """True when FreeRoute generate ran but emitted no SQL/plan, and ranking exists.

    UNARMED / 401 generate cannot emit SQL on a second shot — skip the retry.
    """
    if not isinstance(payload, dict):
        return False
    if insights_query_sql(payload) or typed_query_plan(payload):
        return False
    if not _has_ranked_metrics(payload):
        return False
    gen = payload.get("generative")
    gen_d = gen if isinstance(gen, dict) else {}
    climb = gen_d.get("climb") if gen_d else None
    climb_d = climb if isinstance(climb, dict) else {}
    final = str(climb_d.get("final") or "").upper()
    if final in {"UNARMED", "NO_KEY", "REFUSED_AUTH"}:
        return False
    if str(payload.get("status") or "").upper() == "REFUSE" and not gen_d:
        return False
    return bool(gen_d) or str(payload.get("phase") or "") == "generate"


def query_plan_from_insights_ranking(
    payload: dict[str, Any] | None,
    allowed_measures: set[str] | None = None,
    *,
    aliases: dict[str, str] | None = None,
    specs: dict[str, str] | None = None,
    prefer: str | None = None,
    question: str | None = None,
) -> dict[str, Any] | None:
    """Typed plan from Cortex Insights metric ranking.

    Default (no question): top id only. A Cortex-only top that does not
    resolve is not skipped to a weaker DMS id (stock_value_by_category must
    not become sku_count). Same-intent alias is climb, not a skip.

    With ``question``: walk past a top id that shares **no** content tokens
    with the ask (ranking noise). A Cortex-only id that overlaps the ask
    still aborts — that is the intended metric, missing on DMS.

    With ``prefer``: walk past a resolvable-but-wrong id that does not
    overlap the locked measure (sku_count on "Top 5 selling SKUs by
    revenue"). A Cortex-only id that overlaps the lock still aborts.

    After the walk exhausts (all noise, none resolved): overlay a
    question-matched spine pack id onto the prefer lock. Not skip-to-weaker
    on abort. Not bind_plan.
    """
    if not isinstance(payload, dict):
        return None
    onto = payload.get("ontology")
    if not isinstance(onto, dict):
        return None
    allowed = {str(m) for m in (allowed_measures or ()) if str(m).strip()}
    lock = str(prefer or "").strip() or None
    alias_map = {
        str(k).strip(): str(v).strip()
        for k, v in (aliases or {}).items()
        if str(k).strip() and str(v).strip()
    }
    for row in onto.get("metrics") or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "").strip()
        if not mid:
            continue
        resolved = resolve_ranked_measure(
            mid,
            allowed if allowed else None,
            aliases=alias_map,
            specs=specs,
            prefer=lock,
        )
        if allowed and resolved is None:
            if ranking_is_noise(mid, question=question, prefer=lock):
                continue
            return None
        return {
            "query_plan": {
                "measure": resolved or mid,
                "group_by": [],
                "filters": [],
                "ranked_id": mid,
            },
            "plan_source": ONTOLOGY_MODE,
        }
    pack_id = overlay_pack_id_from_question(
        question,
        prefer=lock,
        aliases=alias_map,
        allowed=allowed or None,
        specs=specs,
    )
    if not pack_id:
        return None
    dest = alias_map.get(pack_id) or lock
    if not dest or (allowed and dest not in allowed):
        return None
    return {
        "query_plan": {
            "measure": dest,
            "group_by": [],
            "filters": [],
            "ranked_id": pack_id,
        },
        "plan_source": ONTOLOGY_MODE,
    }


def _ontology_rank_ctx(
    ontology: dict[str, Any] | None,
) -> tuple[set[str], dict[str, str], dict[str, str], dict[str, Any]]:
    onto = ontology if isinstance(ontology, dict) else {}
    raw_measures = onto.get("measures")
    measures: dict[str, Any] = raw_measures if isinstance(raw_measures, dict) else {}
    allowed = {str(k) for k in measures if str(k).strip()}
    raw_alias = onto.get("measure_aliases")
    aliases = {
        str(k).strip(): str(v).strip()
        for k, v in (raw_alias.items() if isinstance(raw_alias, dict) else [])
        if str(k).strip() and str(v).strip()
    }
    specs = {
        str(k): str(v.get("description") or "") if isinstance(v, dict) else ""
        for k, v in measures.items()
    }
    raw_slots = onto.get("intent_slots")
    slots: dict[str, Any] = raw_slots if isinstance(raw_slots, dict) else {}
    return allowed, aliases, specs, slots


def typed_ranked_retry_plan(
    payload: dict[str, Any] | None,
    *,
    ontology: dict[str, Any] | None = None,
    question: str = "",
) -> dict[str, Any] | None:
    """Resolved DMS measure + retrieve/pack slots for FreeRoute generate retry.

    Walks ranking the same way as ``query_plan_from_insights_ranking`` so a
    leading sku_count does not retry as the plan for a revenue ask.
    """
    allowed, aliases, specs, slots = _ontology_rank_ctx(ontology)
    prefer = str(slots.get("measure") or "").strip() or None
    ranked = query_plan_from_insights_ranking(
        payload,
        allowed or None,
        aliases=aliases,
        specs=specs,
        prefer=prefer,
        question=question,
    )
    if ranked is None:
        return None
    raw = ranked.get("query_plan")
    if not isinstance(raw, dict):
        return None
    measure = str(raw.get("measure") or "").strip()
    if not measure:
        return None
    ranked_id = str(raw.get("ranked_id") or "").strip()
    shape = pack_id_shape(ranked_id or measure)
    group = slots.get("group_by") or raw.get("group_by") or shape.get("group_by") or []
    if not isinstance(group, list):
        group = []
    plan: dict[str, Any] = {
        "measure": measure,
        "group_by": group,
        "filters": list(slots.get("filters") or []),
        "ranked_id": ranked_id,
    }
    limit = slots.get("limit")
    if limit is None:
        limit = shape.get("limit")
    if isinstance(limit, int):
        plan["limit"] = limit
    keep = slots.get("keep_gt")
    if keep is None:
        keep = shape.get("keep_gt")
    if isinstance(keep, (int, float)):
        plan["keep_gt"] = float(keep)
    # QUAL-GUARD-01: add extracted grains/dims. Localized for dms#289 rebase.
    from cortex_client.qualifiers import apply_qualifiers_to_retry_plan

    return apply_qualifiers_to_retry_plan(plan, question)


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
        out["plan_origin"] = PLAN_ORIGIN_GENERATE_SQL
        return attach_compute_plan_source(out)
    if sql:
        out["query_sql"] = sql
        out["plan_origin"] = PLAN_ORIGIN_GENERATE_SQL
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
        out = dict(payload)
        out[_HTTP_STATUS_KEY] = int(res.status_code)
        return out
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
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.TimeoutException):
            raise
        return None
    except TypeError:
        return None
    return _insights_envelope(res)


def _insights_generate_post(
    http: httpx.Client,
    root: str,
    body: dict[str, Any],
    headers: dict[str, str] | None,
) -> dict[str, Any] | None:
    """POST /v1/insights generate. None on transport miss."""
    try:
        res = http.post(
            f"{root}{INSIGHTS_PATH}",
            json=body,
            headers=headers,
        )
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.TimeoutException):
            raise
        return None
    return _insights_envelope(res)


def insights_fail_reason(payload: dict[str, Any] | None) -> str | None:
    """Named fail-closed reason stamped by ``compute_insights``, or None."""
    if not isinstance(payload, dict):
        return None
    raw = str(payload.get("insights_fail") or "").strip()
    return raw if raw in INSIGHTS_FAIL_REASONS else None


def insights_fail_payload(
    reason: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Insights answered without a usable plan. Never a bind_plan miss."""
    out = insights_miss_payload(payload or {})
    out["insights_fail"] = reason if reason in INSIGHTS_FAIL_REASONS else INSIGHTS_FAIL_EMPTY
    out[INSIGHTS_REACHED] = True
    return out


def classify_insights_fail(
    payload: dict[str, Any] | None, *, timed_out: bool = False
) -> str | None:
    """Named ABSTAIN when Insights produced no SQL and no ranking.

    Timeout wins. 401/403 is unauthorized. Unarmed climb is unarmed. Other
    REFUSE is refused. Empty 200 is no_sql_no_ranking. Ranking or SQL is not
    a fail — the ask path compiles those. Transport miss (None) is not named.
    """
    if timed_out:
        return INSIGHTS_FAIL_TIMEOUT
    if not isinstance(payload, dict):
        return None
    if insights_query_sql(payload) or typed_query_plan(payload):
        return None
    if _has_ranked_metrics(payload):
        return None
    http_status = int(payload.get(_HTTP_STATUS_KEY) or 0)
    gen = payload.get("generative")
    gen_d = gen if isinstance(gen, dict) else {}
    climb = gen_d.get("climb")
    climb_d = climb if isinstance(climb, dict) else {}
    final = str(climb_d.get("final") or "").upper()
    if http_status in {401, 403} or final == "REFUSED_AUTH":
        return INSIGHTS_FAIL_UNAUTHORIZED
    if final in {"UNARMED", "NO_KEY"}:
        return INSIGHTS_FAIL_UNARMED
    status = str(payload.get("status") or "").upper()
    if status == "REFUSE" or payload.get("ok") is False:
        return INSIGHTS_FAIL_REFUSED
    return INSIGHTS_FAIL_EMPTY


def _insights_body(
    question: str,
    *,
    session_id: str | None,
    space_id: str | None,
    ontology: dict[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
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
        body["ontology"] = ontology
        if isinstance(ontology, dict):
            slots = ontology.get("intent_slots")
            if isinstance(slots, dict) and slots:
                body["intent_slots"] = slots
    return body


def _leg_kind(payload: dict[str, Any] | None) -> str:
    """sql / plan / nothing for one Insights generate POST."""
    if not isinstance(payload, dict):
        return "nothing"
    if insights_query_sql(payload):
        return "sql"
    if typed_query_plan(payload):
        return "plan"
    return "nothing"


#: SERVED-ATTR-01 (dms#305). Per-call attribution Cortex stamps on each
#: generate response (its RouteStamp through ``served_*``). Copied as received.
SERVED_LEG_KEYS: tuple[str, ...] = ("served_provider", "served_model")
_NO_MODEL_CLIMB = frozenset({"UNARMED", "NO_KEY", "REFUSED_AUTH"})


def _leg(payload: dict[str, Any] | None, returned: str) -> dict[str, Any]:
    """One generate leg: what it returned, plus its served_* when reported."""
    leg: dict[str, Any] = {"returned": returned}
    if isinstance(payload, dict):
        for key in SERVED_LEG_KEYS:
            if key in payload:
                leg[key] = payload[key]
    return leg


def generate_model_called(payload: dict[str, Any] | None) -> bool:
    """True when an Insights generate call may have reached a model.

    False only on wire evidence that no model ran: no payload (transport miss
    or no call), a DMS-side bearer refuse (no HTTP call), a 401/403, or a
    climb Cortex reports as UNARMED / NO_KEY / REFUSED_AUTH. Anything else
    that reached generate - SQL, a plan, a timeout, an empty answer - counts
    as called, so missing attribution shows as missing, never as none.
    """
    if not isinstance(payload, dict):
        return False
    gen = payload.get("generative")
    gen_d = gen if isinstance(gen, dict) else {}
    if isinstance(gen_d.get("stamp"), dict):
        return True
    if str(payload.get("insights_fail") or "") in {
        INSIGHTS_FAIL_BEARER_MISSING,
        INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT,
    }:
        return False
    raw_legs = payload.get("generate_legs")
    if isinstance(raw_legs, dict):
        got = raw_legs.get("legs")
        legs: list[Any] = got if isinstance(got, list) else []
        if not legs and not int(raw_legs.get("count") or 0):
            return False
        if any(
            isinstance(leg, dict) and leg.get("returned") in {"sql", "plan", "timeout"}
            for leg in legs
        ):
            return True
    elif insights_query_sql(payload) or typed_query_plan(payload):
        return True
    if int(payload.get(_HTTP_STATUS_KEY) or 0) in {401, 403}:
        return False
    climb = gen_d.get("climb")
    final = str((climb if isinstance(climb, dict) else {}).get("final") or "").upper()
    return final not in _NO_MODEL_CLIMB


def _run_insights_legs(
    http: httpx.Client,
    root: str,
    question: str,
    insights_body: dict[str, Any],
    headers: dict[str, str] | None,
    ontology: dict[str, Any] | None,
    *,
    budget_s: float | None = None,
) -> dict[str, Any] | None:
    """Generate, optional ontology GET, one ranked retry. No /dms/query.

    A generate timeout is not the end of the ask: the no-model ranking
    (``GET /v1/insights/ontology``) still runs, and when it ranks a metric the
    payload carries that ranking with ``generate_timed_out`` stamped. Only
    when generate timed out AND ranking is unavailable does the timeout
    propagate (named ``insights_timeout`` by the caller). A retry that would
    exceed the budget, or that times out, keeps the first leg's ranking.
    """
    from cortex_client.qualifiers import retry_plan_covers_qualifiers

    started = time.monotonic()
    legs: list[dict[str, Any]] = []
    insights_payload: dict[str, Any] | None = None
    gen_timed_out = False
    try:
        insights_payload = _insights_generate_post(http, root, insights_body, headers)
        legs.append(_leg(insights_payload, _leg_kind(insights_payload)))
    except httpx.TimeoutException:
        gen_timed_out = True
        legs.append({"returned": "timeout"})
    if not _has_ranked_metrics(insights_payload):
        try:
            ranking = _insights_ontology_get(http, root, question, headers)
        except httpx.TimeoutException:
            ranking = None
        if ranking is not None:
            insights_payload = _merge_ontology_ranking(insights_payload, ranking)
    if gen_timed_out:
        if not _has_ranked_metrics(insights_payload):
            raise httpx.TimeoutException("insights generate timed out; no ranking")
        out = dict(insights_payload or {})
        out[GENERATE_TIMED_OUT] = True
        out["generate_legs"] = {"count": len(legs), "legs": legs}
        return out
    ranked_plan = typed_ranked_retry_plan(
        insights_payload, ontology=ontology, question=question
    )
    # QUAL-GUARD-01: never send a retry plan that dropped a qualifier.
    retry_ok = bool(ranked_plan) and retry_plan_covers_qualifiers(
        ranked_plan, question
    )
    # A second generate that cannot finish inside the budget only delays the
    # ranking answer the first leg already holds.
    within_budget = budget_s is None or (time.monotonic() - started) < budget_s / 2
    if (
        generate_retry_eligible(insights_payload)
        and retry_ok
        and ranked_plan is not None
        and within_budget
    ):
        retry_body = dict(insights_body)
        retry_body["query_plan"] = {
            k: v for k, v in ranked_plan.items() if k != "ranked_id"
        }
        retry_body["ranked_metric"] = (
            ranked_plan.get("ranked_id") or ranked_plan["measure"]
        )
        retry_body["generate_retry"] = "ranked_slots"
        try:
            retry_payload = _insights_generate_post(http, root, retry_body, headers)
            legs.append(_leg(retry_payload, _leg_kind(retry_payload)))
        except httpx.TimeoutException:
            retry_payload = None
            legs.append({"returned": "timeout"})
        if isinstance(retry_payload, dict):
            insights_payload = _merge_ontology_ranking(
                retry_payload, insights_payload or {}
            )
    if isinstance(insights_payload, dict):
        out = dict(insights_payload)
        out["generate_legs"] = {"count": len(legs), "legs": legs}
        return out
    return insights_payload


def compute_query(
    base_url: str,
    *,
    question: str,
    session_id: str | None = None,
    space_id: str | None = None,
    ontology: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float = 120.0,
    dms_query: bool = True,
) -> dict[str, Any] | None:
    """POST Cortex Insights generate, then leftover ``/dms/query``. None on miss.

    Not a generate+validate planner Cortex implements. Cortex ``/dms/query``
    ignores ``mode``/``ontology`` and does not emit ``query_plan.measure``
    (KB F-0055). Insights generate may return SELECT SQL or REFUSE; that is
    Insights, not a typed-plan guarantee.

    Ask lanes pass ``dms_query=False`` (or call ``compute_insights``) so they
    never POST ``/dms/query``. Deleting this function is CONTRACT-FAKE-01.

    Order: ``POST /v1/insights`` generate=true ask=false, ``GET
    /v1/insights/ontology`` when ranking is omitted, one ranked-slot generate
    retry when generate ran with no SQL/plan (not UNARMED), then optional
    ``POST /dms/query``. Ranking stays attached when generate SQL is present so
    a validate-fail can climb via ontology_plan slots. Insights 200 REFUSE /
    401 still count as reached so isolated gen does not bind_plan over them.
    An empty key is not replaced with a guessed secret. ``live_5000_ci`` is
    never claimed here.
    """
    # KEY-01 (dms#273): missing (None), empty, demo and insecure-transport
    # keys make no HTTP call on either lane. The ask lane abstains named; the
    # leftover dms_query=True lane misses (None) without posting anything.
    refuse = generate_bearer_refuse(api_key, base_url)
    if refuse:
        return None if dms_query else insights_fail_payload(refuse)
    headers = _auth_headers(api_key)
    root = base_url.rstrip("/")
    insights_body = _insights_body(
        question, session_id=session_id, space_id=space_id, ontology=ontology
    )
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
    timed_out = False
    try:
        with httpx.Client(timeout=timeout) as http:
            try:
                insights_payload = _run_insights_legs(
                    http,
                    root,
                    question,
                    insights_body,
                    headers,
                    ontology,
                    budget_s=float(timeout),
                )
            except httpx.TimeoutException:
                timed_out = True
                if not dms_query:
                    return insights_fail_payload(INSIGHTS_FAIL_TIMEOUT, insights_payload)
                return None
            ranked_plan = typed_ranked_retry_plan(
                insights_payload, ontology=ontology, question=question
            )
            if isinstance(insights_payload, dict):
                if ranked_plan:
                    dms_body.setdefault(
                        "query_plan",
                        {k: v for k, v in ranked_plan.items() if k != "ranked_id"},
                    )
                    dms_body["ranked_metric"] = (
                        ranked_plan.get("ranked_id") or ranked_plan["measure"]
                    )
                else:
                    rid = first_ranked_metric_id(insights_payload)
                    if rid:
                        dms_body.setdefault(
                            "query_plan",
                            {"measure": rid, "group_by": [], "filters": []},
                        )
                        dms_body["ranked_metric"] = rid
                normalized = normalize_insights_compute(insights_payload)
                if normalized is not None:
                    return normalized
                if not dms_query:
                    fail = classify_insights_fail(
                        insights_payload, timed_out=timed_out
                    )
                    if fail:
                        return insights_fail_payload(fail, insights_payload)
                    return insights_miss_payload(insights_payload)
            elif not dms_query:
                fail = classify_insights_fail(insights_payload, timed_out=timed_out)
                if fail:
                    return insights_fail_payload(fail, insights_payload)
                return None
            try:
                dms_res = http.post(
                    f"{root}{COMPUTE_PATH}",
                    json=dms_body,
                    headers=headers,
                )
            except httpx.TimeoutException:
                if insights_payload is not None:
                    return insights_miss_payload(insights_payload)
                return None
            except httpx.HTTPError:
                if insights_payload is not None:
                    return insights_miss_payload(insights_payload)
                return None
    except httpx.TimeoutException:
        if not dms_query:
            return insights_fail_payload(INSIGHTS_FAIL_TIMEOUT, insights_payload)
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


def compute_insights(
    base_url: str,
    *,
    question: str,
    session_id: str | None = None,
    space_id: str | None = None,
    ontology: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """Ask-lane Insights planner: generate + ranking + one retry. No /dms/query.

    Timeout is ``insights_ask_timeout_seconds()`` (env
    ``DMS_INSIGHTS_ASK_TIMEOUT_SECONDS``, default 60s) unless the caller
    passes a bound. A generate timeout still runs the no-model ontology
    ranking; ``insights_timeout`` is named only when both fail. Fail-closed
    payloads stamp ``insights_fail`` with a named reason. OpenVault keys stay
    in Cortex; this client forwards ``api_key`` when already configured and
    never invents one.
    """
    if timeout is None:
        timeout = insights_ask_timeout_seconds()
    return compute_query(
        base_url,
        question=question,
        session_id=session_id,
        space_id=space_id,
        ontology=ontology,
        api_key=api_key,
        timeout=timeout,
        dms_query=False,
    )


__all__ = [
    "COMPUTE_PATH",
    "FREEROUTE_PREFERENCE",
    "GENERATE_TIMED_OUT",
    "INSIGHTS_ASK_TIMEOUT_ENV",
    "INSIGHTS_ASK_TIMEOUT_SECONDS",
    "INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT",
    "INSIGHTS_FAIL_BEARER_MISSING",
    "INSIGHTS_FAIL_REASONS",
    "INSIGHTS_PATH",
    "INSIGHTS_REACHED",
    "ONTOLOGY_MODE",
    "PLAN_ORIGINS",
    "PLAN_ORIGIN_GENERATE_SQL",
    "PLAN_ORIGIN_ONTOLOGY_RANKING",
    "PLAN_SOURCES",
    "SERVED_LEG_KEYS",
    "attach_compute_plan_source",
    "classify_insights_fail",
    "compute_insights",
    "compute_query",
    "first_ranked_metric_id",
    "generate_model_called",
    "generate_retry_eligible",
    "insights_fail_payload",
    "insights_ask_timeout_seconds",
    "insights_fail_reason",
    "insights_miss_payload",
    "insights_query_sql",
    "insights_was_reached",
    "normalize_insights_compute",
    "overlay_pack_id_from_question",
    "pack_id_shape",
    "query_plan_from_insights_ranking",
    "ranked_measure_tokens",
    "ranking_is_noise",
    "resolve_ranked_measure",
    "typed_query_plan",
    "typed_ranked_retry_plan",
]
