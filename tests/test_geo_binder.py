"""CCA-04 — SEA geo pack certify or abstain; never invent countries."""

from __future__ import annotations

from dms_executor.asset_class_binder import bind_asset_class
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.geo_binder import bind_geo_region
from dms_executor.sense_binder import bind_sense


def test_missing_sea_pack_abstains() -> None:
    c = bind_geo_region("rental across SEA")
    assert c["status"] == "ABSTAIN"
    assert c["binding"] is None
    assert "missing sea membership" in " ".join(c["reasons"]).lower()


def test_search_is_not_sea() -> None:
    c = bind_geo_region("search the catalog")
    assert c["status"] == "ABSTAIN"
    assert "no sea region" in " ".join(c["reasons"]).lower()


def test_pack_member_not_on_landed_dim_abstains() -> None:
    c = bind_geo_region(
        "across SEA",
        region_members={"SEA": ("MY", "TH")},
        landed_dim_values=("MY",),
    )
    assert c["status"] == "ABSTAIN"
    assert "landed dim" in " ".join(c["reasons"]).lower()


def test_proposed_member_not_on_pack_abstains() -> None:
    c = bind_geo_region(
        "across SEA",
        region_members={"SEA": ("MY",)},
        landed_dim_values=("MY", "TH"),
        proposed_members=("MY", "TH"),
    )
    assert c["status"] == "ABSTAIN"
    assert "will not invent" in " ".join(c["reasons"]).lower()


def test_certify_when_pack_is_on_landed_dim() -> None:
    c = bind_geo_region(
        "rental across SEA",
        region_members={"SEA": ("MY",)},
        landed_dim_values=("MY", "SG"),
    )
    assert c["status"] == "CERTIFIED"
    assert c["binding"] == "MY"
    assert "MY" in c["evidence"]


def test_missing_sea_pack_does_not_emit_l0_numbers() -> None:
    sense = bind_sense("rental across SEA")
    cls = bind_asset_class(
        "rental commercial only",
        encodings={"commercial": ("COMMERCIAL",)},
        landed_dim_values=("COMMERCIAL",),
    )
    geo = bind_geo_region("rental across SEA")
    env = build_answer_envelope(
        answer_id="a_cca04_miss",
        text="SEA commercial rent 8953922.60",
        badge="L0_CERTIFIED",
        sql_used="SELECT 1 AS sales_value_myr",
        rows=[{"sales_value_myr": 8953922.60}],
        cascade_path=True,
        constraint_trace=[sense, cls, geo],
        ask_mode="live",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "8953922.60" not in env["text"]
    assert env["constraint_trace"][2]["status"] == "ABSTAIN"
