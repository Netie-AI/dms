"""CCA-01 — constraint schema + envelope stage-trace gating.

Does not invent geo/class encodings. Later CERTIFIED after ABSTAIN must fail.
"""

from __future__ import annotations

import pytest
from dms_executor.constraint_cascade import (
    ConstraintSchemaError,
    parse_trace,
    refuse_missing_schema,
)
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope


def _stage(
    stage: str,
    status: str,
    *,
    candidate: str = "x",
    binding: str | None = "bound",
) -> dict:
    return {
        "constraint_id": f"c_{stage}",
        "type": stage,
        "candidate": candidate,
        "binding": binding,
        "evidence": ["pack"],
        "status": status,
        "reasons": [],
    }


def test_schema_missing_raises() -> None:
    with pytest.raises(ConstraintSchemaError, match="schema missing"):
        parse_trace(None)


def test_schema_requires_fields() -> None:
    with pytest.raises(ConstraintSchemaError, match="schema missing fields"):
        parse_trace([{"type": "sense", "status": "CERTIFIED"}])


def test_later_certified_after_abstain_is_illegal() -> None:
    with pytest.raises(ConstraintSchemaError, match="must not be CERTIFIED"):
        parse_trace(
            [
                _stage("sense", "ABSTAIN", binding=None),
                _stage("asset_class", "CERTIFIED", candidate="commercial"),
            ]
        )


def test_missing_prior_stage_cannot_certify_later() -> None:
    with pytest.raises(ConstraintSchemaError, match="must not be CERTIFIED"):
        parse_trace([_stage("geo", "CERTIFIED", candidate="SEA")])


def test_certified_priors_allow_next_stage() -> None:
    trace = parse_trace(
        [
            _stage("sense", "CERTIFIED", candidate="lease"),
            _stage("asset_class", "CERTIFIED", candidate="commercial"),
        ]
    )
    assert trace[0]["status"] == "CERTIFIED"
    assert trace[1]["type"] == "asset_class"


def test_cascade_path_missing_schema_refuses_before_l0() -> None:
    env = build_answer_envelope(
        answer_id="a_cca_miss",
        text="Top 3: ELECTRONICS 8953922.60",
        badge="L0_CERTIFIED",
        sql_used="SELECT 1",
        rows=[{"category": "ELECTRONICS", "sales_value_myr": 8953922.60}],
        cascade_path=True,
        constraint_trace=None,
        ask_mode="live",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env["values"] == []
    assert "schema is missing" in env["text"].lower()
    assert env["constraint_trace"] == []
    miss = refuse_missing_schema()
    assert "before L0" in miss["assumptions"][0]


def test_envelope_emits_trace_when_schema_holds() -> None:
    raw = [_stage("sense", "CERTIFIED", candidate="lease")]
    env = build_answer_envelope(
        answer_id="a_cca_ok",
        text="Need a geo binding before a certified total.",
        badge="ABSTAIN",
        abstained=True,
        cascade_path=True,
        constraint_trace=raw,
        ask_mode="live",
    )
    assert_envelope_valid(env)
    assert env["constraint_trace"][0]["type"] == "sense"
    assert env["constraint_trace"][0]["status"] == "CERTIFIED"


def test_illegal_trace_on_envelope_does_not_stay_certified() -> None:
    env = build_answer_envelope(
        answer_id="a_cca_bad",
        text="ELECTRONICS 8,953,922.60",
        badge="L0_CERTIFIED",
        sql_used="SELECT 1 AS sales_value_myr",
        rows=[{"category": "ELECTRONICS", "sales_value_myr": 8953922.60}],
        cascade_path=True,
        constraint_trace=[
            _stage("sense", "ABSTAIN", binding=None),
            _stage("sql", "CERTIFIED"),
        ],
        ask_mode="live",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "8,953,922.60" not in env["text"]
    assert env["rows"] == []
