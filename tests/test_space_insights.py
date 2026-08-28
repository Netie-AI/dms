"""Grain insights on the ask path: compile, conserve, or abstain."""

from __future__ import annotations

from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.space_insights import grain_insights, is_insights_ask, maybe_grain_insights


def test_insights_intent():
    assert is_insights_ask("show me insights")
    assert is_insights_ask("What stands out in this Space?")
    assert maybe_grain_insights("top 5 SKUs") is None


def test_grain_insights_conserves_and_charts(tmp_path):
    db = tmp_path / "demo.duckdb"
    ensure_demo_warehouse(db)
    env = grain_insights(warehouse=db, space_id="space-demo")
    assert_envelope_valid(env)
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["rows"]
    assert env["chart"]["kind"] == "hbar"
    assert "Insights:" in env["text"]
    assert env["sql_used"]
    grouped = sum(float(r["stock_value_myr"]) for r in env["rows"])
    raw = next(v["value"] for v in env["values"] if v["label"] == "stock_value_myr")
    assert abs(grouped - raw) < 1.0
