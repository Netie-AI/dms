"""PROVE-CURATED-DIAG-01: score-pack exact ids are an allowlist.

On main the lookup set is the ten base metrics and cq_sku_count misses.
Climb rise and synonym rows stay off the allowlist. The product-lane
contract phrases stay off it too. Not a grant change. Not a verified-query
seed. Not COMPLETE.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dms_executor.demo_grants import DEMO_SPACE_GRANTS
from dms_executor.demo_pack import (
    PACK_METRICS,
    SCORE_PACK_EXACT_IDS,
    lookup_pack_metric,
)

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
)
_SPACES = {
    "finance": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "ops": "dddddddd-dddd-dddd-dddd-dddddddddddd",
}
_BASE_IDS = {
    "spend_by_country",
    "stock_value_by_category",
    "total_spend",
    "cq_capacity_utilisation",
    "cq_low_stock_wh_a",
    "cq_cost_by_destination",
    "cq_cold_storage",
    "cq_capacity_above_90",
    "cq_expired_items",
    "cq_cctv_wh_a",
}
# Climb rise / synonym ids. Not score-pack exact match.
_NOT_EXACT = {
    "cq_sku_count_syn_short",
    "cq_sku_count_syn_label",
    "cq_sales_top5_syn_skus",
    "cq_top3_category_syn_value",
    "cq_sales_top5_syn_sales",
    "cq_top3_category_syn_show",
    "cq_top3_category_syn_plain",
    "cq_top3_category_syn_typo",
    "ops_sku_count_syn_short",
    "ops_sku_count_syn_label",
    "ops_sku_count_by_category_syn",
    "ops_stock_value_syn",
    "ops_shipment_cost_syn",
    "cq_sku_count_by_category_per",
    "cq_stock_value_worth",
    "ops_freight_spend_destination",
    "cq_audit_overdue",
    "cq_sales_top5_value",
    "cq_chemicals_list",
}


def _pack() -> list[dict[str, Any]]:
    data = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    assert len(questions) == 52
    return questions


def _grants(space: str) -> set[str]:
    entry = DEMO_SPACE_GRANTS[_SPACES[space]]
    return set(entry[1])


def test_cq_sku_count_is_not_a_pack_hit() -> None:
    case = next(row for row in _pack() if row["id"] == "cq_sku_count")
    hit = lookup_pack_metric(str(case["question"]), grantable=_grants("finance"))
    assert hit is None


def test_allowlist_is_not_loaded() -> None:
    """The named ids are not metrics."""
    assert "cq_sku_count" in SCORE_PACK_EXACT_IDS
    base_only = {m.metric_id for m in PACK_METRICS}
    assert "cq_sku_count" not in base_only
    assert SCORE_PACK_EXACT_IDS.isdisjoint(base_only)


def test_allowlist_and_rise_ids_miss() -> None:
    by_id = {str(row["id"]): row for row in _pack()}
    for qid in SCORE_PACK_EXACT_IDS | _NOT_EXACT:
        case = by_id[qid]
        hit = lookup_pack_metric(str(case["question"]), grantable=_grants(str(case["space"])))
        assert hit is None, qid


def test_refuse_and_abstain_stay_misses() -> None:
    hits: list[str] = []
    for case in _pack():
        expect = str(case.get("expect") or "").lower()
        if expect not in {"refuse", "abstain"}:
            continue
        hit = lookup_pack_metric(str(case["question"]), grantable=_grants(str(case["space"])))
        if hit is not None:
            hits.append(str(case["id"]))
    assert hits == []


def test_pack_metrics_stay_the_climb_snapshot() -> None:
    assert {m.metric_id for m in PACK_METRICS} == _BASE_IDS
    assert "cq_sku_count_syn_short" not in {m.metric_id for m in PACK_METRICS}
