"""Served pack lookup is the ten code-constant base metrics only.

PROVE-CURATED-DIAG-01 (#355) added five score-pack ids read from
curated_ceo. DEMO-PACK-LAZY-01 (#386) takes them back off the served path:
served code never reads the score pack, and those five ids take the normal
lanes like any other question. Not a grant change. Not COMPLETE.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dms_executor.demo_grants import DEMO_SPACE_GRANTS
from dms_executor.demo_pack import PACK_METRICS, lookup_pack_metric

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
_FIVE_IDS = {
    "cq_sku_count",
    "cq_sales_top3_volume",
    "cq_sku_count_by_category",
    "cq_supplier_ranking",
    "trap_categoty",
}
# Climb rise / synonym ids. Not exact match.
_NOT_EXACT = _FIVE_IDS | {
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


def test_five_ids_and_rise_ids_miss_served_lookup() -> None:
    by_id = {str(row["id"]): row for row in _pack()}
    for qid in _NOT_EXACT:
        case = by_id[qid]
        hit = lookup_pack_metric(str(case["question"]), grantable=_grants(str(case["space"])))
        assert hit is None, qid


def test_base_phrases_still_hit() -> None:
    for m in PACK_METRICS:
        hit = lookup_pack_metric(m.question, grantable=set(m.tables))
        assert hit is not None and hit.metric_id == m.metric_id


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
