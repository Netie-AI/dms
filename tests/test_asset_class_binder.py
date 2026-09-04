"""CCA-03 — asset-class encodings certify or abstain; never invent membership."""

from __future__ import annotations

from dms_executor.asset_class_binder import bind_asset_class
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.sense_binder import bind_sense


def test_missing_encoding_abstains() -> None:
    c = bind_asset_class("commercial only, ignore residential")
    assert c["status"] == "ABSTAIN"
    assert c["binding"] is None
    assert "missing encoding" in " ".join(c["reasons"]).lower()


def test_encoding_not_on_landed_dim_abstains() -> None:
    c = bind_asset_class(
        "commercial only",
        encodings={"commercial": ("COMMERCIAL",)},
        landed_dim_values=("RESIDENTIAL",),
    )
    assert c["status"] == "ABSTAIN"
    assert "landed dim" in " ".join(c["reasons"]).lower()


def test_certify_when_encoding_is_on_landed_dim() -> None:
    c = bind_asset_class(
        "commercial only, ignore residential",
        encodings={"commercial": ("COMMERCIAL",), "residential": ("RESIDENTIAL",)},
        landed_dim_values=("COMMERCIAL", "RESIDENTIAL"),
    )
    assert c["status"] == "CERTIFIED"
    assert c["binding"] == "COMMERCIAL"
    assert "COMMERCIAL" in c["evidence"]


def test_missing_encoding_does_not_emit_l0_numbers() -> None:
    sense = bind_sense("lease a warehouse")
    cls = bind_asset_class("lease commercial only")
    env = build_answer_envelope(
        answer_id="a_cca03_miss",
        text="Commercial rent 8953922.60",
        badge="L0_CERTIFIED",
        sql_used="SELECT 1 AS sales_value_myr",
        rows=[{"sales_value_myr": 8953922.60}],
        cascade_path=True,
        constraint_trace=[sense, cls],
        ask_mode="live",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "8953922.60" not in env["text"]
    assert env["constraint_trace"][1]["status"] == "ABSTAIN"
