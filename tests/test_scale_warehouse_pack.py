"""SCALE-WAREHOUSE-01 (#237) design pack is a roadmap, not a live 1PB estate."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "docs" / "schemas" / "scale_warehouse.yaml"
DOC_PATH = ROOT / "docs" / "SCALE_WAREHOUSE.md"
GRAINS_232 = ("sku", "supplier", "plant", "lane", "day")
FACT_PARTITION_TB = ("day", "plant_id")


def _pack() -> dict:
    raw = yaml.safe_load(PACK_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def test_pack_marks_not_live_1pb_and_not_complete() -> None:
    pack = _pack()
    assert pack["id"] == "SCALE-WAREHOUSE-01"
    assert pack["ticket"] == 237
    assert pack["parent_epic"] == 178
    assert pack["maps_grains_from"] == 232
    assert pack["status"] == "ROADMAP_NOT_LIVE"
    assert pack["completeness"] == "NOT_COMPLETE"
    assert pack["live_claim"] is False
    assert pack["petabyte_live"] is False
    assert pack["live_1pb"] is False
    assert pack["not_live_1pb"] is True
    assert pack["design_horizon"] == "TB"
    assert pack["roadmap_horizon"] == "PB"


def test_doc_states_not_live_1pb() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "NOT LIVE 1PB" in text
    assert "ROADMAP_NOT_LIVE" in text
    assert "Fixes #237" not in text  # claim lives on the PR, not the doc
    assert "\n**Status:** `COMPLETE`" not in text
    assert "petabyte-class production" not in text.lower()
    # Negated prose may mention the forbidden claim. The stamp must stay roadmap.
    assert "**Status:** `ROADMAP_NOT_LIVE`" in text


def test_founder_lake_remount_requires_platform_go() -> None:
    remount = _pack()["founder_lake_remount"]
    assert remount["required"] is False
    assert remount["requires_platform_go"] is True


def test_maps_issue_232_grains_with_partition_acl_cardinality() -> None:
    grains = _pack()["grains"]
    assert tuple(grains) == GRAINS_232
    for name in GRAINS_232:
        g = grains[name]
        assert g["issue_232_name"]
        assert g["object"]
        assert g["warehouse_relation"]
        assert g["grain_key"]
        card = g["cardinality"]
        assert card["grain"]
        assert int(card["tb_design_envelope"]) > 0
        assert int(card["pb_roadmap_envelope"]) >= int(card["tb_design_envelope"])
        assert card["pb_roadmap_envelope_live"] is False
        part = g["partition"]
        assert "fact_tb" in part
        assert "fact_pb_roadmap" in part
        acl = g["space_acl"]
        assert acl["inherits"] == "DR-0002"
        assert acl["table_scope"] in {"company", "space", "follows_fact_table"}
        assert g["join_from_facts"]


def test_facts_define_tb_partition_keys() -> None:
    facts = _pack()["facts"]
    for name in ("fact_movement", "fact_shipment"):
        keys = list(facts[name]["partition_keys_tb"])
        for required in FACT_PARTITION_TB:
            assert required in keys, f"{name} missing TB partition {required}"
        assert facts[name]["partition_keys_live"] == []
        assert facts[name]["space_acl"]["table_scope"] == "space"
    lot = facts["fact_lot"]
    assert "plant_id" in lot["partition_keys_tb"]
    assert lot["space_acl"]["table_scope"] == "company"


def test_space_acl_is_predicate_not_storage_fork() -> None:
    acl = _pack()["space_acl"]
    assert acl["law"] == "DR-0002"
    assert acl["storage_partition_by_space_id"] is False
    assert acl["company_default"] == "union_of_space_grants"
    assert "alerts" in acl["ungranted_stays_ungranted"]
    finance = acl["demo_spaces"]["Finance"]["grains_readable"]
    ops = acl["demo_spaces"]["Warehouse Ops"]["grains_readable"]
    assert "supplier" in finance and "supplier" not in ops
    assert "lane" in ops and "lane" not in finance
    for grain in GRAINS_232:
        assert grain in finance or grain in ops


def test_plant_is_not_aliased_to_live_locations() -> None:
    plant = _pack()["grains"]["plant"]
    assert plant["live_relation"] is None
    assert plant["live_shape"] == "missing"
    assert "location" not in plant["grain_key"]
    assert "alias_plant_to_location" in _pack()["must_not"]


def test_cardinality_summary_is_design_not_measured() -> None:
    summary = _pack()["cardinality_summary"]
    assert summary["measured_live"] is False
    for name in GRAINS_232:
        row = summary["grains"][name]
        assert int(row["tb"]) > 0
        assert int(row["pb_roadmap"]) >= int(row["tb"])
