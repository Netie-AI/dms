"""Curated CEO pack: Genie walkthrough analog, fail-closed on green traps."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import (  # noqa: E402
    ask_error_envelope,
    judge,
    load_oracles,
    load_pack,
    self_check,
)

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"

FOUNDER_L0 = (
    "cq_cold_storage",
    "cq_capacity_above_90",
    "cq_expired_items",
    "cq_chemicals_list",
    "cq_supplier_ranking",
)
VQ03_GAPS = (
    "cq_capacity_utilisation",
    "cq_low_stock_wh_a",
    "ops_shipment_cost",
    "cq_cold_storage",
    "cq_capacity_above_90",
    "cq_expired_items",
    "cq_cctv_wh_a",
)
PLANTED_FAIL_CLOSED = (
    "ops_spend_boundary",
    "trap_last_month",
    "trap_short_paraphrase",
    "trap_alerts_ungranted",
    "trap_high_risk_pending",
    "ops_supplier_rank_boundary",
    "trap_delayed_count",
    "trap_stock_by_bin",
    "trap_how_full_synonym",
)
FOUNDER_REFUSE = (
    "trap_alerts_ungranted",
    "trap_delayed_count",
    "trap_stock_by_bin",
    "trap_how_full_synonym",
)
EXISTING_TRAPS = (
    "ops_spend_boundary",
    "trap_categoty",
    "trap_last_month",
    "trap_short_paraphrase",
)


def test_pack_has_l0_hits_and_abstain_traps():
    pack = load_pack(PACK)
    assert len(pack["questions"]) >= 24
    expects = {c["expect"] for c in pack["questions"]}
    assert "l0" in expects
    assert "abstain" in expects
    assert "refuse" in expects
    ids = [c["id"] for c in pack["questions"]]
    assert "ops_spend_boundary" in ids
    assert "ops_shipment_cost" in ids
    assert "trap_categoty" in ids
    for qid in FOUNDER_L0 + FOUNDER_REFUSE + EXISTING_TRAPS + VQ03_GAPS + PLANTED_FAIL_CLOSED:
        assert qid in ids


def test_l0_cases_have_cortex_sql():
    pack = load_pack(PACK)
    oracles = load_oracles()
    for case in pack["questions"]:
        if case.get("expect") != "l0":
            continue
        sql = str((oracles.get(case["id"]) or {}).get("sql") or "").strip()
        assert sql, f"{case['id']} expect:l0 has no Cortex SQL in oracles.yaml"
        assert "SELECT" in sql.upper()


def test_refuse_cases_are_not_l0():
    pack = load_pack(PACK)
    by_id = {c["id"]: c for c in pack["questions"]}
    for qid in FOUNDER_REFUSE:
        assert by_id[qid]["expect"] == "refuse"


def test_judge_green_trap_is_wrong():
    assert (
        judge(
            {"expect": "abstain"},
            {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"v": 1}]},
        )
        == "WRONG"
    )


def test_judge_green_refuse_is_wrong():
    assert (
        judge(
            {"expect": "refuse"},
            {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"v": 1}]},
        )
        == "WRONG"
    )


def test_judge_grant_refuse_is_abstain():
    class _Resp:
        status_code = 403

    class _Exc(Exception):
        response = _Resp()

    env = ask_error_envelope(_Exc())
    assert env is not None
    assert (
        judge({"expect": "refuse"}, env) == "ABSTAIN"
    )


def test_judge_l0_hit():
    assert (
        judge(
            {"expect": "l0", "min_rows": 1},
            {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"v": 1}]},
        )
        == "OK"
    )


def test_self_check_passes():
    assert self_check() == 0


def _sql_ws(sql: str) -> str:
    return " ".join(str(sql or "").split()).strip()


def test_vq03_gaps_expect_l0_and_have_sql():
    pack = load_pack(PACK)
    oracles = load_oracles()
    by_id = {c["id"]: c for c in pack["questions"]}
    for qid in VQ03_GAPS:
        assert by_id[qid]["expect"] == "l0"
        assert _sql_ws((oracles.get(qid) or {}).get("sql") or "")


def test_planted_traps_stay_fail_closed():
    pack = load_pack(PACK)
    by_id = {c["id"]: c for c in pack["questions"]}
    for qid in PLANTED_FAIL_CLOSED:
        assert by_id[qid]["expect"] in {"abstain", "refuse"}, qid


def test_vq03_pack_sql_matches_oracles():
    """Pack SQL is Cortex certified_queries.yaml, not a second dialect."""
    from dms_executor.demo_pack import PACK_METRICS

    oracles = load_oracles()
    qid_to_metric = {
        "cq_capacity_utilisation": "cq_capacity_utilisation",
        "cq_low_stock_wh_a": "cq_low_stock_wh_a",
        "ops_shipment_cost": "cq_cost_by_destination",
        "cq_cold_storage": "cq_cold_storage",
        "cq_capacity_above_90": "cq_capacity_above_90",
        "cq_expired_items": "cq_expired_items",
        "cq_cctv_wh_a": "cq_cctv_wh_a",
    }
    metrics = {m.metric_id: m for m in PACK_METRICS}
    for qid, metric_id in qid_to_metric.items():
        want = _sql_ws((oracles.get(qid) or {}).get("sql") or "")
        got = _sql_ws(metrics[metric_id].sql)
        assert got == want, qid


def test_vq04_planted_l1_traps_match_pack_constants():
    """Refuse phrases stay exact-equal to curated_ceo; greening them is WRONG."""
    from dms_executor.demo_pack import DELAYED_COUNT_TRAP_Q, HOW_FULL_TRAP_Q

    pack = load_pack(PACK)
    by_id = {c["id"]: c for c in pack["questions"]}
    assert by_id["trap_how_full_synonym"]["question"] == HOW_FULL_TRAP_Q
    assert by_id["trap_delayed_count"]["question"] == DELAYED_COUNT_TRAP_Q
    assert by_id["trap_how_full_synonym"]["expect"] == "refuse"
    assert by_id["trap_delayed_count"]["expect"] == "refuse"
    l1 = {"badge": "L1_GOVERNED_METRIC", "abstained": False, "rows": [{"v": 1}]}
    assert judge(by_id["trap_how_full_synonym"], l1) == "WRONG"
    assert judge(by_id["trap_delayed_count"], l1) == "WRONG"
    refused = {"badge": "ABSTAIN", "abstained": True, "rows": []}
    assert judge(by_id["trap_how_full_synonym"], refused) == "ABSTAIN"
    assert judge(by_id["trap_delayed_count"], refused) == "ABSTAIN"
