"""Curated CEO pack: Genie walkthrough analog, fail-closed on green traps."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import judge, load_pack, self_check  # noqa: E402

PACK = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"


def test_pack_has_l0_hits_and_abstain_traps():
    pack = load_pack(PACK)
    assert len(pack["questions"]) >= 13
    expects = {c["expect"] for c in pack["questions"]}
    assert "l0" in expects
    assert "abstain" in expects
    ids = [c["id"] for c in pack["questions"]]
    assert "ops_spend_boundary" in ids
    assert "ops_shipment_cost" in ids
    assert "trap_categoty" in ids


def test_judge_green_trap_is_wrong():
    assert (
        judge(
            {"expect": "abstain"},
            {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"v": 1}]},
        )
        == "WRONG"
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
