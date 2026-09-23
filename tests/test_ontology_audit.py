"""ONTOLOGY-AUDIT-01 — include / exclude / unsure on the ask envelope.

Happy-path numbers carry a receipt. Invented totals demote. COMPLETE is illegal.
Does not stamp epic COMPLETE. Does not touch FreeRoute or GEN-03 ask_path.
"""

from __future__ import annotations

import pytest
from cortex_client.models import AskResponse
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor import map_ask_response_to_envelope
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_map_happy_path_carries_include_exclude_unsure() -> None:
    resp = AskResponse.model_validate(
        {
            "answer": "Outbound revenue was 80.50.",
            "audit_id": "aud_oa01",
            "route": "certified_metric",
            "provenance": {"badge": "certified_metric", "layer": "L0"},
            "sql_used": (
                "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS revenue_myr "
                "FROM transactions WHERE txn_type = 'OUT'"
            ),
            "rows": [{"revenue_myr": 80.5}],
            "drillthrough_token": "dt_oa01_token",
        }
    )
    env = map_ask_response_to_envelope(resp, session_id="ses_oa01")
    assert env["badge"] == "L0_CERTIFIED"
    assert env["abstained"] is False
    rec = env["audit_receipt"]
    assert rec["include"]["status"] == "rows"
    assert rec["include"]["row_count"] == 1
    assert rec["include"]["rows"][0]["revenue_myr"] == 80.5
    assert rec["exclude"]["status"] == "filters"
    assert any("OUT" in r["detail"] for r in rec["exclude"]["reasons"])
    assert rec["unsure"]["status"] == "none"
    assert rec["unsure"]["badge"] == "L0_CERTIFIED"
    assert "complete" not in rec["include"]["status"].lower()
    assert_envelope_valid(env)


def test_map_unsure_flag_abstains() -> None:
    resp = AskResponse.model_validate(
        {
            "answer": "Maybe 999.00.",
            "audit_id": "aud_oa01_unsure",
            "route": "session",
            "provenance": {"badge": "session"},
            "sql_used": "SELECT 999",
            "rows": [{"n": 999.0}],
            "unsure": True,
        }
    )
    env = map_ask_response_to_envelope(resp, session_id="ses_oa01_u")
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["audit_receipt"]["unsure"]["status"] == "abstain"
    assert env["audit_receipt"]["include"]["why"].startswith("N/A:")
    assert_envelope_valid(env)


def test_caller_complete_exclude_is_not_stamped() -> None:
    env = build_answer_envelope(
        answer_id="a_oa01_complete",
        text="Total is 10.00.",
        badge="L2_VALIDATED",
        values=[{"id": "v0", "value": 10.0, "label": "qty"}],
        sql_used="SELECT qty FROM t WHERE qty > 0",
        rows=[{"qty": 10.0}],
        exclude_reasons=["COMPLETE"],
        ask_mode="live",
    )
    assert env["abstained"] is False
    rec = env["audit_receipt"]
    assert rec["exclude"]["status"] == "filters"
    assert rec["exclude"]["reasons"][0]["detail"] != "COMPLETE"
    assert "qty > 0" in rec["exclude"]["reasons"][0]["detail"]
    assert_envelope_valid(env)


def test_missing_cell_is_not_padded_to_zero() -> None:
    env = build_answer_envelope(
        answer_id="a_oa01_pad",
        text="Total is 30.00.",
        badge="L2_VALIDATED",
        values=[{"id": "v0", "value": 30.0, "label": "qty"}],
        sql_used="SELECT sku, qty FROM t",
        rows=[{"sku": "A", "qty": 10.0}, {"sku": "B"}],
        ask_mode="live",
    )
    assert env["abstained"] is True
    assert env["rows"] == []
    assert any("ONTOLOGY-AUDIT-01" in a for a in env["assumptions"])
    assert_envelope_valid(env)


def test_filter_clause_on_aggregate() -> None:
    env = build_answer_envelope(
        answer_id="a_oa01_filter",
        text="Overdue count is 3.00.",
        badge="L1_GOVERNED_METRIC",
        values=[{"id": "v0", "value": 3.0, "label": "n"}],
        sql_used=(
            "SELECT COUNT(*) FILTER (WHERE last_audit_date < CURRENT_DATE - 90) "
            "AS n FROM suppliers"
        ),
        rows=[{"n": 3.0}],
        ask_mode="live",
    )
    rec = env["audit_receipt"]
    assert rec["exclude"]["status"] == "filters"
    assert rec["exclude"]["reasons"][0]["kind"] == "filter"
    assert "last_audit_date" in rec["exclude"]["reasons"][0]["detail"]
    assert env["abstained"] is False
    assert_envelope_valid(env)


def test_demo_ask_http_receipt_on_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())
    body = client.post(
        "/v1/chat/ask",
        json={"question": "What was total revenue?", "space_id": FINANCE},
    ).json()
    assert_envelope_valid(body)
    assert body["badge"] == "L2_VALIDATED"
    rec = body["audit_receipt"]
    assert rec["include"]["status"] == "rows"
    assert rec["include"]["row_count"] == len(body["rows"]) == 1
    assert rec["exclude"]["status"] == "filters"
    assert any("outbound" in r["detail"].lower() for r in rec["exclude"]["reasons"])
    assert rec["unsure"]["status"] == "none"
    get_settings.cache_clear()
