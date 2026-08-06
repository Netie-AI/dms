"""AskResponse facade + live envelope mapping."""

from __future__ import annotations

from cortex_client.models import AskResponse
from dms_executor import map_ask_response_to_envelope
from dms_executor.envelope import assert_envelope_valid


def test_ask_response_flattens_provenance():
    raw = {
        "answer": "Total revenue was 100.",
        "audit_id": "aud_1",
        "route": "governed_metric",
        "provenance": {"badge": "governed_metric", "layer": "L1", "assumptions": "outbound only"},
        "sql_used": "SELECT 1",
        "rows": [{"sku": "A", "revenue_myr": 10.0}],
        "suggestions": ["Try top 5"],
    }
    resp = AskResponse.model_validate(raw)
    assert resp.badge == "governed_metric"
    assert resp.receipt_id == "aud_1"
    assert resp.rows[0]["sku"] == "A"
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_1")
    assert env["ask_mode"] == "live"
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["sql_used"] == "SELECT 1"
    assert env["chart"]["kind"] == "hbar"
    assert env.get("drillthrough_token") is None
    assert_envelope_valid(env)


def test_chart_spec_from_cortex_maps_to_envelope_chart():
    """INS-03: Cortex chart_spec survives mapping for SimpleChart."""
    resp = AskResponse.model_validate(
        {
            "answer": (
                "Top sales by category:\n  • Electronics: 12\n  • Food: 5\n\n"
                "Insights:\n- 2 row(s) in this result.\n"
                "- Highest qty: 12 (Electronics).\n"
                "- Lowest qty: 5 (Food)."
            ),
            "audit_id": "aud_ins03",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": "SELECT category, qty FROM t",
            "rows": [
                {"category": "Electronics", "qty": 12},
                {"category": "Food", "qty": 5},
            ],
            "chart_spec": {
                "type": "bar",
                "x_label": "category",
                "y_label": "qty",
                "title": "stock by category",
                "data": [
                    {"name": "Electronics", "value": 12.0},
                    {"name": "Food", "value": 5.0},
                ],
            },
        }
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_ins")
    assert env["chart"]["kind"] == "bar"
    assert env["chart"]["x"] == "category"
    assert env["chart"]["y"] == "qty"
    assert "Insights:" in env["text"]
    assert "Electronics" in env["text"]
    assert "12" in env["text"]
    assert env["rows"] and len(env["rows"]) == 2
    assert_envelope_valid(env)


def test_chart_spec_bignum_and_line_shapes():
    bignum = AskResponse.model_validate(
        {
            "answer": "Total was 42.",
            "audit_id": "aud_bn",
            "route": "certified_metric",
            "provenance": {"badge": "certified_metric"},
            "sql_used": "SELECT 42",
            "rows": [{"revenue_myr": 42}],
            "chart_spec": {
                "type": "bignum",
                "value": 42,
                "label": "REVENUE MYR",
                "title": "total",
                "data": [],
            },
        }
    )
    env_b = map_ask_response_to_envelope(bignum)
    assert env_b["chart"]["kind"] == "bignum"
    assert env_b["chart"]["value"] == 42
    assert_envelope_valid(env_b)

    line = AskResponse.model_validate(
        {
            "answer": "Monthly volume.\n\nInsights:\n- 2 row(s) in this result.",
            "audit_id": "aud_ln",
            "route": "query_skill",
            "provenance": {"badge": "query_skill"},
            "sql_used": "SELECT order_date, qty FROM t",
            "rows": [
                {"order_date": "2024-01-01", "qty": 1},
                {"order_date": "2024-02-01", "qty": 3},
            ],
            "chart_spec": {
                "type": "line",
                "x_label": "order_date",
                "y_label": "qty",
                "title": "monthly",
                "data": [
                    {"name": "2024-01-01", "value": 1.0},
                    {"name": "2024-02-01", "value": 3.0},
                ],
            },
        }
    )
    env_l = map_ask_response_to_envelope(line)
    assert env_l["chart"]["kind"] == "line"
    assert env_l["chart"]["x"] == "order_date"
    assert_envelope_valid(env_l)


def test_null_chart_spec_falls_back_to_row_hbar():
    """INS-03: null chart_spec uses row inference; Cortex bar spec wins when present."""
    resp = AskResponse.model_validate(
        {
            "answer": "By category.",
            "audit_id": "aud_null",
            "route": "query_skill",
            "provenance": {"badge": "query_skill"},
            "sql_used": "SELECT category, qty FROM t",
            "rows": [{"category": "A", "qty": 3}, {"category": "B", "qty": 1}],
            "chart_spec": None,
        }
    )
    env = map_ask_response_to_envelope(resp)
    assert env["chart"]["kind"] == "hbar"
    assert env["chart"]["x"] == "category"
    assert env["chart"]["y"] == "qty"
    assert_envelope_valid(env)


def test_null_chart_spec_no_chart_when_rows_not_inferrable():
    """INS-03: no chart shell when Cortex omits spec and rows lack cat+measure."""
    resp = AskResponse.model_validate(
        {
            "answer": "One label row.",
            "audit_id": "aud_no_chart",
            "route": "query_skill",
            "provenance": {"badge": "query_skill"},
            "sql_used": "SELECT label FROM t",
            "rows": [{"label": "only"}],
            "chart_spec": None,
        }
    )
    env = map_ask_response_to_envelope(resp)
    assert env.get("chart") is None
    assert_envelope_valid(env)


def test_map_empty_sql_result_demotes_hard_rule_12():
    """Live ask mapping: executed SQL + zero rows must not stay L2_VALIDATED."""
    resp = AskResponse.model_validate(
        {
            "answer": "Total for BETA is 0.00.",
            "audit_id": "aud_empty",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": "SELECT SUM(sales_value_myr) AS t FROM sales WHERE sku = 'BETA'",
            "rows": [],
        }
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_empty")
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert any("hard rule 12" in a for a in env["assumptions"])
    assert_envelope_valid(env)


def test_live_envelope_forwards_drillthrough_token():
    resp = AskResponse.model_validate(
        {
            "answer": "Total was 42.",
            "audit_id": "aud_dt",
            "route": "certified_metric",
            "provenance": {"badge": "certified_metric", "layer": "L0"},
            "sql_used": "SELECT 42",
            "rows": [{"n": 42.0}],
            "drillthrough_token": "dt_live_token",
            "contributing_sources": [{"ref_id": "src_1", "contribution_pct": 100}],
        }
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_1")
    assert env["drillthrough_token"] == "dt_live_token"
    assert env["ask_mode"] == "live"
    assert_envelope_valid(env)
    resp = AskResponse.model_validate(
        {
            "answer": "Cannot answer.",
            "audit_id": "aud_2",
            "route": "abstain",
            "provenance": {"badge": "abstain", "layer": "L0"},
        }
    )
    assert resp.abstained is True
    env = map_ask_response_to_envelope(resp)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert_envelope_valid(env)
