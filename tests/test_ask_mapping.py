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
