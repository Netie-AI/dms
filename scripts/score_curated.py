"""Genie-bar instrument: curated CEO questions through POST /v1/chat/ask.

Same two numbers as score_answers.py (F26). This pack is the Databricks
walkthrough analog: exact certified questions a manager can click, plus
traps that must abstain. It does not start EPIC-019 (no new VQ repo).

  python scripts/score_curated.py --self-check
  python scripts/score_curated.py --live --oracle-db PATH
  python scripts/score_curated.py --ab --oracle-db PATH
  python scripts/score_curated.py --climb --url URL --oracle-db PATH
  python scripts/score_curated.py --climb --ab --url URL --oracle-db PATH
  python scripts/score_curated.py --prove-path
  python scripts/score_curated.py --prove-path --url URL --oracle-db PATH
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from oracle_row_match import (  # noqa: E402
    envelope_rows,
    read_schema_version,
    rows_mismatch_reason,
    run_oracle_select,
)

DEFAULT_PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
DEFAULT_ORACLES = ROOT / "tests" / "fixtures" / "curated_ceo" / "oracles.yaml"
DEFAULT_URL = "http://127.0.0.1:8090"
# Documented Platform target for GEN-02. Not a silent default (fail closed).
PLATFORM_API = "https://studio.netie.ai/api"

CONFIDENT = frozenset(
    {"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS"}
)
REFUSE = frozenset({"abstain", "refuse", "trap"})
# HTTP refusals from Space grants / manifest. Not a transport outage.
GRANT_REFUSAL_STATUS = frozenset({403, 409})
EXACT_ROUTES = frozenset({"governed_metric", "verified_query"})
GENERATIVE_ROUTES = frozenset({"generated"})
PLAN_SOURCES = frozenset({"ontology_plan", "bind_plan", "other"})
HOLD_MAY_CLEAR_FIELD = "Phase A HOLD may clear"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3
# curated_ceo questions.yaml size at SCORE-ROWS-01. Category figures never
# use a smaller denominator. Not a live score.
PACK_DENOMINATOR = 52
FIGURE_LABEL_FIXTURE = "CI fixtures, not live"
FIGURE_LABEL_LIVE = "live row-compared"
LEGACY_JUDGE_LABEL = "scorer_ok_rows_not_compared"
# Count inside INVALID. Not an outcome label. dms#284 leftover: star/UNION
# SQL comes back with a masked typed date.
OVERMASK_STAR_KEY = "overmask_star:dms#284"

# Live curated_ceo @ 91c5cc99 (VQ-04 refuse traps). Frozen measurement, not a target.
BASELINE_91C5CC99: dict[str, Any] = {
    "commit": "91c5cc99",
    "n": 26,
    "ok": 7,
    "layer": 10,
    "abstain": 9,
    "wrong": 0,
}
# Isolated A/B @ a9578348 (GEN-01, Platform prove). Frozen, not a slogan.
# 10/26 = 38.46 pct, 1/26 = 3.85 pct. Do not edit to invent a rise.
BASELINE_AB_A9578348: dict[str, Any] = {
    "commit": "a9578348",
    "n": 26,
    "exact_answered": 10,
    "generative_answered": 1,
    "exact_coverage_answered_pct": 38.46,
    "generative_coverage_answered_pct": 3.85,
    "wrong": 0,
}

# QUALIFIED 15/26 (57.69 pct) gen answered on this pack. Not proven ontology_plan.
# Not COMPLETE. Prove-path labels the producer; Platform owns live counts.
# Frozen n=26 is a floor: leftover Cortex L0s may grow the pack (n>=26).
QUALIFIED_GEN_COVERAGE_CLAIM: dict[str, Any] = {
    "pack": "curated_ceo",
    "n": 26,
    "status": "QUALIFIED",
    "offline_ab_answered": 15,
    "offline_ab_answered_pct": 57.69,
    "note": "15/26 gen answered is QUALIFIED pending plan_source prove",
}

# #212 KEEP_HOLD was answered=17/26: leftover cq_audit_overdue never entered
# the frozen 26-pack denominator. Climb-05 leftover L0s must be scored.
# Certified synonyms are Cortex certified_queries.yaml phrases, not PACK_METRICS.
CLIMB05_LEFTOVER_L0: tuple[str, ...] = (
    "cq_audit_overdue",
    "cq_sku_count_syn_short",
    "cq_sku_count_syn_label",
    "cq_sales_top5_syn_skus",
    "cq_top3_category_syn_value",
)

# Dual KEEP_HOLD #212+#214: Platform stamped 17/26 after both merges.
# Remaining 9/26 are planted refuse traps (WRONG if greened). The 4 Cortex
# synonyms below already lock measures that compiled for their parent L0s
# at #210. Flat 17 means they were never POSTed. Union them in this script
# so live --prove-path cannot score frozen 26. Audit overdue is asked but
# not required for the rise (live last_audit_date ceiling).
CLIMB06_RISE_L0: tuple[dict[str, Any], ...] = (
    {
        "id": "cq_sku_count_syn_short",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "How many SKUs in inventory?",
    },
    {
        "id": "cq_sku_count_syn_label",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "SKU count in inventory",
    },
    {
        "id": "cq_sales_top5_syn_skus",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "Top 5 SKUs by revenue",
    },
    {
        "id": "cq_top3_category_syn_value",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "top 3 categories by sales value",
    },
)
CLIMB06_RISE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CLIMB06_RISE_L0)
CLIMB06_UNION_L0: tuple[dict[str, Any], ...] = CLIMB06_RISE_L0 + (
    {
        "id": "cq_audit_overdue",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "Which suppliers have an audit overdue?",
    },
)

# #216 RISE_PASS: ontology_plan=21 / n=31. The 4 climb-06 synonyms plus the
# frozen 17 already compile. Unused Cortex certified_queries.yaml synonyms
# below were never POSTed. Union them so live --prove-path cannot score
# the 21/31 pack. Not PACK_METRICS. Audit overdue still optional.
CLIMB07_RISE_L0: tuple[dict[str, Any], ...] = (
    {
        "id": "cq_sales_top5_syn_sales",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "Top 5 selling SKUs by sales",
    },
    {
        "id": "cq_top3_category_syn_show",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "show top 3 category sales",
    },
    {
        "id": "cq_top3_category_syn_plain",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "top 3 category sales",
    },
)
CLIMB07_RISE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CLIMB07_RISE_L0)
CLIMB07_UNION_L0: tuple[dict[str, Any], ...] = CLIMB06_UNION_L0 + CLIMB07_RISE_L0

# #218 RISE_PASS: ontology_plan=24 / n=34. The 7 climb-05/06/07 synonyms
# plus the frozen 17 already compile. Unused Cortex leftover below was
# never POSTed. Union them so live --prove-path cannot score the 24/34
# pack. Not PACK_METRICS. Audit overdue still optional.
CLIMB08_RISE_L0: tuple[dict[str, Any], ...] = (
    {
        "id": "cq_top3_category_syn_typo",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "top 3 categoty sales",
    },
    {
        "id": "ops_sku_count",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "How many SKUs do we have in inventory?",
    },
    {
        "id": "ops_sku_count_by_category",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "Show SKU count by category",
    },
)
CLIMB08_RISE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CLIMB08_RISE_L0)
CLIMB08_UNION_L0: tuple[dict[str, Any], ...] = CLIMB07_UNION_L0 + CLIMB08_RISE_L0

# #220 RISE_PASS: ontology_plan=27 / n=37. The 10 climb-05/06/07/08
# leftovers plus the frozen 17 already compile. Unused Cortex leftover
# below was never POSTed. Union them so live --prove-path cannot score
# the 27/37 pack. Not PACK_METRICS. Audit overdue still optional.
CLIMB09_RISE_L0: tuple[dict[str, Any], ...] = (
    {
        "id": "ops_sku_count_syn_short",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "How many SKUs in inventory?",
    },
    {
        "id": "ops_sku_count_syn_label",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "SKU count in inventory",
    },
    {
        "id": "ops_chemicals_list",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "List chemicals in inventory",
    },
)
CLIMB09_RISE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CLIMB09_RISE_L0)
CLIMB09_UNION_L0: tuple[dict[str, Any], ...] = CLIMB08_UNION_L0 + CLIMB09_RISE_L0

# #222 RISE_PASS: ontology_plan=30 / n=40. The 13 climb-05/06/07/08/09
# leftovers plus the frozen 17 already compile. Unused Cortex leftover
# below was never POSTed. Union them so live --prove-path cannot score
# the 30/40 pack. Not PACK_METRICS expansion. Audit overdue still optional.
CLIMB10_RISE_L0: tuple[dict[str, Any], ...] = (
    {
        "id": "ops_expired_items",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "Which items are expired?",
    },
    {
        "id": "ops_cold_storage",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "Which locations are cold storage?",
    },
    {
        "id": "ops_capacity_above_90",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "Which locations are above 90 percent capacity?",
    },
)
CLIMB10_RISE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CLIMB10_RISE_L0)
CLIMB10_UNION_L0: tuple[dict[str, Any], ...] = CLIMB09_UNION_L0 + CLIMB10_RISE_L0

# #224 RISE_PASS: ontology_plan=33 / n=43. The 16 climb-05/06/07/08/09/10
# leftovers plus the frozen 17 already compile. Unused Cortex leftover
# below was never POSTed. Union them so live --prove-path cannot score
# the 33/43 pack. Not PACK_METRICS expansion. Audit overdue still optional.
CLIMB11_RISE_L0: tuple[dict[str, Any], ...] = (
    {
        "id": "ops_capacity_utilisation",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "Show warehouse capacity utilisation",
    },
    {
        "id": "ops_cctv_wh_a",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "Show the CCTV camera for warehouse A",
    },
    {
        "id": "ops_low_stock_wh_a",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "Which SKUs are below reorder level in warehouse A?",
    },
)
CLIMB11_RISE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CLIMB11_RISE_L0)
CLIMB11_UNION_L0: tuple[dict[str, Any], ...] = CLIMB10_UNION_L0 + CLIMB11_RISE_L0

# #226 RISE_PASS: ontology_plan=36 / n=46. The 19 climb-05/06/07/08/09/10/11
# leftovers plus the frozen 17 already compile. Unused Cortex leftover
# below is Ops parent-SQL of certified L0s already live (spine slots,
# not PACK_METRICS, not last_audit_date). Union them so live --prove-path
# cannot score the 36/46 pack. Audit overdue still optional.
CLIMB12_RISE_L0: tuple[dict[str, Any], ...] = (
    {
        "id": "ops_sku_count_by_category_syn",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "SKU count by category",
    },
    {
        "id": "ops_stock_value_syn",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "stock value by category",
    },
    {
        "id": "ops_shipment_cost_syn",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "shipment cost by destination",
    },
)
CLIMB12_RISE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CLIMB12_RISE_L0)
CLIMB12_UNION_L0: tuple[dict[str, Any], ...] = CLIMB11_UNION_L0 + CLIMB12_RISE_L0

# #228 RISE_PASS: ontology_plan=39 / n=49. The 22 climb-05/06/07/08/09/10/11/12
# leftovers plus the frozen 17 already compile. Unused leftover below is
# Cortex metrics.yaml parent-SQL of certified L0s already live (retrieve
# slots, not PACK_METRICS, not last_audit_date). Union them so live
# --prove-path cannot score the 39/49 pack. Audit overdue still optional.
CLIMB13_RISE_L0: tuple[dict[str, Any], ...] = (
    {
        "id": "cq_sku_count_by_category_per",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "how many SKUs per category",
    },
    {
        "id": "cq_stock_value_worth",
        "space": "finance",
        "expect": "l0",
        "min_rows": 1,
        "question": "what is our inventory worth per category",
    },
    {
        "id": "ops_freight_spend_destination",
        "space": "ops",
        "expect": "l0",
        "min_rows": 1,
        "question": "freight spend per destination",
    },
)
CLIMB13_RISE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CLIMB13_RISE_L0)
CLIMB13_UNION_L0: tuple[dict[str, Any], ...] = CLIMB12_UNION_L0 + CLIMB13_RISE_L0

# Platform D distill -> Netie-native mapping. Ideas only; no vendor paste.
DISTILL: dict[str, Any] = {
    "ideas_only": True,
    "vendors_not_pasted": (
        "DB-GPT",
        "mybot",
        "n8n",
        "OpenWillow",
        "guaca",
        "rakazo",
        "Semantica",
        "Graphiti",
        "Mem0",
        "DeepAgents",
    ),
    "ladder": (
        "certified_first_then_generative",
        "ontology_spine_demo_ontology",
        "hybrid_fuse_and_crag_grades",
        "text2sql_cortex_compute_plus_bind_plan",
    ),
    "ontology_spine": {
        "kind": "demo_ontology",
        "source": "packages/executor/dms_executor/ontology.py",
        "yaml_pack_format": False,
        "from_manifest": "extract path, not certified SQL",
        "retrieve_yaml": "packages/executor/dms_executor/ontology_spine.yaml",
    },
    "text2sql": {
        "cortex": "POST /v1/insights generate=true ask=false (ontology_plan)",
        "fallback": "POST /dms/query typed query_plan",
        "slots": "bind_plan typed query_plan on compute miss (isolated gen only)",
        "vendor_sdk": False,
    },
    "try_harder": (
        "retrieve then Cortex compute then bind_plan then compile then "
        "execute-validate; abstain only after that attempt (keep_gt gate). "
        "ML route/train/apply not this slice. No LangChain/LangGraph."
    ),
    "founder_lock": (
        "semantic_retrieve",
        "ontology_relations",
        "generate_sql",
        "execute_validate",
        "ml_optional_parked",
    ),
}


def distill_block() -> dict[str, Any]:
    """Harness record of Platform D mapping. Not a coverage number."""
    return {
        "ideas_only": DISTILL["ideas_only"],
        "vendors_not_pasted": list(DISTILL["vendors_not_pasted"]),
        "ladder": list(DISTILL["ladder"]),
        "ontology_spine": dict(DISTILL["ontology_spine"]),
        "text2sql": dict(DISTILL["text2sql"]),
        "try_harder": DISTILL["try_harder"],
        "founder_lock": list(DISTILL["founder_lock"]),
        "baseline_ab": {
            "commit": BASELINE_AB_A9578348["commit"],
            "n": BASELINE_AB_A9578348["n"],
            "exact_coverage_answered_pct": BASELINE_AB_A9578348[
                "exact_coverage_answered_pct"
            ],
            "generative_coverage_answered_pct": BASELINE_AB_A9578348[
                "generative_coverage_answered_pct"
            ],
            "wrong": BASELINE_AB_A9578348["wrong"],
        },
    }


def load_pack(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    questions = list(data.get("questions") or [])
    spaces = dict(data.get("spaces") or {})
    if not questions:
        raise SystemExit(f"no questions in {path}")
    return {"questions": questions, "spaces": spaces}


def merge_pack_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union climb leftover L0s. Live --prove-path cannot score frozen 26
    or the n=31 / n=34 / n=37 / n=40 / n=43 / n=46 / n=49 packs
    (ontology_plan=21 / 24 / 27 / 30 / 33 / 36 / 39).

    Studio SHA does not include questions.yaml. Dual KEEP_HOLD 17/26 was
    this list staying at the frozen pack when the scoring checkout lagged.
    """
    by_id = {str(row.get("id") or ""): row for row in questions}
    out = list(questions)
    for case in CLIMB13_UNION_L0:
        qid = str(case["id"])
        if qid not in by_id:
            out.append(dict(case))
            by_id[qid] = case
    return out


def leftover_ids_not_ontology(
    cases: list[dict[str, Any]], ids: tuple[str, ...]
) -> list[str]:
    """Ids that were not answered as ontology_plan (asked+ABSTAIN still missing)."""
    by_id = {str(row.get("id") or ""): row for row in cases}
    missing: list[str] = []
    for qid in ids:
        row = by_id.get(qid)
        if row is None:
            missing.append(qid)
            continue
        if row.get("verdict") not in {"OK", "LAYER"}:
            missing.append(qid)
            continue
        if str(row.get("plan_source") or "") != "ontology_plan":
            missing.append(qid)
    return missing


def leftover_rise_not_ontology(cases: list[dict[str, Any]]) -> list[str]:
    """Climb-06 rise L0 ids that were not answered as ontology_plan.

    #214 `_leftover_l0_unscored` only checked the qid was asked. Asked+ABSTAIN
    still stamps ontology_plan=17. Climb-06 requires the 4 synonyms to be
    ontology_plan. cq_audit_overdue is optional (live last_audit_date).
    """
    return leftover_ids_not_ontology(cases, CLIMB06_RISE_IDS)


def load_oracles(path: Path = DEFAULT_ORACLES) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("oracles") or {}
    if not isinstance(rows, dict):
        raise SystemExit(f"oracles must be a mapping in {path}")
    return rows


def resolve_space(case: dict[str, Any], spaces: dict[str, Any]) -> str:
    alias = str(case.get("space") or "finance")
    if alias in spaces:
        return str(spaces[alias])
    return alias


def is_confident(env: dict[str, Any]) -> bool:
    badge = str(env.get("badge") or "")
    if env.get("abstained") or badge == "ABSTAIN":
        return False
    return badge in CONFIDENT


def is_cf1010(status: int, text: str | None) -> bool:
    """Cloudflare 1010 is a browser-signature ban, not a DMS grant 403."""
    if int(status) != 403:
        return False
    blob = (text or "").lower()
    return "error code: 1010" in blob or "error 1010" in blob


def cf1010_blocked_detail(status: int, text: str | None) -> str | None:
    if not is_cf1010(status, text):
        return None
    return (
        "CF1010 Cloudflare browser-signature ban (not IAP). "
        "Use httpx/curl-class fetch, not urllib. "
        "If httpx still 1010: Platform DevOps exception on studio.netie.ai."
    )


def score_http(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float,
) -> Any:
    """httpx/curl-class fetch. urllib CF1010s studio.netie.ai (SCORE-CLIENT-01)."""
    import httpx

    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
    if json_body is not None:
        kwargs["json"] = json_body
    return httpx.request(method, url, **kwargs)


def ask_error_envelope(exc: BaseException) -> dict[str, Any] | None:
    """403/409 grant/session refusal is ABSTAIN, not a scorer WRONG."""
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    text = getattr(resp, "text", None) if resp is not None else None
    if status is not None and is_cf1010(int(status), text):
        return None
    if status in GRANT_REFUSAL_STATUS:
        return {"badge": "ABSTAIN", "abstained": True, "rows": []}
    return None


