"""Curated CEO pack: Genie walkthrough analog, fail-closed on green traps."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import judge, load_pack, self_check  # noqa: E402

PACK = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"


def test_pack_has_l0_hits_and_abstain_traps():
    pack = load_pack(PACK)
    assert len(pack["questions"]) >= 20
    expects = {c["expect"] for c in pack["questions"]}
    assert "l0" in expects
    assert "abstain" in expects
    ids = [c["id"] for c in pack["questions"]]
    assert "ops_spend_boundary" in ids
    assert "ops_shipment_cost" in ids
    assert "trap_categoty" in ids
    assert "cq_spend_total" in ids
    assert "ops_delayed_count" in ids
    assert "cq_cold_storage" in ids
    assert "cq_capacity_how_full" in ids


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


def _norm(q: str) -> str:
    return " ".join(str(q).lower().split())


def test_curated_l0_has_certified_or_vq_backing():
    """Coverage growth is not a YAML wish-list: each L0 ask has L0 SQL."""
    import yaml

    from seed_curated_vqs import ASSETS

    pack = load_pack(PACK)
    cq_path = Path(r"D:\Cortex\packs\dms\semantic\certified_queries.yaml")
    assert cq_path.is_file(), cq_path
    certified = yaml.safe_load(cq_path.read_text(encoding="utf-8")).get("certified") or []
    phrases = set()
    for item in certified:
        phrases.add(_norm(item.get("question") or ""))
        for syn in item.get("synonyms") or []:
            phrases.add(_norm(syn))
    vq = set()
    for asset in ASSETS:
        vq.add(_norm(asset["question"]))
        for syn in asset.get("synonyms") or []:
            vq.add(_norm(syn))
    missing = []
    for case in pack["questions"]:
        if str(case.get("expect") or "").lower() != "l0":
            continue
        q = _norm(case["question"])
        if q not in phrases and q not in vq:
            missing.append(case["id"])
    assert missing == [], missing
