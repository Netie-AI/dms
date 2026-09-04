"""CCA-02 — sense binder certify/abstain; no confident L0 on ambiguous sense."""

from __future__ import annotations

from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.sense_binder import bind_sense


def test_certify_lease() -> None:
    c = bind_sense("lease a warehouse in Johor")
    assert c["status"] == "CERTIFIED"
    assert c["binding"] == "lease"
    assert c["type"] == "sense"
    assert "lease" in c["evidence"]


def test_certify_buy() -> None:
    c = bind_sense("buy the building")
    assert c["status"] == "CERTIFIED"
    assert c["binding"] == "buy"


def test_certify_housing_rent_does_not_also_count_as_lease() -> None:
    c = bind_sense("housing rent in KL")
    assert c["status"] == "CERTIFIED"
    assert c["binding"] == "housing-rent"


def test_rental_synonym_is_lease() -> None:
    c = bind_sense("rental across the park")
    assert c["status"] == "CERTIFIED"
    assert c["binding"] == "lease"


def test_abstain_ambiguous_lease_vs_buy() -> None:
    c = bind_sense("lease or buy this site")
    assert c["status"] == "ABSTAIN"
    assert c["binding"] is None
    assert "ambiguous" in " ".join(c["reasons"]).lower()


def test_abstain_missing_vocabulary() -> None:
    c = bind_sense("what is total revenue")
    assert c["status"] == "ABSTAIN"
    assert c["binding"] is None
    assert "missing vocabulary" in " ".join(c["reasons"]).lower()


def test_ambiguous_sense_does_not_emit_l0_numbers() -> None:
    sense = bind_sense("lease or buy this site")
    env = build_answer_envelope(
        answer_id="a_cca02_amb",
        text="Top 3: ELECTRONICS 8953922.60",
        badge="L0_CERTIFIED",
        sql_used="SELECT 1 AS sales_value_myr",
        rows=[{"category": "ELECTRONICS", "sales_value_myr": 8953922.60}],
        cascade_path=True,
        constraint_trace=[sense],
        ask_mode="live",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env["values"] == []
    assert "8,953,922.60" not in env["text"]
    assert "8953922.60" not in env["text"]
    assert env["constraint_trace"][0]["status"] == "ABSTAIN"


def test_missing_sense_does_not_emit_l0_numbers() -> None:
    sense = bind_sense("what is total revenue")
    env = build_answer_envelope(
        answer_id="a_cca02_miss",
        text="Revenue is 123.2M",
        badge="L0_CERTIFIED",
        sql_used="SELECT 123.2 AS revenue",
        rows=[{"revenue": 123.2}],
        cascade_path=True,
        constraint_trace=[sense],
        ask_mode="live",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "123.2" not in env["text"]