def _response_status(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status is None:
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def no_envelope_verdict(exc: BaseException) -> tuple[str, str]:
    """Ask raised and there is no envelope. Never WRONG.

    HTTP 429 is RATE_LIMIT. Anything else is ABSTAIN(ask_error:<exception type>).
    """
    if _response_status(exc) == 429:
        return "RATE_LIMIT", "RATE_LIMIT"
    return "ABSTAIN", f"ask_error:{type(exc).__name__}"


def _mask_token(value: object) -> bool:
    from dms_core.pii import is_mask_token

    return is_mask_token(value)


def _typed_date_column(column: str) -> bool:
    from dms_core.pii import _typed_date_column as typed

    return typed(column)


def _sql_uses_star_or_union(sql: str) -> bool:
    """Projection star or UNION. COUNT(*) is not a projection star.

    ponytail: regex, not a parse. Ceiling: a star inside a string literal.
    Upgrade: the sqlglot projection walk in served_column_sources.
    """
    blob = " ".join(sql.split())
    if re.search(r"\bUNION\b", blob, re.IGNORECASE):
        return True
    stripped = re.sub(
        r"\bCOUNT\s*\(\s*(?:DISTINCT\s+)?\*\s*\)",
        "",
        blob,
        flags=re.IGNORECASE,
    )
    if re.search(r"\.\s*\*", stripped):
        return True
    return re.search(r"(?:^|[\s,(])\*(?:\s|$)", stripped) is not None


def _has_masked_typed_date(env: Mapping[str, Any]) -> bool:
    for row in envelope_rows(env):
        if not isinstance(row, dict):
            continue
        for key, val in row.items():
            if _mask_token(val) and _typed_date_column(str(key)):
                return True
    return False


def overmask_star_case(env: Mapping[str, Any]) -> bool:
    """INVALID breakdown: star or UNION SQL and a masked typed date. Not a verdict."""
    sql = str(env.get("sql_used") or "")
    return bool(sql) and _sql_uses_star_or_union(sql) and _has_masked_typed_date(env)


def _gold_columns(gold: list[Any]) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for row in gold:
        if not isinstance(row, dict):
            continue
        for key in row:
            name = str(key)
            if name not in seen:
                seen.add(name)
                cols.append(name)
    return cols


def _masked_compared_column(got: list[Any], cols: list[str]) -> str | None:
    """First served column that the oracle compare will read and that is masked."""
    gold = set(cols)
    for row in got:
        if isinstance(row, dict):
            for key, val in row.items():
                name = str(key)
                if name in gold and _mask_token(val):
                    return name
            continue
        cells = list(row) if isinstance(row, (list, tuple)) else [row]
        for i, val in enumerate(cells):
            if i >= len(cols) or not _mask_token(val):
                continue
            return cols[i]
    return None


def _drop_uncompared_masks(rows: list[Any], cols: list[str]) -> tuple[list[Any], bool]:
    """Drop masked cells the oracle does not compare. Compared cells stay."""
    gold = set(cols)
    changed = False
    out: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        new: dict[str, Any] = {}
        for key, val in row.items():
            if str(key) not in gold and _mask_token(val):
                changed = True
                continue
            new[key] = val
        out.append(new)
    return out, changed


def _mask_compare_gate(
    case: dict[str, Any],
    env: dict[str, Any],
    *,
    oracle_db: Path | str | None,
    oracles: Mapping[str, Any] | None,
    oracle_sql: str | None,
    as_of: str | None,
) -> tuple[dict[str, Any], JudgeResult | None]:
    """INVALID when a compared column is DMSMASK_. Else the normal judge.

    A masked column the oracle does not return is removed so the row compare
    judges the compared columns. No unmask, no second HTTP fetch.
    """
    expect = str(case.get("expect") or "l0").lower()
    if expect in REFUSE or oracle_db is None or expect != "l0" or not is_confident(env):
        return env, None
    sql = _lookup_oracle_sql(case, oracles, oracle_sql)
    if not sql:
        return env, None
    params = {"as_of": as_of} if as_of is not None and "$as_of" in sql else None
    gold, err = run_oracle_select(oracle_db, sql, params=params)
    if err is not None or not gold:
        return env, None
    cols = _gold_columns(gold)
    if not cols:
        return env, None
    got = envelope_rows(env)
    masked = _masked_compared_column(got, cols)
    if masked:
        return env, JudgeResult("INVALID", f"masked_compare:{masked}", _judge_badge(case, env))
    projected, changed = _drop_uncompared_masks(got, cols)
    if not changed:
        return env, None
    viewed = dict(env)
    viewed["rows"] = projected
    return viewed, None


@dataclass(frozen=True)
class JudgeResult:
    verdict: str
    reason: str
    scorer_ok_rows_not_compared: str


def _judge_badge(case: dict[str, Any], env: dict[str, Any]) -> str:
    """OK | ABSTAIN | LAYER | WRONG. Badge/min_rows only. No row compare."""
    expect = str(case.get("expect") or "l0").lower()
    badge = str(env.get("badge") or "")
    rows = env.get("rows") or env.get("values") or []
    n = len(rows) if isinstance(rows, list) else 0
    min_rows = int(case.get("min_rows") or 0)
    confident = is_confident(env)

    if expect in REFUSE:
        return "WRONG" if confident else "ABSTAIN"

    if not confident:
        return "ABSTAIN"
    if min_rows and n < min_rows:
        return "WRONG"
    if expect == "l0" and not badge.startswith("L0"):
        return "LAYER"
    return "OK"


def _lookup_oracle_sql(
    case: dict[str, Any],
    oracles: Mapping[str, Any] | None,
    oracle_sql: str | None,
) -> str:
    if oracle_sql is not None:
        return str(oracle_sql).strip()
    if oracles is None:
        return ""
    qid = str(case.get("id") or "")
    return str((oracles.get(qid) or {}).get("sql") or "").strip()


def judge_detailed(
    case: dict[str, Any],
    env: dict[str, Any],
    *,
    oracle_db: Path | str | None = None,
    oracles: Mapping[str, Any] | None = None,
    oracle_sql: str | None = None,
    as_of: str | None = None,
) -> JudgeResult:
    """Row-compared judge when oracle_db is set. ORACLE_ERROR never OK."""
    legacy = _judge_badge(case, env)
    expect = str(case.get("expect") or "l0").lower()
    if expect in REFUSE or oracle_db is None:
        return JudgeResult(legacy, "", legacy)

    sql = _lookup_oracle_sql(case, oracles, oracle_sql)
    if expect != "l0":
        return JudgeResult(legacy, "", legacy)
    if not sql:
        return JudgeResult("ORACLE_ERROR", "oracle_error:missing_sql", legacy)
    from dms_executor.demo_warehouse import sql_has_reserved_as_of

    params = (
        {"as_of": as_of}
        if as_of is not None and sql_has_reserved_as_of(sql)
        else None
    )
    gold, err = run_oracle_select(oracle_db, sql, params=params)
    if err is not None:
        return JudgeResult("ORACLE_ERROR", f"oracle_error:{err}", legacy)

    if not is_confident(env):
        return JudgeResult("ABSTAIN", "", legacy)

    got = envelope_rows(env)
    min_rows = int(case.get("min_rows") or 0)
    if min_rows and len(got) < min_rows:
        reason = rows_mismatch_reason(got, gold or [], sql=sql)
        return JudgeResult("WRONG", reason or "", legacy)

    reason = rows_mismatch_reason(got, gold or [], sql=sql)
    if reason:
        return JudgeResult("WRONG", reason, legacy)
    badge = str(env.get("badge") or "")
    if not badge.startswith("L0"):
        return JudgeResult("LAYER", "", legacy)
    return JudgeResult("OK", "", legacy)


def judge(
    case: dict[str, Any],
    env: dict[str, Any],
    *,
    oracle_db: Path | str | None = None,
    oracles: Mapping[str, Any] | None = None,
    oracle_sql: str | None = None,
    as_of: str | None = None,
) -> str:
    """OK | ABSTAIN | LAYER | WRONG | ORACLE_ERROR. WRONG/ORACLE_ERROR are P0."""
    return judge_detailed(
        case,
        env,
        oracle_db=oracle_db,
        oracles=oracles,
        oracle_sql=oracle_sql,
        as_of=as_of,
    ).verdict


def judge_envelope(
    case: dict[str, Any],
    env: dict[str, Any],
    *,
    oracle_db: Path | str | None = None,
    oracles: Mapping[str, Any] | None = None,
    oracle_sql: str | None = None,
    as_of: str | None = None,
) -> str:
    """Live judge. Silent demo fallback is WRONG (lying 200), not OK."""
    return judge_envelope_detailed(
        case,
        env,
        oracle_db=oracle_db,
        oracles=oracles,
        oracle_sql=oracle_sql,
        as_of=as_of,
    ).verdict


def judge_envelope_detailed(
    case: dict[str, Any],
    env: dict[str, Any],
    *,
    oracle_db: Path | str | None = None,
    oracles: Mapping[str, Any] | None = None,
    oracle_sql: str | None = None,
    as_of: str | None = None,
) -> JudgeResult:
    viewed, blocked = _mask_compare_gate(
        case,
        env,
        oracle_db=oracle_db,
        oracles=oracles,
        oracle_sql=oracle_sql,
        as_of=as_of,
    )
    if blocked is not None:
        return blocked
    inner = judge_detailed(
        case,
        viewed,
        oracle_db=oracle_db,
        oracles=oracles,
        oracle_sql=oracle_sql,
        as_of=as_of,
    )
    if env.get("demo_fallback_used"):
        return JudgeResult("WRONG", "demo_fallback_used", inner.scorer_ok_rows_not_compared)
    return inner


def pack_category_report(
    tallies: Mapping[str, int],
    *,
    figure_label: str,
) -> dict[str, Any]:
    """answered/abstained/WRONG/LAYER/ORACLE_ERROR/excluded-pending-scan out of 52."""
    ok = int(tallies.get("OK") or 0)
    layer = int(tallies.get("LAYER") or 0)
    abstain = int(tallies.get("ABSTAIN") or 0)
    wrong = int(tallies.get("WRONG") or 0)
    oracle_error = int(tallies.get("ORACLE_ERROR") or 0)
    invalid = int(tallies.get("INVALID") or 0)
    rate_limit = int(tallies.get("RATE_LIMIT") or 0)
    answered = ok + layer
    accounted = ok + layer + abstain + wrong + oracle_error + invalid + rate_limit
    denom = max(PACK_DENOMINATOR, accounted)
    excluded = denom - accounted

    def frac(n: int) -> str:
        return f"{n}/{denom}"

    return {
        "denominator": denom,
        "figure_label": figure_label,
        "answered": answered,
        "answered_of": frac(answered),
        "abstained": abstain,
        "abstained_of": frac(abstain),
        "wrong": wrong,
        "wrong_of": frac(wrong),
        "layer": layer,
        "layer_of": frac(layer),
        "oracle_error": oracle_error,
        "oracle_error_of": frac(oracle_error),
        "excluded_pending_scan": excluded,
        "excluded_pending_scan_of": frac(excluded),
    }


def print_category_report(cats: Mapping[str, Any]) -> None:
    print(
        f"answered {cats['answered_of']}  abstained {cats['abstained_of']}  "
        f"WRONG {cats['wrong_of']}  LAYER {cats['layer_of']}  "
        f"ORACLE_ERROR {cats['oracle_error_of']}  "
        f"excluded-pending-scan {cats['excluded_pending_scan_of']}"
    )
    print(str(cats["figure_label"]))


def require_oracle_db(path: Path | None) -> str | None:
    """None if usable; else a CONFIG reason. Live modes must not score without it."""
    if path is None:
        return (
            "CONFIG: --oracle-db PATH is required in live / --prove-path / "
            "--ab / --climb (DuckDB the answer ran on). Not a score."
        )
    if not path.is_file():
        return f"CONFIG: --oracle-db is not a file: {path}. Not a score."
    return None


def read_engine_clock_from_con(con: Any) -> tuple[str | None, str | None]:
    """CURRENT_DATE + TimeZone on this connection. Not datetime.now()."""
    try:
        row = con.execute(
            "SELECT CAST(CURRENT_DATE AS VARCHAR), current_setting('TimeZone')"
        ).fetchone()
    except Exception:  # noqa: BLE001 - clock must not fail the scorer
        return None, None
    if not row:
        return None, None
    as_of = str(row[0]).strip() if row[0] is not None else ""
    tz = str(row[1]).strip() if row[1] is not None else ""
    return (as_of or None, tz or None)


def round_date_label(before: str | None, after: str | None) -> str | None:
    """INVALID when the engine date is missing or crossed midnight. Never WRONG.

    The date is the answer engine CURRENT_DATE. A live round with no recorded
    engine date is INVALID and must not fall back to the oracle DuckDB clock.
    """
    if not before or not after or before != after:
        return "INVALID"
    return None


def stamp_round_clock(
    report: dict[str, Any],
    before: str | None,
    after: str | None,
    timezone: str | None,
) -> str | None:
    """Report metadata only. Does not change judge verdicts or WRONG tallies."""
    label = round_date_label(before, after)
    report["oracle_as_of"] = before
    report["oracle_as_of_after"] = after
    report["oracle_timezone"] = timezone
    if label == "INVALID":
        report["round_label"] = "INVALID"
        report["passed"] = False
    return label


def _round_clock(
    before: str | None,
    after: str | None,
    timezone: str | None,
) -> dict[str, Any]:
    return {
        "oracle_as_of": before,
        "oracle_as_of_after": after,
        "oracle_timezone": timezone,
        "round_label": round_date_label(before, after),
    }


def _invalid_round_exit(
    round_label: str | None, reason: str | None = None
) -> int | None:
    if round_label == "INVALID":
        if isinstance(reason, str) and reason.startswith("pin_"):
            print(f"INVALID: {reason}. Not WRONG.")
        else:
            print(
                "INVALID: no recorded answer-engine CURRENT_DATE, or the engine "
                "date crossed midnight during this round. Not WRONG."
            )
        return EXIT_FAIL
    return None


def _ab_engine_clock(path: Path) -> tuple[str | None, str | None]:
    """Same connect_file as A/B submit. Close before the next attach."""
    from dms_executor.demo_warehouse import connect_file

    con = connect_file(path)
    try:
        return read_engine_clock_from_con(con)
    finally:
        con.close()


def classify_path(route: Any) -> str:
    """Attribution on the product path. Not a second pack."""
    r = str(route or "")
    if r in GENERATIVE_ROUTES:
        return "generative"
    if r in EXACT_ROUTES:
        return "exact_match"
    return "other"


def classify_plan_source(env: dict[str, Any] | None) -> str:
    """ontology_plan | bind_plan | other from envelope telemetry only.

    Do not infer from SQL, question text, or assumption strings. A missing
    field is other so an old host cannot be guessed into Cortex AI coverage.
    """
    if not isinstance(env, dict):
        return "other"
    raw = str(env.get("plan_source") or "").strip().lower()
    return raw if raw in PLAN_SOURCES else "other"


def classify_crag(env: dict[str, Any]) -> str:
    """CRAG-style validate-or-abstain grade. Ideas-only; not a vendor clone.

    validated = gen execute after validate. abstain_validate = EXPLAIN/grant/hostile
    fail. abstain_gate = unsure/uncertified. skipped = exact-match or miss.
    """
    notes = " ".join(str(x) for x in (env.get("assumptions") or []))
    route = str(env.get("route") or "")
    abstained = bool(env.get("abstained") or env.get("badge") == "ABSTAIN")
    if route == "generated" and not abstained:
        return "validated"
    if abstained and "validate:" in notes:
        return "abstain_validate"
    if abstained and (
        "unsure" in notes
        or "uncertified" in notes
        or "too vague" in notes
        or "query_plan was not typed" in notes
    ):
        return "abstain_gate"
    if abstained:
        return "abstain"
    return "skipped"


def answered_vs_baseline(measured: int, baseline: int) -> str:
    if measured > baseline:
        return "rose"
    if measured < baseline:
        return "fell"
    return "flat"


def baseline_answered() -> int:
    return int(BASELINE_91C5CC99["ok"]) + int(BASELINE_91C5CC99["layer"])


def climb_url(url: str | None, env: dict[str, str] | None = None) -> str | None:
    """Fail closed: no laptop default. Platform sets --url or DMS_API_BASE."""
    raw = (url or "").strip()
    if raw:
        return raw.rstrip("/")
    bag = env if env is not None else os.environ
    for key in ("DMS_API_BASE", "STUDIO_API_BASE", "DMS_URL"):
        val = str(bag.get(key) or "").strip()
        if val:
            return val.rstrip("/")
    return None


def _ask(
    base: str,
    question: str,
    space_id: str,
    timeout: float,
    ask_path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"question": question, "space_id": space_id}
    if ask_path:
        payload["ask_path"] = ask_path
    resp = score_http(
        "POST",
        f"{base.rstrip('/')}/v1/chat/ask",
        json_body=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("ask response is not an object")
    return body


def self_check() -> int:
    pack = load_pack(DEFAULT_PACK)
    ids = [c["id"] for c in pack["questions"]]
    if len(ids) != len(set(ids)):
        print("FAIL: duplicate ids")
        return 1
    if len(ids) < 24:
        print(f"FAIL: Genie walkthrough needs >= 24 cases, got {len(ids)}")
        return 1
    missing_leftover = [qid for qid in CLIMB05_LEFTOVER_L0 if qid not in ids]
    if missing_leftover:
        print(
            "FAIL: climb-05 leftover L0 missing from pack "
            f"(17/26 freeze): {missing_leftover}"
        )
        return 1
    if len(ids) <= int(QUALIFIED_GEN_COVERAGE_CLAIM["n"]):
        print("FAIL: pack must outgrow frozen n=26 so leftover L0s are scored")
        return 1
    if len(ids) <= 31:
        print("FAIL: pack must outgrow n=31 so climb-07 rise L0s are scored")
        return 1
    if len(ids) <= 34:
        print("FAIL: pack must outgrow n=34 so climb-08 rise L0s are scored")
        return 1
    if len(ids) <= 37:
        print("FAIL: pack must outgrow n=37 so climb-09 rise L0s are scored")
        return 1
    if len(ids) <= 40:
        print("FAIL: pack must outgrow n=40 so climb-10 rise L0s are scored")
        return 1
    if len(ids) <= 43:
        print("FAIL: pack must outgrow n=43 so climb-11 rise L0s are scored")
        return 1
    if len(ids) <= 46:
        print("FAIL: pack must outgrow n=46 so climb-12 rise L0s are scored")
        return 1
    if len(ids) <= 49:
        print("FAIL: pack must outgrow n=49 so climb-13 rise L0s are scored")
        return 1
    if len(ids) < PACK_DENOMINATOR:
        print(
            f"FAIL: pack smaller than SCORE-ROWS-01 denominator "
            f"{PACK_DENOMINATOR}, got {len(ids)}"
        )
        return 1
    frozen26 = [
        {"id": f"frozen_{i}", "space": "finance", "expect": "abstain", "question": "x"}
        for i in range(int(QUALIFIED_GEN_COVERAGE_CLAIM["n"]))
    ]
    merged = merge_pack_questions(frozen26)
    merged_ids = {str(c["id"]) for c in merged}
    missing_climb07 = [qid for qid in CLIMB07_RISE_IDS if qid not in ids]
    if missing_climb07:
        print(
            "FAIL: climb-07 leftover L0 missing from pack "
            f"(21/31 freeze): {missing_climb07}"
        )
        return 1
    missing_climb08 = [qid for qid in CLIMB08_RISE_IDS if qid not in ids]
    if missing_climb08:
        print(
            "FAIL: climb-08 leftover L0 missing from pack "
            f"(24/34 freeze): {missing_climb08}"
        )
        return 1
    missing_climb09 = [qid for qid in CLIMB09_RISE_IDS if qid not in ids]
    if missing_climb09:
        print(
            "FAIL: climb-09 leftover L0 missing from pack "
            f"(27/37 freeze): {missing_climb09}"
        )
        return 1
    missing_climb10 = [qid for qid in CLIMB10_RISE_IDS if qid not in ids]
    if missing_climb10:
        print(
            "FAIL: climb-10 leftover L0 missing from pack "
            f"(30/40 freeze): {missing_climb10}"
        )
        return 1
    missing_climb11 = [qid for qid in CLIMB11_RISE_IDS if qid not in ids]
    if missing_climb11:
        print(
            "FAIL: climb-11 leftover L0 missing from pack "
            f"(33/43 freeze): {missing_climb11}"
        )
        return 1
    missing_climb12 = [qid for qid in CLIMB12_RISE_IDS if qid not in ids]
    if missing_climb12:
        print(
            "FAIL: climb-12 leftover L0 missing from pack "
            f"(36/46 freeze): {missing_climb12}"
        )
        return 1
    missing_climb13 = [qid for qid in CLIMB13_RISE_IDS if qid not in ids]
    if missing_climb13:
        print(
            "FAIL: climb-13 leftover L0 missing from pack "
            f"(39/49 freeze): {missing_climb13}"
        )
        return 1
    if any(
        qid not in merged_ids
        for qid in (
            *CLIMB06_RISE_IDS,
            *CLIMB07_RISE_IDS,
            *CLIMB08_RISE_IDS,
            *CLIMB09_RISE_IDS,
            *CLIMB10_RISE_IDS,
            *CLIMB11_RISE_IDS,
            *CLIMB12_RISE_IDS,
            *CLIMB13_RISE_IDS,
        )
    ):
        print("FAIL: merge_pack_questions must inject rise L0s into frozen 26")
        return 1
    if leftover_rise_not_ontology(
        [{"id": qid, "verdict": "ABSTAIN", "plan_source": "other"} for qid in CLIMB06_RISE_IDS]
    ) != list(CLIMB06_RISE_IDS):
        print("FAIL: abstained rise L0s must fail the climb-06 gate")
        return 1
    if leftover_rise_not_ontology(
        [
            {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
            for qid in CLIMB06_RISE_IDS
        ]
    ):
        print("FAIL: ontology_plan rise L0s must pass the climb-06 gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            for qid in CLIMB07_RISE_IDS
        ],
        CLIMB07_RISE_IDS,
    ) != list(CLIMB07_RISE_IDS):
        print("FAIL: abstained climb-07 rise L0s must fail the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
            for qid in CLIMB07_RISE_IDS
        ],
        CLIMB07_RISE_IDS,
    ):
        print("FAIL: ontology_plan climb-07 rise L0s must pass the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            for qid in CLIMB08_RISE_IDS
        ],
        CLIMB08_RISE_IDS,
    ) != list(CLIMB08_RISE_IDS):
        print("FAIL: abstained climb-08 rise L0s must fail the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
            for qid in CLIMB08_RISE_IDS
        ],
        CLIMB08_RISE_IDS,
    ):
        print("FAIL: ontology_plan climb-08 rise L0s must pass the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            for qid in CLIMB09_RISE_IDS
        ],
        CLIMB09_RISE_IDS,
    ) != list(CLIMB09_RISE_IDS):
        print("FAIL: abstained climb-09 rise L0s must fail the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
            for qid in CLIMB09_RISE_IDS
        ],
        CLIMB09_RISE_IDS,
    ):
        print("FAIL: ontology_plan climb-09 rise L0s must pass the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            for qid in CLIMB10_RISE_IDS
        ],
        CLIMB10_RISE_IDS,
    ) != list(CLIMB10_RISE_IDS):
        print("FAIL: abstained climb-10 rise L0s must fail the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
            for qid in CLIMB10_RISE_IDS
        ],
        CLIMB10_RISE_IDS,
    ):
        print("FAIL: ontology_plan climb-10 rise L0s must pass the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            for qid in CLIMB11_RISE_IDS
        ],
        CLIMB11_RISE_IDS,
    ) != list(CLIMB11_RISE_IDS):
        print("FAIL: abstained climb-11 rise L0s must fail the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
            for qid in CLIMB11_RISE_IDS
        ],
        CLIMB11_RISE_IDS,
    ):
        print("FAIL: ontology_plan climb-11 rise L0s must pass the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            for qid in CLIMB12_RISE_IDS
        ],
        CLIMB12_RISE_IDS,
    ) != list(CLIMB12_RISE_IDS):
        print("FAIL: abstained climb-12 rise L0s must fail the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
            for qid in CLIMB12_RISE_IDS
        ],
        CLIMB12_RISE_IDS,
    ):
        print("FAIL: ontology_plan climb-12 rise L0s must pass the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            for qid in CLIMB13_RISE_IDS
        ],
        CLIMB13_RISE_IDS,
    ) != list(CLIMB13_RISE_IDS):
        print("FAIL: abstained climb-13 rise L0s must fail the gate")
        return 1
    if leftover_ids_not_ontology(
        [
            {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
            for qid in CLIMB13_RISE_IDS
        ],
        CLIMB13_RISE_IDS,
    ):
        print("FAIL: ontology_plan climb-13 rise L0s must pass the gate")
        return 1
    expects = {str(c.get("expect") or "").lower() for c in pack["questions"]}
    if "l0" not in expects or not (expects & REFUSE):
        print("FAIL: pack must include l0 hits and abstain/refuse traps")
        return 1
    oracles = load_oracles()
    missing_sql = [
        str(c["id"])
        for c in pack["questions"]
        if str(c.get("expect") or "").lower() == "l0"
        and not str((oracles.get(c["id"]) or {}).get("sql") or "").strip()
    ]
    if missing_sql:
        print(f"FAIL: expect:l0 without Cortex SQL in oracles.yaml: {missing_sql}")
        return 1
    planted_ok = judge(
        {"expect": "l0", "min_rows": 1},
        {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"x": 1}]},
    )
    planted_wrong = judge(
        {"expect": "abstain"},
        {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"x": 1}]},
    )
    planted_refuse = judge(
        {"expect": "refuse"},
        {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"x": 1}]},
    )
    planted_abs = judge(
        {"expect": "l0"},
        {"badge": "ABSTAIN", "abstained": True, "rows": []},
    )
    if (
        planted_ok != "OK"
        or planted_wrong != "WRONG"
        or planted_refuse != "WRONG"
        or planted_abs != "ABSTAIN"
    ):
        print("FAIL: judge plant")
        return 1
    planted_demo = judge_envelope(
        {"expect": "l0", "min_rows": 1},
        {
            "badge": "L0_CERTIFIED",
            "abstained": False,
            "rows": [{"x": 1}],
            "demo_fallback_used": True,
        },
    )
    if planted_demo != "WRONG":
        print("FAIL: demo fallback must not score OK")
        return 1
    cats = pack_category_report(
        {"OK": 40, "LAYER": 0, "ABSTAIN": 9, "WRONG": 2, "ORACLE_ERROR": 1},
        figure_label=FIGURE_LABEL_FIXTURE,
    )
    if (
        cats["answered_of"] != "40/52"
        or cats["wrong_of"] != "2/52"
        or cats["oracle_error_of"] != "1/52"
        or cats["excluded_pending_scan_of"] != "0/52"
        or cats["figure_label"] != FIGURE_LABEL_FIXTURE
    ):
        print("FAIL: category report must be out of 52 and labelled CI fixtures")
        return 1
    short = pack_category_report(
        {"OK": 1, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0, "ORACLE_ERROR": 0},
        figure_label=FIGURE_LABEL_FIXTURE,
    )
    if short["excluded_pending_scan_of"] != "51/52":
        print("FAIL: unscored questions must stay excluded-pending-scan out of 52")
        return 1
    import tempfile

    import duckdb

    row_db = Path(tempfile.mkdtemp()) / "self_check_rows.duckdb"
    seed = duckdb.connect(str(row_db))
    try:
        seed.execute("CREATE TABLE meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
        seed.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        seed.execute("CREATE TABLE sales (month INTEGER, sku VARCHAR, amount DOUBLE)")
        seed.executemany(
            "INSERT INTO sales VALUES (?, ?, ?)",
            [(m, "A", 10.0) for m in range(1, 13)],
        )
    finally:
        seed.close()
    if read_schema_version(row_db) != "1":
        print("FAIL: schema_version must be recorded from meta")
        return 1
    monthly = "SELECT month, ROUND(SUM(amount), 2) AS total FROM sales GROUP BY month"
    l0 = {"id": "monthly", "expect": "l0", "min_rows": 1}
    green = {"badge": "L0_CERTIFIED", "abstained": False}
    three = judge_detailed(
        l0,
        {**green, "rows": [{"month": 1, "total": 10.0}] * 3},
        oracle_db=row_db,
        oracle_sql=monthly,
    )
    if three.verdict != "WRONG" or "rows_mismatch:count=3/12" not in three.reason:
        print("FAIL: ungrouped rows vs monthly oracle must be WRONG count")
        return 1
    if three.scorer_ok_rows_not_compared != "OK":
        print("FAIL: legacy judge must stay labelled scorer_ok_rows_not_compared")
        return 1
    missing = judge_detailed(
        l0,
        {**green, "rows": [{"month": 1, "total": 10.0}]},
        oracle_db=row_db,
        oracle_sql="SELECT no_such_column FROM sales",
    )
    if missing.verdict != "ORACLE_ERROR" or missing.verdict == "OK":
        print("FAIL: missing-column oracle must be ORACLE_ERROR, never OK")
        return 1
    trap = judge_detailed(
        {"expect": "refuse"},
        {**green, "rows": [{"v": 1}]},
        oracle_db=row_db,
        oracle_sql="SELECT 1",
    )
    if trap.verdict != "WRONG":
        print("FAIL: confident refuse must stay WRONG when oracle_db is set")
        return 1
    b = BASELINE_91C5CC99
    if (
        int(b["ok"]) + int(b["layer"]) + int(b["abstain"]) + int(b["wrong"]) != int(b["n"])
        or int(b["wrong"]) != 0
        or len(ids) < int(b["n"])
    ):
        print("FAIL: baseline @ 91c5cc99 does not match pack / WRONG=0")
        return 1
    if climb_url(None, {}) is not None:
        print("FAIL: climb url must not default")
        return 1
    if climb_url(None, {"DMS_API_BASE": PLATFORM_API}) != PLATFORM_API:
        print("FAIL: climb url from DMS_API_BASE")
        return 1
    if classify_health(403, None, "text/html")[0] != "blocked":
        print("FAIL: IAP 403 must be BLOCKED, not a score")
        return 1
    if not is_cf1010(403, "error code: 1010"):
        print("FAIL: CF1010 plant")
        return 1
    if is_cf1010(403, "Cloudflare Access login"):
        print("FAIL: IAP HTML is not CF1010")
        return 1
    ab = BASELINE_AB_A9578348
    if (
        int(ab["exact_answered"]) + int(ab["generative_answered"]) < 1
        or int(ab["wrong"]) != 0
        or len(ids) < int(ab["n"])
        or int(ab["exact_answered"]) != 10
        or int(ab["generative_answered"]) != 1
        or float(ab["exact_coverage_answered_pct"]) != 38.46
        or float(ab["generative_coverage_answered_pct"]) != 3.85
        or round(100.0 * int(ab["exact_answered"]) / int(ab["n"]), 2) != 38.46
        or round(100.0 * int(ab["generative_answered"]) / int(ab["n"]), 2) != 3.85
    ):
        print("FAIL: A/B baseline @ a9578348 drifted")
        return 1
    if (
        DISTILL["ideas_only"] is not True
        or DISTILL["text2sql"]["vendor_sdk"] is not False
        or DISTILL["ontology_spine"]["yaml_pack_format"] is not False
        or DISTILL["ladder"][0] != "certified_first_then_generative"
        or list(DISTILL["founder_lock"])[:4]
        != [
            "semantic_retrieve",
            "ontology_relations",
            "generate_sql",
            "execute_validate",
        ]
    ):
        print("FAIL: distill constraints drifted")
        return 1
    spine = ROOT / str(DISTILL["ontology_spine"]["retrieve_yaml"])
    if not spine.is_file():
        print("FAIL: ontology spine yaml missing")
        return 1
    if classify_crag(
        {
            "route": "generated",
            "abstained": False,
            "badge": "L2_VALIDATED",
            "assumptions": ["executed via Cortex submit after validate"],
        }
    ) != "validated":
        print("FAIL: CRAG validated plant")
        return 1
    if classify_crag(
        {
            "route": "generated",
            "abstained": True,
            "badge": "ABSTAIN",
            "assumptions": ["GEN-01: validate:explain"],
        }
    ) != "abstain_validate":
        print("FAIL: CRAG validate-or-abstain plant")
        return 1
    fake_t = {"OK": 8, "LAYER": 10, "ABSTAIN": 8, "WRONG": 0}
    fake_cases = [
        {
            "id": "cq_x",
            "verdict": "LAYER",
            "route": "generated",
            "path": "generative",
            "expect": "l0",
        }
    ]
    report = build_climb_report(fake_t, cases=fake_cases, url=PLATFORM_API)
    blob = json.dumps(report)
    if "99.95" in blob or "COMPLETE" in blob:
        print("FAIL: climb report invented COMPLETE / 99.95")
        return 1
    if report["answered_vs_baseline"] != "rose" or report["passed_wrong_zero"] is not True:
        print("FAIL: climb delta plant")
        return 1
    if report.get("claim") not in (None, "measured"):
        print("FAIL: climb claim must stay measured")
        return 1
    if classify_plan_source(
        {
            "assumptions": ["compute_fallback:bind_plan"],
            "route": "generated",
            "sql_used": "SELECT 1",
        }
    ) != "other":
        print("FAIL: plan_source must not be guessed from assumptions")
        return 1
    if classify_plan_source({"plan_source": "ontology_plan"}) != "ontology_plan":
        print("FAIL: plan_source ontology_plan plant")
        return 1
    if classify_plan_source({"plan_source": "bind_plan"}) != "bind_plan":
        print("FAIL: plan_source bind_plan plant")
        return 1
    insights_plant = build_gen_path_prove_report(
        {"OK": 1, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0},
        cases=[{"id": "cq_sku_count", "verdict": "OK", "plan_source": "ontology_plan"}],
        mode="offline",
    )
    if int(insights_plant["by_plan_source"]["ontology_plan"]["answered"]) < 1:
        print("FAIL: prove must count ontology_plan>=1 when Insights path works")
        return 1
    if "COMPLETE" in json.dumps(insights_plant):
        print("FAIL: insights plant invented COMPLETE")
        return 1
    climb_plant = build_gen_path_prove_report(
        {"OK": 2, "LAYER": 0, "ABSTAIN": 24, "WRONG": 0},
        cases=(
            [
                {"id": "cq_sku_count", "verdict": "OK", "plan_source": "ontology_plan"},
                {
                    "id": "cq_stock_value_by_category",
                    "verdict": "OK",
                    "plan_source": "ontology_plan",
                },
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(24)
            ]
        ),
        mode="offline",
    )
    if int(climb_plant["by_plan_source"]["ontology_plan"]["answered"]) < 2:
        print("FAIL: climb plant must count ontology_plan>1")
        return 1
    climb04_plant = build_gen_path_prove_report(
        {"OK": 18, "LAYER": 0, "ABSTAIN": 9, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(18)
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(9)
            ]
        ),
        mode="offline",
    )
    if int(climb04_plant["by_plan_source"]["ontology_plan"]["answered"]) <= 17:
        print("FAIL: climb-04 plant must count ontology_plan>17")
        return 1
    if "COMPLETE" in json.dumps(climb04_plant) or "99.95" in json.dumps(climb04_plant):
        print("FAIL: climb-04 plant invented COMPLETE / 99.95")
        return 1
    climb05_plant = build_gen_path_prove_report(
        {"OK": 20, "LAYER": 0, "ABSTAIN": 11, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(20)
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(11)
            ]
        ),
        mode="offline",
    )
    if int(climb05_plant["by_plan_source"]["ontology_plan"]["answered"]) <= 17:
        print("FAIL: climb-05 plant must count ontology_plan>17")
        return 1
    if "COMPLETE" in json.dumps(climb05_plant) or "99.95" in json.dumps(climb05_plant):
        print("FAIL: climb-05 plant invented COMPLETE / 99.95")
        return 1
    hold17 = build_gen_path_prove_report(
        {"OK": 17, "LAYER": 0, "ABSTAIN": 14, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
                for qid in CLIMB06_RISE_IDS
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(hold17) is None:
        print("FAIL: live climb-06 must fail flat ontology_plan=17")
        return 1
    rise21 = build_gen_path_prove_report(
        {"OK": 21, "LAYER": 0, "ABSTAIN": 10, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
                for qid in CLIMB06_RISE_IDS
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(rise21) is None:
        print("FAIL: live climb-07 must fail flat ontology_plan=21")
        return 1
    rise24 = build_gen_path_prove_report(
        {"OK": 24, "LAYER": 0, "ABSTAIN": 10, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
                for qid in (*CLIMB06_RISE_IDS, *CLIMB07_RISE_IDS)
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(rise24) is None:
        print("FAIL: live climb-08 must fail flat ontology_plan=24")
        return 1
    rise27 = build_gen_path_prove_report(
        {"OK": 27, "LAYER": 0, "ABSTAIN": 10, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
                for qid in (*CLIMB06_RISE_IDS, *CLIMB07_RISE_IDS, *CLIMB08_RISE_IDS)
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(rise27) is None:
        print("FAIL: live climb-09 must fail flat ontology_plan=27")
        return 1
    rise30 = build_gen_path_prove_report(
        {"OK": 30, "LAYER": 0, "ABSTAIN": 10, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
                for qid in (
                    *CLIMB06_RISE_IDS,
                    *CLIMB07_RISE_IDS,
                    *CLIMB08_RISE_IDS,
                    *CLIMB09_RISE_IDS,
                )
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(rise30) is None:
        print("FAIL: live climb-10 must fail flat ontology_plan=30")
        return 1
    rise33 = build_gen_path_prove_report(
        {"OK": 33, "LAYER": 0, "ABSTAIN": 10, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
                for qid in (
                    *CLIMB06_RISE_IDS,
                    *CLIMB07_RISE_IDS,
                    *CLIMB08_RISE_IDS,
                    *CLIMB09_RISE_IDS,
                    *CLIMB10_RISE_IDS,
                )
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(rise33) is None:
        print("FAIL: live climb-11 must fail flat ontology_plan=33")
        return 1
    rise36 = build_gen_path_prove_report(
        {"OK": 36, "LAYER": 0, "ABSTAIN": 10, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
                for qid in (
                    *CLIMB06_RISE_IDS,
                    *CLIMB07_RISE_IDS,
                    *CLIMB08_RISE_IDS,
                    *CLIMB09_RISE_IDS,
                    *CLIMB10_RISE_IDS,
                    *CLIMB11_RISE_IDS,
                )
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(rise36) is None:
        print("FAIL: live climb-12 must fail flat ontology_plan=36")
        return 1
    rise39 = build_gen_path_prove_report(
        {"OK": 39, "LAYER": 0, "ABSTAIN": 10, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
                for qid in (
                    *CLIMB06_RISE_IDS,
                    *CLIMB07_RISE_IDS,
                    *CLIMB08_RISE_IDS,
                    *CLIMB09_RISE_IDS,
                    *CLIMB10_RISE_IDS,
                    *CLIMB11_RISE_IDS,
                    *CLIMB12_RISE_IDS,
                )
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(rise39) is None:
        print("FAIL: live climb-13 must fail flat ontology_plan=39")
        return 1
    rise42 = build_gen_path_prove_report(
        {"OK": 42, "LAYER": 0, "ABSTAIN": 10, "WRONG": 0},
        cases=(
            [
                {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
                for i in range(17)
            ]
            + [
                {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
                for qid in (
                    *CLIMB06_RISE_IDS,
                    *CLIMB07_RISE_IDS,
                    *CLIMB08_RISE_IDS,
                    *CLIMB09_RISE_IDS,
                    *CLIMB10_RISE_IDS,
                    *CLIMB11_RISE_IDS,
                    *CLIMB12_RISE_IDS,
                    *CLIMB13_RISE_IDS,
                )
            ]
            + [
                {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
                for i in range(10)
            ]
        ),
        mode="live",
    )
    if live_climb_gate(rise42) is not None:
        print("FAIL: live climb-13 must pass ontology_plan>39 with rise L0s")
        return 1
    if "COMPLETE" in json.dumps(climb_plant) or "99.95" in json.dumps(climb_plant):
        print("FAIL: climb plant invented COMPLETE / 99.95")
        return 1
    yes_cases = (
        [{"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"} for i in range(14)]
        + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(12)]
    )
    yes_t = {"OK": 14, "LAYER": 0, "ABSTAIN": 12, "WRONG": 0}
    yes = build_gen_path_prove_report(
        tallies=yes_t, cases=yes_cases, mode="offline"
    )
    yes_blob = json.dumps(yes)
    if "COMPLETE" in yes_blob or "99.95" in yes_blob or "DB-GPT-class" in yes_blob:
        print("FAIL: prove report invented COMPLETE / 99.95")
        return 1
    if yes[HOLD_MAY_CLEAR_FIELD] != "YES" or yes["phase_a_hold_may_clear"] != "YES":
        print("FAIL: majority ontology_plan WRONG=0 should be YES")
        return 1
    no_bind = build_gen_path_prove_report(
        tallies={"OK": 15, "LAYER": 0, "ABSTAIN": 11, "WRONG": 0},
        cases=(
            [{"id": f"q{i}", "verdict": "OK", "plan_source": "bind_plan"} for i in range(15)]
            + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(11)]
        ),
        mode="offline",
    )
    if no_bind[HOLD_MAY_CLEAR_FIELD] != "NO":
        print("FAIL: bind_plan majority must be NO")
        return 1
    no_wrong = build_gen_path_prove_report(
        tallies={"OK": 14, "LAYER": 0, "ABSTAIN": 11, "WRONG": 1},
        cases=(
            [{"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"} for i in range(14)]
            + [{"id": "w", "verdict": "WRONG", "plan_source": "ontology_plan"}]
            + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(11)]
        ),
        mode="offline",
    )
    if no_wrong[HOLD_MAY_CLEAR_FIELD] != "NO":
        print("FAIL: WRONG>0 must be NO even if ontology_plan majority")
        return 1
    qclaim = QUALIFIED_GEN_COVERAGE_CLAIM
    if (
        qclaim["status"] != "QUALIFIED"
        or int(qclaim["n"]) > len(ids)
        or int(qclaim["offline_ab_answered"]) != 15
        or float(qclaim["offline_ab_answered_pct"]) != 57.69
        or round(100.0 * 15 / 26, 2) != 57.69
    ):
        print("FAIL: QUALIFIED 15/26 pack identity drifted")
        return 1
    if round_date_label("2026-09-24", "2026-09-25") != "INVALID":
        print("FAIL: mismatched engine dates must be INVALID")
        return 1
    if round_date_label("2026-09-24", "2026-09-25") == "WRONG":
        print("FAIL: midnight round must not be labelled WRONG")
        return 1
    if round_date_label("2026-09-25", "2026-09-25") is not None:
        print("FAIL: matching engine dates must not be INVALID")
        return 1
    if round_date_label(None, None) != "INVALID":
        print("FAIL: missing engine date must be INVALID")
        return 1
    print(f"PASS: curated pack {len(ids)} cases, judge fail-closed on green trap")
    return 0


def _clock_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def resolve_live_engine_clock(url: str, timeout: float) -> dict[str, str | None]:
    """Engine date for opening a live round. Never the oracle file or the box clock.

    Prefer the clock published by the connection that ran answer SQL. Else the
    /health point-read. Else all nulls (the round is unread).
    """
    published: dict[str, str] | None = None
    try:
        from dms_executor.demo_warehouse import current_engine_clock

        published = current_engine_clock()
    except Exception:  # noqa: BLE001 - missing warehouse is an unread round
        published = None
    if published and published.get("engine_as_of") and published.get("engine_as_of_after"):
        return {
            "engine_as_of": published.get("engine_as_of"),
            "engine_as_of_after": published.get("engine_as_of_after"),
            "engine_timezone": published.get("engine_timezone"),
        }
    body: dict[str, Any] = {}
    try:
        resp = score_http("GET", f"{url.rstrip('/')}/health", timeout=timeout)
        resp.raise_for_status()
        parsed = resp.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:  # noqa: BLE001 - unread, do not invent a date
        body = {}
    return {
        "engine_as_of": _clock_text(body.get("engine_as_of")),
        "engine_as_of_after": _clock_text(body.get("engine_as_of_after")),
        "engine_timezone": _clock_text(body.get("engine_timezone")),
    }


def _planned_n() -> int:
    """Cases the pack will ask. Stays 52 when a preflight round scores n=0."""
    pack = load_pack(DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def score_live_entry(
    url: str,
    timeout: float,
    ask_path: str | None = None,
    *,
    oracle_db: Path | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]], dict[str, Any]]:
    """Every live entry point. Passes the engine date. Does not call the judge."""
    clock = resolve_live_engine_clock(url, timeout)
    return score_pack_live(
        url,
        timeout,
        ask_path,
        oracle_db=oracle_db,
        engine_as_of=clock.get("engine_as_of"),
        engine_as_of_after=clock.get("engine_as_of_after"),
        engine_timezone=clock.get("engine_timezone"),
    )


def _case_invalid_reason(
    before: str | None,
    after: str | None,
    tz: str | None,
    tz_after: str | None,
) -> str | None:
    if not before or not after or before != after:
        return "engine_date_mismatch"
    if not tz or not tz_after:
        return "engine_timezone_unread"
    if tz != tz_after:
        return "engine_timezone_mismatch"
    return None


def _own_engine_date(env: Mapping[str, Any] | None) -> str | None:
    """Clock carried by this answer. The round date is not a substitute."""
    if not env:
        return None
    return _clock_text(env.get("engine_as_of"))


def _answer_clock(
    env: Mapping[str, Any] | None,
    round_before: str | None,
    round_tz: str | None,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """case when this answer carried its own connection clock. Else round_health.

    The open /health body's engine_as_of_after is not this case's end date.
    """
    if env:
        before = _clock_text(env.get("engine_as_of"))
        after = _clock_text(env.get("engine_as_of_after"))
        if before or after:
            return (
                "case",
                before,
                after,
                _clock_text(env.get("engine_timezone")),
                _clock_text(env.get("engine_timezone_after")),
            )
    return ("round_health", round_before, round_before, round_tz, round_tz)


def read_round_end_health(url: str, timeout: float) -> dict[str, Any]:
    """A new GET /health after the last case. Not the opener's engine_as_of_after."""
    try:
        resp = score_http("GET", f"{url.rstrip('/')}/health", timeout=timeout)
        resp.raise_for_status()
        parsed = resp.json()
    except Exception:  # noqa: BLE001 - a failed end read is round_end_unread
        return {"ok": False, "engine_as_of": None}
    if not isinstance(parsed, dict):
        return {"ok": False, "engine_as_of": None}
    as_of = _clock_text(parsed.get("engine_as_of"))
    if not as_of:
        return {"ok": False, "engine_as_of": None}
    return {
        "ok": True,
        "engine_as_of": as_of,
        "engine_timezone": _clock_text(parsed.get("engine_timezone")),
    }


_CLOCK_KEYS = (
    "engine_as_of",
    "engine_as_of_after",
    "engine_timezone",
    "engine_timezone_after",
)


def _iso_day(value: str | None) -> date | None:
    if not value or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _preserved_case_clock(env: Mapping[str, Any] | None) -> bool:
    """Four-field shape. Extra check on a recorded read. Never a keep by itself."""
    if not env:
        return False
    before = _clock_text(env.get("engine_as_of"))
    after = _clock_text(env.get("engine_as_of_after"))
    tz = _clock_text(env.get("engine_timezone"))
    tz_after = _clock_text(env.get("engine_timezone_after"))
    if not before or not after or not tz or not tz_after or tz != tz_after:
        return False
    start = _iso_day(before)
    end = _iso_day(after)
    if start is None or end is None:
        return False
    return end == start or end == start + timedelta(days=1)


def _retag_case(
    tallies: dict[str, int],
    case: dict[str, Any],
    rec: dict[str, Any],
    reason: str,
) -> None:
    old = str(case.get("verdict") or "")
    if old != "INVALID":
        if old in tallies:
            tallies[old] -= 1
        tallies["INVALID"] += 1
    case["verdict"] = "INVALID"
    case["reason"] = reason
    case[LEGACY_JUDGE_LABEL] = "INVALID"
    rec["outcome"] = "INVALID"
    rec["reason"] = reason
    rec["oracle_verdict"] = "INVALID"


def _clock_equals_read(env: Mapping[str, Any], read: Mapping[str, Any]) -> bool:
    """True when the four top-level clock fields equal this case's connection read."""
    for key in _CLOCK_KEYS:
        if _clock_text(env.get(key)) != _clock_text(read.get(key)):
            return False
    return True


def _clock_equals_health_read(
    env: Mapping[str, Any], day: str | None, tz: str | None
) -> bool:
    """Both dates and both zones equal one /health read. A span is not this."""
    if not day or not tz:
        return False
    return (
        _clock_text(env.get("engine_as_of")) == day
        and _clock_text(env.get("engine_as_of_after")) == day
        and _clock_text(env.get("engine_timezone")) == tz
        and _clock_text(env.get("engine_timezone_after")) == tz
    )


def _health_clock_match(
    env: Mapping[str, Any],
    *,
    start: str | None,
    end: str | None,
    start_tz: str | None,
    end_tz: str | None,
) -> bool:
    """True when every top-level clock field equals this round's /health reads."""
    if not start or not end:
        return False
    pairs = (
        ("engine_as_of", start),
        ("engine_as_of_after", end),
        ("engine_timezone", start_tz),
        ("engine_timezone_after", end_tz or start_tz),
    )
    for key, want in pairs:
        got = _clock_text(env.get(key))
        if got != want:
            return False
    return True


def _apply_clock_keep(
    tallies: dict[str, int],
    cases_out: list[dict[str, Any]],
    records: list[dict[str, Any]],
    reads: list[dict[str, str] | str | None],
    *,
    start: str | None,
    end: str | None,
    start_tz: str | None,
    end_tz: str | None,
    end_ok: bool,
) -> bool:
    """Mask a top-level clock the scorer cannot tie to a read.

    A recorded connection read keeps the clock only when the four fields
    equal that read. An empty log keeps it only when both dates and both
    zones equal this round's /health start or end. The other keep is a
    /health next-day end (``round_spans_midnight``). Anything else is
    masked. An existing ``engine_timezone_*`` or ``round_spans_midnight``
    reason is kept, and the round still records ``engine_clock_masked``.
    A mask token is not a date match.
    """
    from dms_core.pii import mask_unkept_clock_fields

    if end_ok:
        allowed: dict[str, str | None] = {
            "engine_as_of": start,
            "engine_as_of_after": end,
            "engine_timezone": start_tz,
            "engine_timezone_after": end_tz or start_tz,
        }
    else:
        allowed = {key: None for key in _CLOCK_KEYS}
    any_masked = False
    for case, rec, read in zip(cases_out, records, reads, strict=True):
        env = rec.get("envelope")
        if not isinstance(env, dict):
            continue
        if (
            end_ok
            and end
            and start
            and end != start
            and _health_clock_match(
                env, start=start, end=end, start_tz=start_tz, end_tz=end_tz
            )
        ):
            _retag_case(tallies, case, rec, "round_spans_midnight")
            continue
        if (
            isinstance(read, dict)
            and _clock_equals_read(env, read)
            and _preserved_case_clock(env)
        ):
            continue
        # Empty log: this process never saw _publish_engine_clock. /health only.
        if read is None and end_ok and (
            _clock_equals_health_read(env, start, start_tz)
            or _clock_equals_health_read(env, end, end_tz or start_tz)
        ):
            continue
        if not any(key in env for key in _CLOCK_KEYS):
            continue
        masked_env, did = mask_unkept_clock_fields(env, allowed)
        if not did:
            continue
        any_masked = True
        raw_day = _clock_text(env.get("engine_as_of"))
        rec["envelope"] = masked_env
        if raw_day and rec.get("engine_date") == raw_day:
            rec["engine_date"] = masked_env.get("engine_as_of")
        if raw_day and case.get("oracle_as_of") == raw_day:
            case["oracle_as_of"] = masked_env.get("engine_as_of")
        if case.get("reason") in (
            "engine_timezone_unread",
            "engine_timezone_mismatch",
            "round_spans_midnight",
        ):
            continue
        _retag_case(tallies, case, rec, "engine_clock_masked")
    return any_masked


def _mark_round_health_midnight(
    tallies: dict[str, int],
    cases_out: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> None:
    """Every round_health case becomes INVALID round_spans_midnight and stays in n."""
    for case, rec in zip(cases_out, records, strict=True):
        if rec.get("clock_source") != "round_health":
            continue
        if case.get("verdict") == "INVALID" and case.get("reason") == "round_spans_midnight":
            continue
        old = str(case.get("verdict") or "")
        if old != "INVALID":
            if old in tallies:
                tallies[old] -= 1
            tallies["INVALID"] += 1
        case["verdict"] = "INVALID"
        case["reason"] = "round_spans_midnight"
        case[LEGACY_JUDGE_LABEL] = "INVALID"
        rec["outcome"] = "INVALID"
        rec["reason"] = "round_spans_midnight"
        rec["oracle_verdict"] = "INVALID"


def _unread_clock(
    before: str | None,
    after: str | None,
    timezone: str | None,
) -> dict[str, Any]:
    clock = _round_clock(before, after, timezone)
    clock["reason"] = "engine_date_unread"
    clock["invalid"] = 0
    clock["n"] = 0
    clock["n_without_invalid"] = 0
    return clock


def _invalid_case_exit(tallies: dict[str, int]) -> int | None:
    n = int(tallies.get("INVALID") or 0)
    if n:
        print(
            f"FAIL: INVALID={n} (engine date, timezone, or model pin on a case). "
            "Not WRONG. Not PASS."
        )
        return EXIT_FAIL
    return None


def _pin_case(
    case: dict[str, Any],
    *,
    verdict: str,
    reason: str,
    vault_reason: str,
    schema_ver: str | None,
    as_of: str | None,
    tz: str | None,
    shot: Any = None,
) -> dict[str, Any]:
    """One pinned case. ABSTAIN or INVALID. Rows stay empty. Not WRONG.

    Body and header served ids are both kept. A disagreement does not pick one.
    """
    row = {
        "id": str(case["id"]),
        "verdict": verdict,
        "badge": "ABSTAIN" if verdict == "ABSTAIN" else None,
        "route": None,
        "path": "other",
        "plan_source": "other",
        "crag": "abstain" if verdict == "ABSTAIN" else "skipped",
        "rows": 0,
        "expect": case.get("expect"),
        "reason": reason,
        "pin_reason": vault_reason,
        LEGACY_JUDGE_LABEL: verdict,
        "oracle_schema_version": schema_ver,
        "oracle_as_of": as_of,
        "oracle_timezone": tz,
        "served_provider_body": getattr(shot, "body_provider", None),
        "served_model_body": getattr(shot, "body_model", None),
        "served_provider_header": getattr(shot, "header_provider", None),
        "served_model_header": getattr(shot, "header_model", None),
    }
    return row


def score_pack_live(
    url: str,
    timeout: float,
    ask_path: str | None = None,
    *,
    oracle_db: Path | None = None,
    engine_as_of: str | None = None,
    engine_as_of_after: str | None = None,
    engine_timezone: str | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]], dict[str, Any]]:
    """Live HTTP pack. Bind date is the answer-engine CURRENT_DATE only.

    Never falls back to --oracle-db CURRENT_DATE. No recorded engine date:
    round INVALID, not judged.
    """
    # Missing engine date is an unread round: n=0, no asks. The end date is a
    # second /health read after the cases, not engine_as_of_after on this open.
    planned = _planned_n()
    if not engine_as_of:
        clock = _unread_clock(engine_as_of, engine_as_of_after, engine_timezone)
        clock["n_planned"] = planned
        return _tally(), [], clock
    from cortex_client.strict_pin import envelope_mismatch, next_answer, open_round
    from dms_executor.demo_warehouse import (
        case_connection_read,
        clear_connection_log,
        connection_log_len,
    )

    pin_round = open_round()
    if pin_round.blocked:
        clock = _round_clock(engine_as_of, engine_as_of_after, engine_timezone)
        clock["reason"] = pin_round.reason
        clock["pin_reason"] = pin_round.vault_reason
        clock["round_label"] = "INVALID"
        clock["passed"] = False
        clock["invalid"] = 0
        clock["n"] = 0
        clock["n_planned"] = planned
        clock["n_without_invalid"] = 0
        clock["case_records"] = []
        if str(pin_round.reason or "").startswith("pin_unavailable:"):
            clock["pin_preflight_unavailable"] = True
        return _tally(), [], clock
    pack = load_pack(DEFAULT_PACK)
    pack["questions"] = merge_pack_questions(list(pack["questions"]))
    oracles = load_oracles() if oracle_db is not None else None
    schema_ver = read_schema_version(oracle_db) if oracle_db is not None else None
    as_of = engine_as_of
    oracle_tz = engine_timezone
    clear_connection_log()
    tallies = _tally()
    cases_out: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    reads: list[dict[str, str] | str | None] = []
    overmask = 0
    read_at = 0

    def _push(rec: dict[str, Any]) -> None:
        records.append(rec)
        reads.append(case_connection_read(read_at))

    for case in pack["questions"]:
        read_at = connection_log_len()
        qid = str(case["id"])
        space = resolve_space(case, pack["spaces"])
        if pin_round.active:
            shot = next_answer()
            if shot.kind != "ok":
                if shot.kind == "unavailable":
                    tallies["ABSTAIN"] += 1
                    verdict = "ABSTAIN"
                else:
                    tallies["INVALID"] += 1
                    verdict = "INVALID"
                print(
                    f"{qid}\t{verdict}\tpinned\treason={shot.name}"
                    f"\tpin_reason={shot.vault_reason}"
                )
                cases_out.append(
                    _pin_case(
                        case,
                        verdict=verdict,
                        reason=shot.name,
                        vault_reason=shot.vault_reason,
                        schema_ver=schema_ver,
                        as_of=as_of,
                        tz=oracle_tz,
                        shot=shot,
                    )
                )
                rec = _case_record(
                    qid, verdict, shot.name, None, verdict, None, "round_health"
                )
                # Neither side is the record's served model. Both pairs are stored.
                rec["served_provider_body"] = shot.body_provider
                rec["served_model_body"] = shot.body_model
                rec["served_provider_header"] = shot.header_provider
                rec["served_model_header"] = shot.header_model
                _push(rec)
                continue
        try:
            env = _ask(url, str(case["question"]), space, timeout, ask_path=ask_path)
        except Exception as exc:  # noqa: BLE001
            env = ask_error_envelope(exc)
            if env is None:
                # Live WRONG path changed: no envelope is never WRONG.
                verdict, reason = no_envelope_verdict(exc)
                err = f"{type(exc).__name__}: {exc}"
                print(f"{qid}\t{verdict}\t{reason}\t{err}")
                tallies[verdict] += 1
                cases_out.append(
                    {
                        "id": qid,
                        "verdict": verdict,
                        "badge": None,
                        "route": None,
                        "path": "other",
                        "crag": "skipped",
                        "plan_source": "other",
                        "rows": 0,
                        "expect": case.get("expect"),
                        "error": err,
                        "reason": reason,
                        LEGACY_JUDGE_LABEL: "",
                    }
                )
                _push(
                    _case_record(qid, verdict, reason, None, verdict, None, "round_health")
                )
                continue
            print(f"{qid}\tGRANT_REFUSE\t{type(exc).__name__}: {exc}")
        source, case_before, case_after, case_tz, case_tz_after = _answer_clock(
            env, as_of, oracle_tz
        )
        if pin_round.active:
            pin_why = envelope_mismatch(env)
            if pin_why:
                tallies["INVALID"] += 1
                print(f"{qid}\tINVALID\tpinned\treason={pin_why}")
                cases_out.append(
                    _pin_case(
                        case,
                        verdict="INVALID",
                        reason=pin_why,
                        vault_reason="",
                        schema_ver=schema_ver,
                        as_of=as_of,
                        tz=oracle_tz,
                    )
                )
                _push(
                    _case_record(
                        qid,
                        "INVALID",
                        pin_why,
                        env,
                        "INVALID",
                        _own_engine_date(env),
                        source,
                    )
                )
                continue
        invalid_reason = _case_invalid_reason(
            case_before, case_after, case_tz, case_tz_after
        )
        if invalid_reason:
            tallies["INVALID"] += 1
            badge = env.get("badge")
            route = env.get("route")
            print(
                f"{qid}\tINVALID\t{badge}\troute={route}\treason={invalid_reason}"
            )
            cases_out.append(
                {
                    "id": qid,
                    "verdict": "INVALID",
                    "badge": badge,
                    "route": route,
                    "path": classify_path(route),
                    "plan_source": classify_plan_source(env),
                    "crag": classify_crag(env),
                    "rows": len(env.get("rows") or []),
                    "expect": case.get("expect"),
                    "reason": invalid_reason,
                    LEGACY_JUDGE_LABEL: "INVALID",
                    "oracle_schema_version": schema_ver,
                    "oracle_as_of": case_before,
                    "oracle_timezone": case_tz,
                }
            )
            _push(
                _case_record(
                    qid,
                    "INVALID",
                    invalid_reason,
                    env,
                    "INVALID",
                    _own_engine_date(env),
                    source,
                )
            )
            continue
        result = judge_envelope_detailed(
            case, env, oracle_db=oracle_db, oracles=oracles, as_of=case_before
        )
        verdict = result.verdict
        if verdict == "INVALID" and overmask_star_case(env):
            overmask += 1
        tallies[verdict] += 1
        badge = env.get("badge")
        route = env.get("route")
        path = classify_path(route)
        crag = classify_crag(env)
        plan_source = classify_plan_source(env)
        n = len(env.get("rows") or [])
        extra = ""
        if result.reason:
            extra += f"\treason={result.reason}"
        if oracle_db is not None:
            extra += f"\t{LEGACY_JUDGE_LABEL}={result.scorer_ok_rows_not_compared}"
        print(
            f"{qid}\t{verdict}\t{badge}\troute={route}\tpath={path}\t"
            f"plan_source={plan_source}\tcrag={crag}"
            f"\trows={n}\texpect={case['expect']}{extra}"
        )
        cases_out.append(
            {
                "id": qid,
                "verdict": verdict,
                "badge": badge,
                "route": route,
                "path": path,
                "plan_source": plan_source,
                "crag": crag,
                "rows": n,
                "expect": case.get("expect"),
                "demo_fallback_used": bool(env.get("demo_fallback_used")),
                "reason": result.reason,
                LEGACY_JUDGE_LABEL: result.scorer_ok_rows_not_compared,
                "oracle_schema_version": schema_ver,
                "oracle_as_of": case_before,
                "oracle_timezone": case_tz,
            }
        )
        _push(
            _case_record(
                qid, verdict, result.reason, env, result.verdict, _own_engine_date(env), source
            )
        )
    end = read_round_end_health(url, timeout)
    if not end["ok"]:
        clock = _round_clock(as_of, as_of, oracle_tz)
        clock["oracle_as_of_after"] = None
        clock["round_label"] = "INVALID"
        clock["reason"] = "round_end_unread"
        end_day = None
        end_tz = None
        end_ok = False
    else:
        end_day = str(end["engine_as_of"])
        end_tz = _clock_text(end.get("engine_timezone"))
        if end_day != as_of:
            _mark_round_health_midnight(tallies, cases_out, records)
        clock = _round_clock(as_of, as_of, oracle_tz)
        clock["oracle_as_of_after"] = end_day
        end_ok = True
    clock["engine_clock_masked"] = _apply_clock_keep(
        tallies,
        cases_out,
        records,
        reads,
        start=as_of,
        end=end_day,
        start_tz=oracle_tz,
        end_tz=end_tz,
        end_ok=end_ok,
    )
    if clock["engine_clock_masked"] and clock.get("reason") != "round_end_unread":
        clock["reason"] = "engine_clock_masked"
        clock["round_label"] = "INVALID"
        clock["passed"] = False
    invalid_n = int(tallies.get("INVALID") or 0)
    clock["invalid"] = invalid_n
    clock["n"] = sum(tallies.values())
    clock["n_planned"] = planned
    clock["n_without_invalid"] = clock["n"] - invalid_n
    clock["round_health"] = sum(
        1 for rec in records if rec.get("clock_source") == "round_health"
    )
    clock[OVERMASK_STAR_KEY] = overmask
    clock["case_records"] = records
    if invalid_n:
        clock["passed"] = False
    return tallies, cases_out, clock


def _attr(env: Mapping[str, Any] | None, key: str) -> str:
    if not env:
        return "unknown"
    val = env.get(key)
    if val is None or str(val).strip() == "":
        return "unknown"
    return str(val)


def _stored_served(
    env: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[Any]]:
    """Served envelope copied through the existing masker.

    Mask tokens stay. A clear value the masker would catch is masked here
    so the record file never holds unmasked rows.
    """
    if not env:
        return None, []
    from dms_core.pii import fail_closed_mask_envelope

    stored = fail_closed_mask_envelope(dict(env))
    rows = stored.get("rows")
    if not isinstance(rows, list):
        rows = []
    stored["rows"] = rows
    return stored, rows


def _case_record(
    qid: str,
    outcome: str,
    reason: str,
    env: Mapping[str, Any] | None,
    oracle_verdict: str,
    engine_date: str | None,
    clock_source: str,
) -> dict[str, Any]:
    stored, rows = _stored_served(env)
    if outcome == "UNCONFIRMED" or oracle_verdict == "UNCONFIRMED":
        outcome = "INVALID"
        oracle_verdict = "INVALID"
        reason = reason or "INVALID"
    return {
        "id": qid,
        "outcome": outcome,
        "reason": reason,
        "served_provider": _attr(env, "served_provider"),
        "served_model": _attr(env, "served_model"),
        "envelope": stored,
        "rows": rows,
        "oracle_verdict": oracle_verdict,
        "engine_date": engine_date,
        "clock_source": clock_source,
    }


def merge_commit_sha() -> str:
    """Commit this process is running. On main that is the merge commit."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            text=True,
            timeout=5,
        )
        sha = out.strip()
        if sha:
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("GIT_SHA") or os.environ.get("GITHUB_SHA") or "unknown"


def case_record_dir(art: Path) -> Path:
    """DMS_CASE_RECORD_DIR, else DMS_SCORE_CASE_DIR, else the default scratch dir.

    Unset `DMS_CASE_RECORD_DIR` still writes `.tmp/score_cases` when the score
    dir is the default `.tmp`. A custom DMS_SCORE_DIR keeps that older path so
    existing live() tests stay put. Only `DMS_CASE_RECORD_DIR` can make a
    round baseline-eligible.
    """
    configured = (os.environ.get("DMS_CASE_RECORD_DIR") or "").strip()
    if configured:
        return Path(configured)
    legacy = (os.environ.get("DMS_SCORE_CASE_DIR") or "").strip()
    if legacy:
        return Path(legacy)
    if art.resolve() == (ROOT / ".tmp").resolve():
        return art / "score_cases"
    return art


def _path_in_work_tree(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def record_path_block() -> str | None:
    """Unset `DMS_CASE_RECORD_DIR` is scratch. Relative or in-repo is in_repo."""
    raw = (os.environ.get("DMS_CASE_RECORD_DIR") or "").strip()
    if not raw:
        return "record_path_scratch"
    path = Path(raw)
    if not path.is_absolute() or _path_in_work_tree(path):
        return "record_path_in_repo"
    return None


# FROM/JOIN names in pack oracle SQL. Refuse oracles are not scored answers.
_SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_SQL_TABLE_SKIP = frozenset(
    {
        "and",
        "as",
        "cross",
        "from",
        "full",
        "group",
        "inner",
        "join",
        "lateral",
        "left",
        "limit",
        "on",
        "or",
        "order",
        "outer",
        "right",
        "select",
        "values",
        "where",
    }
)
# Private fixture snapshot, keyed by the table tuple. Not the shared warehouse.
_SERVING_FIXTURE_CACHE: dict[tuple[str, ...], dict[str, Any]] = {}
_TABLE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def pack_question_tables(pack_path: Path = DEFAULT_PACK) -> tuple[str, ...]:
    """Tables the scored pack questions name in oracle SQL.

    Each merged pack question's oracle contributes FROM/JOIN identifiers.
    ``expect: refuse`` oracles are not scored answers, so their SQL is not a
    serving table (``alerts`` appears only on ``trap_alerts_ungranted``).
    The curated 52 resolve to inventory, locations, shipments, suppliers,
    and transactions. Not a silent list: the names come from the pack files.
    """
    pack = load_pack(pack_path)
    ids = {
        str(row.get("id") or "")
        for row in merge_pack_questions(list(pack["questions"]))
    }
    found: set[str] = set()
    for qid, row in load_oracles().items():
        if str(qid) not in ids or not isinstance(row, dict):
            continue
        if str(row.get("expect") or "").strip().lower() == "refuse":
            continue
        sql = row.get("sql")
        if not isinstance(sql, str):
            continue
        for match in _SQL_TABLE_RE.finditer(sql):
            name = match.group(1).lower()
            if name not in _SQL_TABLE_SKIP:
                found.add(name)
    return tuple(sorted(found))


def serving_precheck_gap(record: Mapping[str, Any], tables: tuple[str, ...]) -> bool:
    """True when path, inode, mtime, snapshot hash, or a used table's rows are missing.

    A used table that is absent from ``serving_row_counts`` or has 0 rows is a gap.
    """
    path = record.get("serving_path")
    if not isinstance(path, str) or not path.strip():
        return True
    if record.get("serving_inode") in (None, ""):
        return True
    if record.get("serving_mtime") in (None, ""):
        return True
    digest = record.get("serving_snapshot_hash")
    if not isinstance(digest, str) or not digest.strip():
        return True
    counts = record.get("serving_row_counts")
    if not isinstance(counts, dict):
        return True
    for name in tables:
        if name not in counts:
            return True
        try:
            n = int(counts[name])
        except (TypeError, ValueError):
            return True
        if n <= 0:
            return True
    return False


def _table_row_counts(path: Path, tables: tuple[str, ...]) -> dict[str, int]:
    """Row counts on a copy. A missing table is omitted (the gap check catches it)."""
    import duckdb

    wanted = [name for name in tables if _TABLE_IDENT.match(name)]
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'"
        ).fetchall()
        present = {str(row[0]).lower() for row in rows}
        counts: dict[str, int] = {}
        for name in wanted:
            if name not in present:
                continue
            got = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
            counts[name] = int(got[0]) if got else 0
        return counts
    except duckdb.Error:
        return {}
    finally:
        con.close()


def _snapshot_serving_file(path: Path, tables: tuple[str, ...]) -> dict[str, Any]:
    """Stat the serving file, hash a copy, count rows on the copy.

    The live file is not attached. That is the shared-file lock this must
    not bring back. A missing file is an empty record (a skipped precheck).
    """
    import hashlib
    import shutil
    import tempfile

    src = Path(path)
    if not src.is_file():
        return {}
    st = src.stat()
    tmp = Path(tempfile.mkdtemp(prefix="serving_precheck_"))
    copy = tmp / "snapshot.duckdb"
    shutil.copy2(src, copy)
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    return {
        "serving_path": str(src.resolve()),
        "serving_inode": int(st.st_ino),
        "serving_mtime": st.st_mtime,
        "serving_snapshot_hash": digest,
        "serving_row_counts": _table_row_counts(copy, tables),
    }


def _fixture_serving_precheck(tables: tuple[str, ...]) -> dict[str, Any]:
    """Private seeded file when no Cortex warehouse is configured.

    Offline ``live()`` rounds (the #323 unit path) have no prove serving
    file. Recording this snapshot keeps those rounds from looking skipped.
    A prove baseline sets ``CORTEX_WAREHOUSE_DB`` or ``DMS_SERVING_PRECHECK``
    to the file Cortex serves. This never calls ``ensure_demo_warehouse``
    on that file.
    """
    import tempfile

    cached = _SERVING_FIXTURE_CACHE.get(tables)
    if cached is not None:
        return {
            **cached,
            "serving_row_counts": dict(cached.get("serving_row_counts") or {}),
        }
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = Path(tempfile.mkdtemp(prefix="serving_fixture_")) / "fixture.duckdb"
    ensure_demo_warehouse(path)
    record = _snapshot_serving_file(path, tables)
    if not serving_precheck_gap(record, tables):
        _SERVING_FIXTURE_CACHE[tables] = record
    return record


def load_serving_precheck(tables: tuple[str, ...]) -> dict[str, Any]:
    """Serving block stored on the round summary.

    Swap: ``DMS_SERVING_PRECHECK`` is the recorded JSON (Platform, or a test
    that plants a skipped or partial check). Unset reads the explicit Cortex
    file (``CORTEX_WAREHOUSE_DB`` / ``DMS_ORACLE_WAREHOUSE``) via a copy.
    Neither set: a private fixture snapshot, not ``data/dms_demo.duckdb``.
    """
    raw = (os.environ.get("DMS_SERVING_PRECHECK") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return parsed
    from dms_executor.warehouse_identity import explicit_engine_warehouse

    explicit = explicit_engine_warehouse()
    if explicit is not None:
        return _snapshot_serving_file(explicit, tables)
    return _fixture_serving_precheck(tables)


def baseline_eligibility(
    *,
    path_block: str | None,
    write_failed: bool,
    unidentified: bool,
    round_label: str | None,
    round_reason: str | None,
    pin_preflight_unavailable: bool = False,
    engine_clock_masked: bool = False,
    serving_precheck_missing: bool = False,
) -> tuple[bool, list[str]]:
    """The only baseline gate. Eligible is true exactly when the list is empty.

    pin_preflight_unavailable is a blocked pin preflight. An in-round 503 does
    not set it. round_end_unread is a round INVALID reason and is listed here.
    engine_clock_masked is added when a clock field was masked, including
    beside round_end_unread. serving_precheck_missing is a round record that
    lacks the serving path, inode, mtime, snapshot hash, or a positive row
    count for a table the pack questions use.
    """
    reasons: list[str] = []
    if path_block:
        reasons.append(path_block)
    if write_failed:
        reasons.append("record_write_failed")
    if unidentified:
        reasons.append("record_unidentified")
    if pin_preflight_unavailable:
        reasons.append("pin_preflight_unavailable")
    if round_reason == "round_end_unread" and "round_end_unread" not in reasons:
        reasons.append("round_end_unread")
    if round_label == "INVALID":
        invalid = round_reason or "INVALID"
        if invalid not in reasons:
            reasons.append(invalid)
    if engine_clock_masked and "engine_clock_masked" not in reasons:
        reasons.append("engine_clock_masked")
    if serving_precheck_missing and "serving_precheck_missing" not in reasons:
        reasons.append("serving_precheck_missing")
    return (not reasons, reasons)


def case_record_path(directory: Path, run_id: str, sha: str) -> Path:
    return directory / f"score_cases_{run_id}_{sha}.jsonl"


def _record_id_ok(value: str | None) -> bool:
    """Run id and commit sha are hex. Empty and 'unknown' are unreadable."""
    text = (value or "").strip().lower()
    if len(text) < 8:
        return False
    return all(ch in "0123456789abcdef" for ch in text)


def write_case_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()


def case_record_matches(path: Path, n: int) -> bool:
    if not path.is_file():
        return False
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return len(lines) == n


def live(url: str, timeout: float, oracle_db: Path | None = None) -> int:
    why = require_oracle_db(oracle_db)
    if why:
        print(why)
        return EXIT_CONFIG
    assert oracle_db is not None
    tallies, cases, clock = score_live_entry(url, timeout, oracle_db=oracle_db)
    n = sum(tallies.values())
    wrong = tallies["WRONG"]
    oracle_error = int(tallies.get("ORACLE_ERROR") or 0)
    invalid_n = int(tallies.get("INVALID") or 0)
    rate_limit = int(tallies.get("RATE_LIMIT") or 0)
    overmask = int(clock.get(OVERMASK_STAR_KEY) or 0)
    answered_ok = tallies["OK"] + tallies["LAYER"]
    precision = 100.0 if answered_ok + wrong == 0 else (
        100.0 * answered_ok / (answered_ok + wrong)
    )
    cats = pack_category_report(tallies, figure_label=FIGURE_LABEL_LIVE)
    print(
        f"precision-on-answered {precision:.2f} pct  "
        f"coverage {tallies['OK']}/{n}  "
        f"WRONG {wrong}  abstain {tallies['ABSTAIN']}  layer {tallies['LAYER']}  "
        f"INVALID {invalid_n}  RATE_LIMIT {rate_limit}  "
        f"round_health {int(clock.get('round_health') or 0)}  "
        f"n {n}  n_planned {int(clock.get('n_planned') or _planned_n())}  "
        f"n_without_invalid {n - invalid_n}"
    )
    print(f"{OVERMASK_STAR_KEY} {overmask}")
    as_of = clock.get("oracle_as_of")
    after = clock.get("oracle_as_of_after")
    oracle_tz = clock.get("oracle_timezone")
    print(
        f"oracle_db={oracle_db} schema_version={read_schema_version(oracle_db)} "
        f"oracle_as_of={as_of} oracle_as_of_after={after} timezone={oracle_tz}"
    )
    print_category_report(cats)
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    sha = merge_commit_sha()
    rec_path = case_record_path(case_record_dir(art), run_id, sha)
    unidentified = not _record_id_ok(run_id) or not _record_id_ok(sha)
    write_failed = False
    if not unidentified:
        try:
            write_case_records(rec_path, list(clock.get("case_records") or []))
        except OSError:
            write_failed = True
    date_invalid = clock.get("round_label") == "INVALID"
    record_missing = (
        not unidentified
        and not write_failed
        and not case_record_matches(rec_path, n)
    )
    record_bad = unidentified or write_failed or record_missing
    round_label = "INVALID" if date_invalid or record_bad else clock.get("round_label")
    reason = clock.get("reason")
    if reason != "engine_date_unread":
        if unidentified:
            reason = "record_unidentified"
        elif write_failed:
            reason = "record_write_failed"
    abs_record = rec_path if rec_path.is_absolute() else rec_path.absolute()
    path_block = record_path_block()
    serving_tables = pack_question_tables()
    serving = load_serving_precheck(serving_tables)
    precheck_missing = serving_precheck_gap(serving, serving_tables)
    eligible, ineligible = baseline_eligibility(
        path_block=path_block,
        write_failed=write_failed,
        unidentified=unidentified,
        round_label=round_label if isinstance(round_label, str) else None,
        round_reason=reason if isinstance(reason, str) else None,
        pin_preflight_unavailable=bool(clock.get("pin_preflight_unavailable")),
        engine_clock_masked=bool(clock.get("engine_clock_masked")),
        serving_precheck_missing=precheck_missing,
    )
    print(f"case_record={abs_record}")
    print(
        f"baseline_eligible={str(eligible).lower()} "
        f"baseline_ineligible_reasons={','.join(ineligible)}"
    )
    (art / "score_curated.json").write_text(
        json.dumps(
            {
                "kind": "dms.score_curated",
                "pack": "curated_ceo",
                "precision_on_answered": round(precision, 2),
                "coverage_pct": round(100.0 * tallies["OK"] / n, 2) if n else 0.0,
                "correct": tallies["OK"],
                "answered": tallies["OK"] + tallies["LAYER"],
                "wrong": wrong,
                "oracle_error": oracle_error,
                "invalid": invalid_n,
                "rate_limit": rate_limit,
                "layer": tallies["LAYER"],
                OVERMASK_STAR_KEY: overmask,
                "total": n,
                "n": n,
                "n_planned": int(clock.get("n_planned") or _planned_n()),
                "n_without_invalid": n - invalid_n,
                "round_health": int(clock.get("round_health") or 0),
                "abstained": tallies["ABSTAIN"],
                "reason": reason,
                "passed": (
                    wrong == 0
                    and oracle_error == 0
                    and invalid_n == 0
                    and rate_limit == 0
                    and not date_invalid
                    and not record_bad
                ),
                "cases": cases,
                "oracle_db": str(oracle_db),
                "schema_version": read_schema_version(oracle_db),
                "oracle_as_of": as_of,
                "oracle_as_of_after": after,
                "oracle_timezone": oracle_tz,
                "round_label": round_label,
                "pin_reason": clock.get("pin_reason"),
                "run_id": run_id,
                "commit_sha": sha,
                "case_record": str(abs_record),
                "serving_path": serving.get("serving_path"),
                "serving_inode": serving.get("serving_inode"),
                "serving_mtime": serving.get("serving_mtime"),
                "serving_snapshot_hash": serving.get("serving_snapshot_hash"),
                "serving_row_counts": serving.get("serving_row_counts"),
                "serving_tables": list(serving_tables),
                "baseline_eligible": eligible,
                "baseline_ineligible_reasons": ineligible,
                "record_path_scratch": path_block == "record_path_scratch",
                "record_path_in_repo": path_block == "record_path_in_repo",
                "categories": cats,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    inv = _invalid_round_exit(clock.get("round_label"), clock.get("reason"))
    if inv is not None:
        return inv
    if unidentified:
        print("INVALID: record_unidentified. Not WRONG.")
        return EXIT_FAIL
    if write_failed:
        print("INVALID: record_write_failed. Not WRONG.")
        return EXIT_FAIL
    if record_missing:
        print("INVALID: case record file missing. Not WRONG.")
        return EXIT_FAIL
    engine_bad = any(
        row.get("verdict") == "INVALID"
        and str(row.get("reason") or "").startswith("engine_")
        for row in cases
    )
    if engine_bad:
        inv_case = _invalid_case_exit(tallies)
        if inv_case is not None:
            return inv_case
    if invalid_n:
        print(f"FAIL: INVALID={invalid_n} (masked compared column). Not WRONG.")
        return EXIT_FAIL
    if oracle_error:
        print("FAIL: ORACLE_ERROR>0 (oracle SQL did not run). Not OK, not skipped.")
        return EXIT_FAIL
    if rate_limit:
        print(f"FAIL: RATE_LIMIT={rate_limit}. Not WRONG.")
        return EXIT_FAIL
    if wrong:
        print("FAIL: confidently wrong")
        return EXIT_FAIL
    print("PASS: 0 WRONG")
    return EXIT_PASS


def _ab_miss() -> dict[str, Any]:
    return {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "path miss"}


def _ab_seed(path: Path) -> Any:
    """Demo warehouse + demo_ontology for the generative A/B lane.

    Not a certified-pack expand. Same lake the exact-match pack names.
    """
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.generative_ask import load_verified_ontology
    from dms_executor.ontology import demo_ontology

    ensure_demo_warehouse(path)
    loaded = load_verified_ontology(path, demo_ontology(path))
    if loaded is None:
        raise RuntimeError("A/B demo ontology failed verify")
    return loaded


def _tally() -> dict[str, int]:
    return {
        "OK": 0,
        "ABSTAIN": 0,
        "LAYER": 0,
        "WRONG": 0,
        "ORACLE_ERROR": 0,
        "INVALID": 0,
        "RATE_LIMIT": 0,
    }


def _path_report(name: str, tallies: dict[str, int], n: int) -> dict[str, Any]:
    wrong = tallies["WRONG"]
    answered = tallies["OK"] + tallies["LAYER"]
    oracle_error = int(tallies.get("ORACLE_ERROR") or 0)
    invalid = int(tallies.get("INVALID") or 0)
    return {
        "path": name,
        "n": n,
        "n_without_invalid": n - invalid,
        "ok": tallies["OK"],
        "layer": tallies["LAYER"],
        "abstain": tallies["ABSTAIN"],
        "wrong": wrong,
        "oracle_error": oracle_error,
        "invalid": invalid,
        "answered": answered,
        "coverage_answered_pct": round(100.0 * answered / n, 2) if n else 0.0,
        "categories": pack_category_report(tallies, figure_label=FIGURE_LABEL_FIXTURE),
    }


def run_ab_curated(
    pack_path: Path = DEFAULT_PACK,
    *,
    oracle_db: Path | None = None,
    compare_rows: bool = False,
) -> dict[str, Any]:
    """Offline A/B: exact-match pack vs retrieve+bind generative on the same pack.

    Fake submit/ledger so CI has no keys. Does not expand certified packs.
    compare_rows=False keeps the badge/min_rows judge (existing tests).
    CLI --ab sets compare_rows so l0 answers are checked against oracle SQL.
    """
    import tempfile
    from types import SimpleNamespace

    from dms_executor.demo_grants import DEMO_SPACE_GRANTS, canonical_space_id
    from dms_executor.demo_pack import maybe_pack_ask, maybe_uncertified_refuse_ask
    from dms_executor.generative_ask import maybe_generative_ask
    from dms_executor.semantic_retrieve import bind_plan

    pack = load_pack(pack_path)
    tmp = Path(tempfile.mkdtemp()) / "ab_gen01.duckdb"
    onto = _ab_seed(tmp)
    compare_db: Path | None = None
    oracles: dict[str, Any] | None = None
    schema_ver: str | None = None
    before, oracle_tz = _ab_engine_clock(tmp)
    as_of = before
    if compare_rows:
        compare_db = oracle_db if oracle_db is not None else tmp
        oracles = load_oracles()
        schema_ver = read_schema_version(compare_db)
    def submit(sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(tmp)
        try:
            _ans_as_of, _ans_tz = read_engine_clock_from_con(con)
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            out = [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:  # noqa: BLE001 -- execute-validate fail is an abstain
            return SimpleNamespace(ok=False, status="err", run_id="run_ab", output=None)
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_ab", output={"rows": out})

    def ledger(_payload: dict[str, Any]) -> Any:
        return SimpleNamespace(entry_id="led_ab", hash="hash_ab_not_entry")

    exact_t = _tally()
    gen_t = _tally()
    cases_out: list[dict[str, Any]] = []
    for case in pack["questions"]:
        q = str(case["question"])
        space = resolve_space(case, pack["spaces"])
        entry = DEMO_SPACE_GRANTS.get(canonical_space_id(space))
        grants = set(entry[1]) if entry else set()
        exact_env = maybe_uncertified_refuse_ask(q, space_id=space) or maybe_pack_ask(
            q,
            space_id=space,
            grantable=grants,
            submit=submit,
            ledger_append=ledger,
        )
        exact_env = exact_env if exact_env is not None else _ab_miss()
        gen_env = maybe_generative_ask(
            q,
            space_id=space,
            warehouse=tmp,
            grantable=grants,
            compute=lambda ctx, _q=q: bind_plan(_q, ctx),
            submit=submit,
            ledger_append=ledger,
            ontology=onto,
        )
        gen_env = gen_env if gen_env is not None else _ab_miss()
        exact_r = judge_detailed(
            case, exact_env, oracle_db=compare_db, oracles=oracles, as_of=as_of
        )
        gen_r = judge_detailed(
            case, gen_env, oracle_db=compare_db, oracles=oracles, as_of=as_of
        )
        ev = exact_r.verdict
        gv = gen_r.verdict
        exact_t[ev] += 1
        gen_t[gv] += 1
        cases_out.append(
            {
                "id": case["id"],
                "expect": case.get("expect"),
                "exact": ev,
                "generative": gv,
                "exact_badge": exact_env.get("badge"),
                "generative_badge": gen_env.get("badge"),
                "plan_source": classify_plan_source(gen_env),
                "crag": classify_crag(gen_env),
                "exact_reason": exact_r.reason,
                "generative_reason": gen_r.reason,
                LEGACY_JUDGE_LABEL: {
                    "exact": exact_r.scorer_ok_rows_not_compared,
                    "generative": gen_r.scorer_ok_rows_not_compared,
                },
            }
        )
    n = len(pack["questions"])
    exact_r = _path_report("exact_match", exact_t, n)
    gen_r = _path_report("generative_semantic", gen_t, n)
    crag_counts: dict[str, int] = {}
    for row in cases_out:
        key = str(row.get("crag") or "skipped")
        crag_counts[key] = crag_counts.get(key, 0) + 1
    base = BASELINE_AB_A9578348
    oracle_error = int(exact_r["oracle_error"]) + int(gen_r["oracle_error"])
    after, tz_after = _ab_engine_clock(tmp)
    timezone = oracle_tz or tz_after
    report = {
        "kind": "dms.ab_gen01",
        "pack": "curated_ceo",
        "claim": "measured",
        "exact_match": exact_r,
        "generative": gen_r,
        "crag": crag_counts,
        "baseline_ab": {
            "commit": base["commit"],
            "exact_answered": base["exact_answered"],
            "generative_answered": base["generative_answered"],
            "exact_coverage_answered_pct": base["exact_coverage_answered_pct"],
            "generative_coverage_answered_pct": base["generative_coverage_answered_pct"],
            "n": base["n"],
            "wrong": base["wrong"],
        },
        "distill": distill_block(),
        "generative_vs_baseline": answered_vs_baseline(
            gen_r["answered"], int(base["generative_answered"])
        ),
        "wrong": exact_r["wrong"] + gen_r["wrong"],
        "oracle_error": oracle_error,
        "passed": (
            exact_r["wrong"] == 0
            and gen_r["wrong"] == 0
            and oracle_error == 0
            and int(exact_r.get("invalid") or 0) == 0
            and int(gen_r.get("invalid") or 0) == 0
        ),
        "compare_rows": compare_rows,
        "oracle_db": str(compare_db) if compare_db is not None else None,
        "schema_version": schema_ver,
        "oracle_as_of": as_of,
        "oracle_timezone": timezone,
        "cases": cases_out,
    }
    stamp_round_clock(report, before, after, timezone)
    return report


def ab_offline(oracle_db: Path | None = None) -> int:
    if oracle_db is not None:
        why = require_oracle_db(oracle_db)
        if why:
            print(why)
            return EXIT_CONFIG
    report = run_ab_curated(oracle_db=oracle_db, compare_rows=True)
    exact = report["exact_match"]
    gen = report["generative"]
    base = report["baseline_ab"]
    print(
        f"{'path':<22} n n_without_invalid ok layer abstain wrong invalid "
        "answered coverage_answered"
    )
    for row in (exact, gen):
        print(
            f"{row['path']:<22} {row['n']} {row['n_without_invalid']} {row['ok']} "
            f"{row['layer']} {row['abstain']} {row['wrong']} {row['invalid']} "
            f"{row['answered']} {row['coverage_answered_pct']:.2f} pct"
        )
    print(
        f"baseline @ {base['commit']}: exact_answered={base['exact_answered']} "
        f"generative_answered={base['generative_answered']} WRONG={base['wrong']}"
    )
    print(
        f"generative vs baseline: {report['generative_vs_baseline']}  "
        f"crag={report['crag']}"
    )
    if report.get("oracle_db"):
        print(
            f"oracle_db={report['oracle_db']} "
            f"schema_version={report.get('schema_version')} "
            f"oracle_as_of={report.get('oracle_as_of')} "
            f"oracle_as_of_after={report.get('oracle_as_of_after')} "
            f"timezone={report.get('oracle_timezone')}"
        )
        print_category_report(exact["categories"])
        print_category_report(gen["categories"])
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in report.items() if k != "cases"}
    blob = json.dumps(slim, indent=2)
    if "99.95" in blob or "COMPLETE" in blob:
        print("FAIL: A/B report invented COMPLETE / 99.95")
        return EXIT_FAIL
    (art / "ab_gen01.json").write_text(blob + "\n", encoding="utf-8")
    inv = _invalid_round_exit(report.get("round_label"), report.get("reason"))
    if inv is not None:
        return inv
    if int(report.get("oracle_error") or 0):
        print("FAIL: ORACLE_ERROR>0 (oracle SQL did not run). Not OK, not skipped.")
        return EXIT_FAIL
    if not report["passed"]:
        print("FAIL: A/B WRONG>0")
        return EXIT_FAIL
    print("PASS: A/B WRONG=0 on both paths (not EPIC-019 COMPLETE)")
    return EXIT_PASS


def _answered_by_path(cases: list[dict[str, Any]]) -> dict[str, int]:
    out = {"exact_match": 0, "generative": 0, "other": 0}
    for row in cases:
        if row.get("verdict") not in {"OK", "LAYER"}:
            continue
        path = str(row.get("path") or "other")
        if path not in out:
            path = "other"
        out[path] += 1
    return out


def build_climb_report(
    tallies: dict[str, int],
    *,
    cases: list[dict[str, Any]],
    url: str,
) -> dict[str, Any]:
    n = sum(tallies.values())
    wrong = tallies["WRONG"]
    oracle_error = int(tallies.get("ORACLE_ERROR") or 0)
    answered = tallies["OK"] + tallies["LAYER"]
    base = BASELINE_91C5CC99
    base_ans = baseline_answered()
    vs = answered_vs_baseline(answered, base_ans)
    by_path = _answered_by_path(cases)
    cats = pack_category_report(tallies, figure_label=FIGURE_LABEL_LIVE)
    return {
        "kind": "dms.score_climb",
        "ticket": "GEN-02",
        "issue": 180,
        "pack": "curated_ceo",
        "url": url,
        "claim": "measured",
        "baseline": {
            "commit": base["commit"],
            "n": base["n"],
            "ok": base["ok"],
            "layer": base["layer"],
            "abstain": base["abstain"],
            "wrong": base["wrong"],
            "answered": base_ans,
        },
        "measured": {
            "n": n,
            "ok": tallies["OK"],
            "layer": tallies["LAYER"],
            "abstain": tallies["ABSTAIN"],
            "wrong": wrong,
            "oracle_error": oracle_error,
            "answered": answered,
            "coverage_ok_pct": round(100.0 * tallies["OK"] / n, 2) if n else 0.0,
            "coverage_answered_pct": round(100.0 * answered / n, 2) if n else 0.0,
        },
        "delta": {
            "ok": tallies["OK"] - int(base["ok"]),
            "layer": tallies["LAYER"] - int(base["layer"]),
            "abstain": tallies["ABSTAIN"] - int(base["abstain"]),
            "wrong": wrong - int(base["wrong"]),
            "answered": answered - base_ans,
        },
        "answered_by_path": by_path,
        "answered_vs_baseline": vs,
        "wrong": wrong,
        "oracle_error": oracle_error,
        "invalid": int(tallies.get("INVALID") or 0),
        "n_without_invalid": n - int(tallies.get("INVALID") or 0),
        "passed_wrong_zero": wrong == 0,
        "categories": cats,
        "distill": distill_block(),
        "cases": cases,
    }


def classify_health(
    status: int, body: dict[str, Any] | None, ctype: str | None
) -> tuple[str, str]:
    """ok | blocked | fail. IAP/auth is BLOCKED, not a 26-WRONG score."""
    if status in {401, 403}:
        return "blocked", f"health status={status} (IAP/auth). Not a score."
    if status != 200:
        return "fail", f"health status={status}"
    if body is None:
        kind = (ctype or "").split(";")[0].strip().lower()
        if kind == "text/html":
            return "fail", "health is HTML (SPA /health? use /api)"
        return "fail", "health is not JSON"
    if body.get("demo_fallback") is True:
        return "fail", "demo_fallback=true (lying affordance)"
    if str(body.get("ask_mode") or "") == "demo":
        return "fail", "ask_mode=demo"
    climb = body.get("gen_path_climb")
    extra = ""
    if isinstance(climb, dict) and climb.get("n") is not None:
        extra = f" climb_n={climb.get('n')} frozen_n={climb.get('frozen_n')}"
    return "ok", f"product={body.get('product')} ask_mode={body.get('ask_mode')}{extra}"


def probe_climb_health(
    url: str, timeout: float
) -> tuple[str, str, dict[str, Any] | None]:
    """ok | blocked | fail plus /health JSON. Does not invent a score."""
    try:
        import httpx
    except ImportError:
        return "blocked", "httpx required (DMS .venv). Not a score.", None

    health = f"{url.rstrip('/')}/health"
    try:
        resp = score_http("GET", health, timeout=min(timeout, 15.0))
    except httpx.HTTPError as exc:
        return "blocked", f"{type(exc).__name__}: {exc}", None
    status = int(resp.status_code)
    ctype = resp.headers.get("content-type") if resp.headers is not None else None
    text = str(getattr(resp, "text", "") or "")[:8000]
    cf = cf1010_blocked_detail(status, text)
    if cf:
        return "blocked", cf, None
    try:
        parsed = json.loads(text)
    except ValueError:
        kind, detail = classify_health(status, None, ctype)
        return kind, detail, None
    body = parsed if isinstance(parsed, dict) else None
    kind, detail = classify_health(status, body, ctype)
    return kind, detail, body


def probe_climb_host(url: str, timeout: float) -> tuple[str, str]:
    """ok | blocked | fail. Health only (httpx). Does not invent a score."""
    kind, detail, _body = probe_climb_health(url, timeout)
    return kind, detail


def climb(url: str, timeout: float, oracle_db: Path | None = None) -> int:
    kind, detail = probe_climb_host(url, timeout)
    print(f"GEN-02 climb host {url}  [{kind}] {detail}")
    if kind == "blocked":
        print("BLOCKED: cannot reach host. Not a score. Not COMPLETE.")
        return EXIT_BLOCKED
    if kind == "fail":
        print("FAIL: host is not a live governed ask")
        return EXIT_FAIL
    why = require_oracle_db(oracle_db)
    if why:
        print(why)
        return EXIT_CONFIG
    assert oracle_db is not None
    try:
        tallies, cases, clock = score_live_entry(url, timeout, oracle_db=oracle_db)
    except ImportError:
        print("CONFIG: httpx required (DMS .venv). Not a score.")
        return EXIT_CONFIG
    report = build_climb_report(tallies, cases=cases, url=url)
    report["oracle_db"] = str(oracle_db)
    report["schema_version"] = read_schema_version(oracle_db)
    stamp_round_clock(
        report,
        clock.get("oracle_as_of"),
        clock.get("oracle_as_of_after"),
        clock.get("oracle_timezone"),
    )
    if clock.get("round_label") == "INVALID":
        report["round_label"] = "INVALID"
        report["passed"] = False
    report["reason"] = clock.get("reason")
    if clock.get("pin_reason"):
        report["pin_reason"] = clock.get("pin_reason")
    planned = int(clock.get("n_planned") or _planned_n())
    report["n_planned"] = planned
    report["measured"]["n_planned"] = planned
    measured = report["measured"]
    base = report["baseline"]
    delta = report["delta"]
    by_path = report["answered_by_path"]
    print(f"{'':<10} {'n':>3} {'ok':>3} {'layer':>5} {'abstain':>7} {'wrong':>5} {'answered':>8}")
    print(
        f"{'baseline':<10} {base['n']:>3} {base['ok']:>3} {base['layer']:>5} "
        f"{base['abstain']:>7} {base['wrong']:>5} {base['answered']:>8}  @ {base['commit']}"
    )
    print(
        f"{'measured':<10} {measured['n']:>3} {measured['ok']:>3} {measured['layer']:>5} "
        f"{measured['abstain']:>7} {measured['wrong']:>5} {measured['answered']:>8}"
    )
    print(f"n {measured['n']}  n_planned {report['n_planned']}")
    print(
        f"{'delta':<10} {'':>3} {delta['ok']:>+3} {delta['layer']:>+5} "
        f"{delta['abstain']:>+7} {delta['wrong']:>+5} {delta['answered']:>+8}"
    )
    print(
        f"answered_by_path exact_match={by_path['exact_match']} "
        f"generative={by_path['generative']} other={by_path['other']}"
    )
    print(f"answered vs baseline @ {base['commit']}: {report['answered_vs_baseline']}")
    print(
        f"oracle_db={oracle_db} schema_version={report['schema_version']} "
        f"oracle_as_of={report.get('oracle_as_of')} "
        f"oracle_as_of_after={report.get('oracle_as_of_after')} "
        f"timezone={report.get('oracle_timezone')}"
    )
    print_category_report(report["categories"])
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in report.items() if k != "cases"}
    (art / "score_climb.json").write_text(
        json.dumps(slim, indent=2) + "\n", encoding="utf-8"
    )
    (art / "score_climb_cases.json").write_text(
        json.dumps(report["cases"], indent=2) + "\n", encoding="utf-8"
    )
    inv = _invalid_round_exit(report.get("round_label"), report.get("reason"))
    if inv is not None:
        return inv
    inv_case = _invalid_case_exit(tallies)
    if inv_case is not None:
        return inv_case
    if int(report.get("oracle_error") or 0):
        print("FAIL: ORACLE_ERROR>0 (oracle SQL did not run). Not OK, not skipped.")
        return EXIT_FAIL
    if not report["passed_wrong_zero"]:
        print("FAIL: WRONG>0 (law). Not COMPLETE.")
        return EXIT_FAIL
    print(
        "PASS: WRONG=0 measured. Climb is the answered delta, not a 99.95% claim. "
        "Not EPIC-019 COMPLETE."
    )
    return EXIT_PASS


def _crag_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in cases:
        key = str(row.get("crag") or "skipped")
        out[key] = out.get(key, 0) + 1
    return out


def climb_ab_live(url: str, timeout: float, oracle_db: Path | None = None) -> int:
    """Live isolated A/B: ask_path=exact vs ask_path=generative. WRONG=0 law."""
    kind, detail = probe_climb_host(url, timeout)
    print(f"GEN-02 live A/B host {url}  [{kind}] {detail}")
    if kind == "blocked":
        print("BLOCKED: cannot reach host. Not a score. Not COMPLETE.")
        return EXIT_BLOCKED
    if kind == "fail":
        print("FAIL: host is not a live governed ask")
        return EXIT_FAIL
    why = require_oracle_db(oracle_db)
    if why:
        print(why)
        return EXIT_CONFIG
    assert oracle_db is not None
    print("-- ask_path=exact --")
    try:
        exact_t, exact_cases, exact_clock = score_live_entry(
            url, timeout, ask_path="exact", oracle_db=oracle_db
        )
        print("-- ask_path=generative --")
        gen_t, gen_cases, gen_clock = score_live_entry(
            url, timeout, ask_path="generative", oracle_db=oracle_db
        )
    except ImportError:
        print("CONFIG: httpx required (DMS .venv). Not a score.")
        return EXIT_CONFIG
    n = sum(exact_t.values())
    exact_r = _path_report("exact_match", exact_t, n)
    gen_r = _path_report("generative_semantic", gen_t, n)
    exact_r["categories"] = pack_category_report(
        exact_t, figure_label=FIGURE_LABEL_LIVE
    )
    gen_r["categories"] = pack_category_report(gen_t, figure_label=FIGURE_LABEL_LIVE)
    base = BASELINE_AB_A9578348
    crag = _crag_counts(gen_cases)
    vs = answered_vs_baseline(gen_r["answered"], int(base["generative_answered"]))
    oracle_error = int(exact_t.get("ORACLE_ERROR") or 0) + int(
        gen_t.get("ORACLE_ERROR") or 0
    )
    report = {
        "kind": "dms.ab_live",
        "ticket": "GEN-02",
        "issue": 180,
        "pack": "curated_ceo",
        "url": url,
        "claim": "measured",
        "oracle_db": str(oracle_db),
        "schema_version": read_schema_version(oracle_db),
        "oracle_as_of": exact_clock.get("oracle_as_of"),
        "oracle_as_of_after": gen_clock.get("oracle_as_of_after"),
        "oracle_timezone": exact_clock.get("oracle_timezone")
        or gen_clock.get("oracle_timezone"),
        "baseline_ab": {
            "commit": base["commit"],
            "exact_answered": base["exact_answered"],
            "generative_answered": base["generative_answered"],
            "exact_coverage_answered_pct": base["exact_coverage_answered_pct"],
            "generative_coverage_answered_pct": base["generative_coverage_answered_pct"],
            "n": base["n"],
            "wrong": base["wrong"],
        },
        "distill": distill_block(),
        "exact_match": exact_r,
        "generative": gen_r,
        "crag": crag,
        "generative_vs_baseline": vs,
        "wrong": exact_r["wrong"] + gen_r["wrong"],
        "oracle_error": oracle_error,
        "invalid": int(exact_t.get("INVALID") or 0) + int(gen_t.get("INVALID") or 0),
        "reason": exact_clock.get("reason") or gen_clock.get("reason"),
        "pin_reason": exact_clock.get("pin_reason") or gen_clock.get("pin_reason"),
        "passed_wrong_zero": exact_r["wrong"] == 0 and gen_r["wrong"] == 0,
        "n_planned": int(
            exact_clock.get("n_planned") or gen_clock.get("n_planned") or _planned_n()
        ),
    }
    exact_r["n_planned"] = report["n_planned"]
    gen_r["n_planned"] = report["n_planned"]
    if (
        exact_clock.get("round_label") == "INVALID"
        or gen_clock.get("round_label") == "INVALID"
    ):
        report["round_label"] = "INVALID"
        report["passed"] = False
    blob = json.dumps(report, indent=2)
    if "99.95" in blob or "COMPLETE" in blob:
        print("FAIL: live A/B invented COMPLETE / 99.95")
        return EXIT_FAIL
    print(
        f"{'path':<22} n n_planned n_without_invalid ok layer abstain wrong invalid "
        "answered coverage_answered"
    )
    for row in (exact_r, gen_r):
        print(
            f"{row['path']:<22} {row['n']} {row['n_planned']} "
            f"{row['n_without_invalid']} {row['ok']} "
            f"{row['layer']} {row['abstain']} {row['wrong']} {row['invalid']} "
            f"{row['answered']} {row['coverage_answered_pct']:.2f} pct"
        )
    print(
        f"baseline @ {base['commit']}: exact_answered={base['exact_answered']} "
        f"generative_answered={base['generative_answered']}"
    )
    print(f"generative vs baseline: {vs}  crag={crag}")
    print(
        f"oracle_db={oracle_db} schema_version={report['schema_version']} "
        f"oracle_as_of={report.get('oracle_as_of')} "
        f"oracle_as_of_after={report.get('oracle_as_of_after')} "
        f"timezone={report.get('oracle_timezone')}"
    )
    print_category_report(exact_r["categories"])
    print_category_report(gen_r["categories"])
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    (art / "score_climb_ab.json").write_text(blob + "\n", encoding="utf-8")
    (art / "score_climb_ab_cases.json").write_text(
        json.dumps({"exact": exact_cases, "generative": gen_cases}, indent=2) + "\n",
        encoding="utf-8",
    )
    inv = _invalid_round_exit(report.get("round_label"), report.get("reason"))
    if inv is not None:
        return inv
    if int(report.get("invalid") or 0):
        print(
            f"FAIL: INVALID={report['invalid']} (engine date or timezone mismatch). "
            "Not WRONG. Not PASS."
        )
        return EXIT_FAIL
    if oracle_error:
        print("FAIL: ORACLE_ERROR>0 (oracle SQL did not run). Not OK, not skipped.")
        return EXIT_FAIL
    if not report["passed_wrong_zero"]:
        print("FAIL: WRONG>0 (law). Not COMPLETE.")
        return EXIT_FAIL
    print(
        "PASS: live A/B WRONG=0 measured. Climb is the gen answered delta, "
        "not a 99.95% claim. Not EPIC-019 COMPLETE."
    )
    return EXIT_PASS


def _plan_source_bucket(cases: list[dict[str, Any]]) -> dict[str, int]:
    out = {"ontology_plan": 0, "bind_plan": 0, "other": 0}
    for row in cases:
        if row.get("verdict") not in {"OK", "LAYER"}:
            continue
        src = str(row.get("plan_source") or "other")
        if src not in out:
            src = "other"
        out[src] += 1
    return out


def _leftover_l0_unscored(cases: list[dict[str, Any]]) -> list[str]:
    """Leftover L0 ids missing in this prove run.

    #212 KEEP_HOLD answered=17/26 because cq_audit_overdue was never asked.
    #216 RISE_PASS 21/31 never asked the unused certified synonyms.
    #218 RISE_PASS 24/34 never asked the leftover typo synonym / ops L0s.
    #220 RISE_PASS 27/37 never asked leftover Ops sku synonyms / chemicals.
    #222 RISE_PASS 30/40 never asked leftover Ops expired / cold / capacity>90.
    #224 RISE_PASS 33/43 never asked leftover Ops utilisation / cctv / low-stock.
    #226 RISE_PASS 36/46 never asked leftover Ops sku-by-category / stock /
    # shipment-cost parent-SQL synonyms.
    #228 RISE_PASS 39/49 never asked leftover how-many-SKUs-per-category /
    # inventory-worth / freight-spend parent-SQL.
    """
    got = {str(row.get("id") or "") for row in cases}
    required = (
        CLIMB05_LEFTOVER_L0
        + CLIMB07_RISE_IDS
        + CLIMB08_RISE_IDS
        + CLIMB09_RISE_IDS
        + CLIMB10_RISE_IDS
        + CLIMB11_RISE_IDS
        + CLIMB12_RISE_IDS
        + CLIMB13_RISE_IDS
    )
    return [qid for qid in required if qid not in got]


def decide_phase_a_hold_may_clear(
    *,
    wrong: int,
    n: int,
    pack: str,
    ontology_answered: int,
    bind_answered: int,
    answered: int,
) -> tuple[str, str]:
    """YES only if majority answered path is proven Cortex ontology_plan.

    Does not stamp COMPLETE. Re-baselined packs stay NO for Decision.
    """
    if wrong:
        return "NO", "WRONG>0; HOLD stays"
    if pack != "curated_ceo" or int(n) < int(QUALIFIED_GEN_COVERAGE_CLAIM["n"]):
        return "NO", "pack re-baselined; Decision must accept leftover"
    if answered <= 0:
        return "NO", "zero answered; no majority ontology_plan"
    if ontology_answered * 2 <= answered:
        return (
            "NO",
            f"ontology_plan is not majority of answered "
            f"({ontology_answered}/{answered})",
        )
    if ontology_answered <= bind_answered:
        return (
            "NO",
            f"ontology_plan ({ontology_answered}) does not exceed "
            f"bind_plan ({bind_answered})",
        )
    return (
        "YES",
        f"majority answered path is ontology_plan "
        f"({ontology_answered}/{answered}) WRONG=0",
    )


def build_gen_path_prove_report(
    tallies: dict[str, int],
    *,
    cases: list[dict[str, Any]],
    mode: str,
    url: str | None = None,
    pack: str = "curated_ceo",
) -> dict[str, Any]:
    n = sum(int(v) for v in tallies.values()) or len(cases)
    wrong = int(tallies.get("WRONG") or 0)
    oracle_error = int(tallies.get("ORACLE_ERROR") or 0)
    answered = int(tallies.get("OK") or 0) + int(tallies.get("LAYER") or 0)
    raw = _plan_source_bucket(cases)
    by_source: dict[str, Any] = {}
    for key, count in raw.items():
        by_source[key] = {
            "answered": count,
            "answered_pct": round(100.0 * count / answered, 2) if answered else 0.0,
            "pack_pct": round(100.0 * count / n, 2) if n else 0.0,
        }
    hold, reason = decide_phase_a_hold_may_clear(
        wrong=wrong,
        n=n,
        pack=pack,
        ontology_answered=raw["ontology_plan"],
        bind_answered=raw["bind_plan"],
        answered=answered,
    )
    figure_label = FIGURE_LABEL_LIVE if mode == "live" else FIGURE_LABEL_FIXTURE
    return {
        "kind": "dms.gen_path_prove",
        "ticket": "GEN-PATH-PROVE-01",
        "issue": 199,
        "claim": "measured",
        "pack": pack,
        "mode": mode,
        "url": url,
        "qualified_claim": dict(QUALIFIED_GEN_COVERAGE_CLAIM),
        "n": n,
        "ok": int(tallies.get("OK") or 0),
        "layer": int(tallies.get("LAYER") or 0),
        "abstain": int(tallies.get("ABSTAIN") or 0),
        "wrong": wrong,
        "oracle_error": oracle_error,
        "invalid": int(tallies.get("INVALID") or 0),
        "n_without_invalid": n - int(tallies.get("INVALID") or 0),
        "answered": answered,
        "passed_wrong_zero": wrong == 0,
        "by_plan_source": by_source,
        HOLD_MAY_CLEAR_FIELD: hold,
        "phase_a_hold_may_clear": hold,
        "phase_a_hold_may_clear_reason": reason,
        "categories": pack_category_report(tallies, figure_label=figure_label),
        "cases": cases,
    }


def live_climb_gate(report: dict[str, Any]) -> str | None:
    """None = pass. Reason = FAIL. Offline --prove-path does not use this.

    Dual KEEP_HOLD 17/26 is not climb PASS. #216 21/31 is not climb-07 PASS.
    #218 24/34 is not climb-08 PASS. #220 27/37 is not climb-09 PASS.
    #222 30/40 is not climb-10 PASS. #224 33/43 is not climb-11 PASS.
    #226 36/46 is not climb-12 PASS. #228 39/49 is not climb-13 PASS.
    Rise L0s (climb-06/07/08/09/10/11/12/13) must be ontology_plan.
    Do not shrink the pack to fake a higher percent.
    Do not reintroduce frozen n=26 / n=34 / n=37 / n=40 / n=43 / n=46 / n=49.
    """
    by = report.get("by_plan_source") or {}
    onto_row = by.get("ontology_plan") if isinstance(by, dict) else None
    onto = int((onto_row or {}).get("answered") or 0)
    cases = list(report.get("cases") or [])
    missing06 = leftover_rise_not_ontology(cases)
    missing07 = leftover_ids_not_ontology(cases, CLIMB07_RISE_IDS)
    missing08 = leftover_ids_not_ontology(cases, CLIMB08_RISE_IDS)
    missing09 = leftover_ids_not_ontology(cases, CLIMB09_RISE_IDS)
    missing10 = leftover_ids_not_ontology(cases, CLIMB10_RISE_IDS)
    missing11 = leftover_ids_not_ontology(cases, CLIMB11_RISE_IDS)
    missing12 = leftover_ids_not_ontology(cases, CLIMB12_RISE_IDS)
    missing13 = leftover_ids_not_ontology(cases, CLIMB13_RISE_IDS)
    if int(report.get("n") or 0) <= int(QUALIFIED_GEN_COVERAGE_CLAIM["n"]):
        return "pack n<=26 is frozen 17/26 KEEP_HOLD, not climb-06"
    if missing06:
        return "rise L0s not ontology_plan (dual-flat 17/26): " + ",".join(missing06)
    if missing07:
        return "climb-07 rise L0s not ontology_plan (flat 21/31): " + ",".join(
            missing07
        )
    if missing08:
        return "climb-08 rise L0s not ontology_plan (flat 24/34): " + ",".join(
            missing08
        )
    if missing09:
        return "climb-09 rise L0s not ontology_plan (flat 27/37): " + ",".join(
            missing09
        )
    if missing10:
        return "climb-10 rise L0s not ontology_plan (flat 30/40): " + ",".join(
            missing10
        )
    if missing11:
        return "climb-11 rise L0s not ontology_plan (flat 33/43): " + ",".join(
            missing11
        )
    if missing12:
        return "climb-12 rise L0s not ontology_plan (flat 36/46): " + ",".join(
            missing12
        )
    if missing13:
        return "climb-13 rise L0s not ontology_plan (flat 39/49): " + ",".join(
            missing13
        )
    if onto <= 39:
        return "ontology_plan<=39 is KEEP_HOLD, not climb PASS"
    return None


def _write_prove_report(
    report: dict[str, Any], *, live_climb: bool = False
) -> tuple[int, dict[str, Any]]:
    exact_cases = report.pop("exact_cases", None)
    blob = json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2)
    if "COMPLETE" in blob or "99.95" in blob or "DB-GPT-class" in blob:
        print("FAIL: gen-path prove invented COMPLETE / 99.95")
        return EXIT_FAIL, report
    art = Path(os.environ.get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    (art / "score_gen_path_prove.json").write_text(blob + "\n", encoding="utf-8")
    (art / "score_gen_path_prove_cases.json").write_text(
        json.dumps(report["cases"], indent=2) + "\n", encoding="utf-8"
    )
    if exact_cases is not None:
        (art / "score_gen_path_prove_exact_cases.json").write_text(
            json.dumps(exact_cases, indent=2) + "\n", encoding="utf-8"
        )
    by = report["by_plan_source"]
    print(
        f"{'plan_source':<16} answered answered_pct pack_pct"
    )
    for key in ("ontology_plan", "bind_plan", "other"):
        row = by[key]
        print(
            f"{key:<16} {row['answered']} {row['answered_pct']:.2f} pct "
            f"{row['pack_pct']:.2f} pct"
        )
    print(
        f"WRONG {report['wrong']}  answered {report['answered']}/{report['n']}  "
        f"n_planned {report.get('n_planned', report['n'])}  "
        f"{HOLD_MAY_CLEAR_FIELD}: {report[HOLD_MAY_CLEAR_FIELD]}"
    )
    if report.get("categories"):
        print_category_report(report["categories"])
    print(
        "leftover_l0 "
        + ",".join(CLIMB05_LEFTOVER_L0)
        + f"  (frozen n=26 floor; this pack n={report['n']})"
    )
    print("rise_l0 " + ",".join(CLIMB06_RISE_IDS))
    print("climb07_rise_l0 " + ",".join(CLIMB07_RISE_IDS))
    print("climb08_rise_l0 " + ",".join(CLIMB08_RISE_IDS))
    print("climb09_rise_l0 " + ",".join(CLIMB09_RISE_IDS))
    print("climb10_rise_l0 " + ",".join(CLIMB10_RISE_IDS))
    print("climb11_rise_l0 " + ",".join(CLIMB11_RISE_IDS))
    print("climb12_rise_l0 " + ",".join(CLIMB12_RISE_IDS))
    print("climb13_rise_l0 " + ",".join(CLIMB13_RISE_IDS))
    print(f"reason: {report['phase_a_hold_may_clear_reason']}")
    print("Harness only. Live counts are Platform. HOLD is not an epic stamp.")
    inv = _invalid_round_exit(report.get("round_label"), report.get("reason"))
    if inv is not None:
        return inv, report
    if int(report.get("invalid") or 0):
        print(
            f"FAIL: INVALID={report['invalid']} (engine date or timezone mismatch). "
            "Not WRONG. Not PASS."
        )
        return EXIT_FAIL, report
    if int(report.get("oracle_error") or 0):
        print("FAIL: ORACLE_ERROR>0 (oracle SQL did not run). Not OK, not skipped.")
        return EXIT_FAIL, report
    if not report["passed_wrong_zero"]:
        print("FAIL: WRONG>0 (law).")
        return EXIT_FAIL, report
    if live_climb:
        why = live_climb_gate(report)
        if why:
            print(f"FAIL: {why}")
            return EXIT_FAIL, report
    print("PASS: WRONG=0 measured plan_source labels.")
    return EXIT_PASS, report


def prove_path_offline(oracle_db: Path | None = None) -> int:
    if oracle_db is not None:
        why = require_oracle_db(oracle_db)
        if why:
            print(why)
            return EXIT_CONFIG
    ab = run_ab_curated(oracle_db=oracle_db, compare_rows=True)
    cases = [
        {
            "id": row["id"],
            "verdict": row["generative"],
            "badge": row.get("generative_badge"),
            "plan_source": row.get("plan_source") or "other",
            "expect": row.get("expect"),
            "crag": row.get("crag"),
        }
        for row in ab["cases"]
    ]
    gen = ab["generative"]
    tallies = {
        "OK": int(gen["ok"]),
        "LAYER": int(gen["layer"]),
        "ABSTAIN": int(gen["abstain"]),
        "WRONG": int(gen["wrong"]),
        "ORACLE_ERROR": int(gen.get("oracle_error") or 0),
    }
    report = build_gen_path_prove_report(
        tallies, cases=cases, mode="offline", pack=str(ab.get("pack") or "curated_ceo")
    )
    stamp_round_clock(
        report,
        ab.get("oracle_as_of"),
        ab.get("oracle_as_of_after"),
        ab.get("oracle_timezone"),
    )
    code, _ = _write_prove_report(report)
    if report.get("round_label") == "INVALID":
        return EXIT_FAIL
    if int(ab["wrong"]):
        print("FAIL: exact-match lane WRONG>0 on same pack")
        return EXIT_FAIL
    if int(ab.get("oracle_error") or 0):
        print("FAIL: ORACLE_ERROR>0 (oracle SQL did not run). Not OK, not skipped.")
        return EXIT_FAIL
    if _leftover_l0_unscored(cases):
        print("FAIL: leftover L0s not scored (frozen 17/26 pack)")
        return EXIT_FAIL
    return code


def prove_path_live(url: str, timeout: float, oracle_db: Path | None = None) -> int:
    kind, detail, health = probe_climb_health(url, timeout)
    print(f"GEN-PATH-PROVE-01 host {url}  [{kind}] {detail}")
    climb = health.get("gen_path_climb") if isinstance(health, dict) else None
    if isinstance(climb, dict) and climb.get("rise_l0"):
        print(
            "Studio gen_path_climb "
            f"n={climb.get('n')} frozen_n={climb.get('frozen_n')} "
            "rise_l0=" + ",".join(str(x) for x in climb.get("rise_l0") or [])
        )
    else:
        print(
            "WARN: Studio /health missing gen_path_climb "
            "(pre-#216 split-brain). Harness still asks rise L0s."
        )
    if kind == "blocked":
        print("BLOCKED: cannot reach host. Not a score.")
        return EXIT_BLOCKED
    if kind == "fail":
        print("FAIL: host is not a live governed ask")
        return EXIT_FAIL
    why = require_oracle_db(oracle_db)
    if why:
        print(why)
        return EXIT_CONFIG
    assert oracle_db is not None
    print("-- ask_path=generative (plan_source labels) --")
    try:
        gen_t, gen_cases, gen_clock = score_live_entry(
            url, timeout, ask_path="generative", oracle_db=oracle_db
        )
        print("-- ask_path=exact (WRONG=0 on same pack) --")
        exact_t, exact_cases, exact_clock = score_live_entry(
            url, timeout, ask_path="exact", oracle_db=oracle_db
        )
    except ImportError:
        print("CONFIG: httpx required (DMS .venv). Not a score.")
        return EXIT_CONFIG
    report = build_gen_path_prove_report(
        gen_t, cases=gen_cases, mode="live", url=url
    )
    report["oracle_db"] = str(oracle_db)
    report["schema_version"] = read_schema_version(oracle_db)
    stamp_round_clock(
        report,
        gen_clock.get("oracle_as_of"),
        gen_clock.get("oracle_as_of_after"),
        gen_clock.get("oracle_timezone"),
    )
    report["reason"] = gen_clock.get("reason") or exact_clock.get("reason")
    report["pin_reason"] = gen_clock.get("pin_reason") or exact_clock.get("pin_reason")
    exact_n = sum(int(v) for v in exact_t.values())
    exact_invalid = int(exact_t.get("INVALID") or 0)
    report["n_planned"] = int(
        gen_clock.get("n_planned") or exact_clock.get("n_planned") or _planned_n()
    )
    report["exact_n"] = exact_n
    report["exact_invalid"] = exact_invalid
    report["exact_n_without_invalid"] = exact_n - exact_invalid
    report["exact_cases"] = exact_cases
    if exact_invalid:
        report["passed"] = False
    if (
        exact_clock.get("round_label") == "INVALID"
        or gen_clock.get("round_label") == "INVALID"
    ):
        report["round_label"] = "INVALID"
        report["passed"] = False
    print(
        f"oracle_db={oracle_db} schema_version={report['schema_version']} "
        f"oracle_as_of={report.get('oracle_as_of')} "
        f"oracle_as_of_after={report.get('oracle_as_of_after')} "
        f"timezone={report.get('oracle_timezone')}"
    )
    code, _ = _write_prove_report(report, live_climb=True)
    if report.get("round_label") == "INVALID":
        return EXIT_FAIL
    if int(report.get("exact_invalid") or 0):
        print(
            f"FAIL: exact lane INVALID={report['exact_invalid']} "
            "(engine date or timezone mismatch). Not WRONG. Not PASS."
        )
        return EXIT_FAIL
    if int(exact_t["WRONG"]):
        print("FAIL: exact-match lane WRONG>0 on same pack")
        return EXIT_FAIL
    if int(exact_t.get("ORACLE_ERROR") or 0):
        print("FAIL: exact-match lane ORACLE_ERROR>0")
        return EXIT_FAIL
    if _leftover_l0_unscored(gen_cases):
        print("FAIL: leftover L0s not scored (frozen 17/26 pack)")
        return EXIT_FAIL
    return code


def grid_score_hook(
    url: str,
    timeout: float,
    oracle_db: Path | None = None,
) -> dict[str, Any]:
    """dms#299 row. Same live clock as live/climb/prove. Not the grid runner."""
    why = require_oracle_db(oracle_db)
    if why:
        return {
            "kind": "dms.grid_score_hook",
            "issue": 299,
            "n": 0,
            "n_planned": _planned_n(),
            "n_without_invalid": 0,
            "round_health": 0,
            "invalid": 0,
            "passed": False,
            "reason": None,
            "config": why,
            "cases": [],
        }
    assert oracle_db is not None
    tallies, cases, clock = score_live_entry(url, timeout, oracle_db=oracle_db)
    n = sum(tallies.values())
    invalid_n = int(tallies.get("INVALID") or 0)
    return {
        "kind": "dms.grid_score_hook",
        "issue": 299,
        "n": n,
        "n_planned": int(clock.get("n_planned") or _planned_n()),
        "n_without_invalid": n - invalid_n,
        "round_health": int(clock.get("round_health") or 0),
        "invalid": invalid_n,
        "ok": tallies["OK"],
        "layer": tallies["LAYER"],
        "abstain": tallies["ABSTAIN"],
        "wrong": tallies["WRONG"],
        "oracle_error": tallies["ORACLE_ERROR"],
        "round_label": clock.get("round_label"),
        "reason": clock.get("reason"),
        "pin_reason": clock.get("pin_reason"),
        "passed": (
            tallies["WRONG"] == 0
            and tallies["ORACLE_ERROR"] == 0
            and invalid_n == 0
            and clock.get("round_label") != "INVALID"
        ),
        "cases": cases,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--ab", action="store_true")
    p.add_argument("--climb", action="store_true")
    p.add_argument("--prove-path", action="store_true")
    p.add_argument("--url", default=None)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument(
        "--oracle-db",
        default=None,
        help="DuckDB file the answer ran on. Required in live / --climb / "
        "--prove-path --url. Read-only oracle SQL.",
    )
    args = p.parse_args(argv)
    oracle_db = Path(args.oracle_db) if args.oracle_db else None
    if args.self_check:
        return self_check()
    if args.prove_path:
        if args.climb:
            target = climb_url(args.url)
            if not target:
                print(
                    "CONFIG: --prove-path --climb needs --url or DMS_API_BASE "
                    f"(Platform: {PLATFORM_API}). No laptop default."
                )
                return EXIT_CONFIG
            why = require_oracle_db(oracle_db)
            if why:
                print(why)
                return EXIT_CONFIG
            assert oracle_db is not None
            return prove_path_live(target, args.timeout, oracle_db)
        target = climb_url(args.url)
        if target:
            why = require_oracle_db(oracle_db)
            if why:
                print(why)
                return EXIT_CONFIG
            assert oracle_db is not None
            return prove_path_live(target, args.timeout, oracle_db)
        return prove_path_offline(oracle_db)
    if args.climb:
        target = climb_url(args.url)
        if not target:
            print(
                "CONFIG: --climb needs --url or DMS_API_BASE "
                f"(Platform: {PLATFORM_API}). No laptop default."
            )
            return EXIT_CONFIG
        why = require_oracle_db(oracle_db)
        if why:
            print(why)
            return EXIT_CONFIG
        assert oracle_db is not None
        if args.ab:
            return climb_ab_live(target, args.timeout, oracle_db)
        return climb(target, args.timeout, oracle_db)
    if args.ab:
        return ab_offline(oracle_db)
    if args.live:
        why = require_oracle_db(oracle_db)
        if why:
            print(why)
            return EXIT_CONFIG
        assert oracle_db is not None
        url = (args.url or os.environ.get("DMS_URL") or DEFAULT_URL).rstrip("/")
        return live(url, args.timeout, oracle_db)
    print(
        "usage: python scripts/score_curated.py "
        "--self-check | --live --oracle-db PATH | --ab [--oracle-db PATH] | "
        "--climb --url URL --oracle-db PATH | --climb --ab --url URL --oracle-db PATH "
        "| --prove-path [--oracle-db PATH] | --prove-path --url URL --oracle-db PATH"
    )
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
