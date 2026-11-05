"""CHAT-01. Plan, edit, clarify, confirm. No SQL on the plan route. Nothing PASS."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.insights import InsightsError
from dms_api.app import create_app
from dms_core.studio_chat_turn import (
    NOT_PROVIDED,
    StudioChat,
    followups_from_envelope,
    project_studio_turn,
)
from fastapi.testclient import TestClient

_ONTOLOGY = {
    "ok": True,
    "phase": "ontology",
    "intent": "how many skus",
    "ontology": {
        "locations": [
            {
                "id": "inventory",
                "where": {"table": "inventory", "primary_key": "sku"},
            }
        ],
        "joins": [
            {
                "id": "inventory_location",
                "from": "inventory",
                "to": "locations",
                "from_property": "location_id",
                "to_property": "id",
            }
        ],
        "metrics": [{"id": "sku_count", "kind": "metric"}],
    },
    "law": "Ontology first.",
    "live_5000_ci": False,
}

_PLAN = {
    "query_plan": {"measure": "sku_count", "group_by": [], "filters": []},
    "ontology": _ONTOLOGY["ontology"],
}


def _allow(**kwargs: Any) -> ComplianceDecision:
    return ComplianceDecision(
        allowed=True, reason="ok", action=str(kwargs.get("action") or "studio.chat_plan")
    )


class _FakeCortex:
    base_url = "http://cortex.test"
    api_key = "ov_test_chat_01"

    def __init__(self, payloads: list[dict[str, Any]] | None = None) -> None:
        self.payloads = list(payloads or [_ONTOLOGY])
        self.ontology_qs: list[str] = []
        self.asks: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def insights_ontology(self, q: str) -> dict[str, Any]:
        self.ontology_qs.append(q)
        if self.error:
            raise self.error
        idx = min(len(self.ontology_qs) - 1, len(self.payloads) - 1)
        return dict(self.payloads[idx])

    def insights_ask(self, **kwargs: Any) -> dict[str, Any]:
        self.asks.append(kwargs)
        raise AssertionError("plan must not call insights_ask")


def _client(fake: _FakeCortex, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("dms_api.routes.studio_chat.compliance_gate", _allow)
    app = create_app()
    app.state.cortex = fake
    return TestClient(app)


def test_plan_shown_and_confirm_runs() -> None:
    chat = StudioChat()
    preview = chat.plan("how many skus", _PLAN)
    assert preview["plan"]["provided"] is True
    assert preview["plan"]["query_plan"]["measure"] == "sku_count"
    assert preview["sql_ran"] is False
    assert chat.executions == 0
    assert chat.confirm() == "how many skus"
    assert chat.executions == 1
    assert chat.executed_question == "how many skus"


def test_edit_replans_without_executing() -> None:
    chat = StudioChat()
    chat.plan("how many skus", _PLAN)
    chat.edit("stock value by category")
    assert chat.preview is None
    assert chat.executions == 0
    with pytest.raises(RuntimeError, match="confirm before plan"):
        chat.confirm()
    edited = {
        "query_plan": {"measure": "stock_value_myr", "group_by": [["product", "category"]]},
    }
    preview = chat.plan("stock value by category", edited)
    assert preview["plan"]["query_plan"]["measure"] == "stock_value_myr"
    assert chat.plan_calls == 2
    assert chat.executions == 0
    assert chat.confirm() == "stock value by category"
    assert chat.executions == 1


def test_clarify_blocks_until_answered() -> None:
    payload = {
        "route": "needs_clarification",
        "answer": "Which warehouse?",
        "suggestions": ["Show warehouse capacity utilisation"],
        "query_plan": {"measure": "sku_count"},
    }
    chat = StudioChat()
    preview = chat.plan("stock in the warehouse", payload)
    assert preview["clarify"]["blocks"] is True
    assert preview["clarify"]["text"] == "Which warehouse?"
    with pytest.raises(RuntimeError, match="clarify unanswered"):
        chat.confirm()
    assert chat.executions == 0
    chat.answer("   ")
    with pytest.raises(RuntimeError, match="clarify unanswered"):
        chat.confirm()
    chat.answer("WH-A")
    assert chat.confirm() == "stock in the warehouse\nWH-A"
    assert chat.executions == 1


def test_missing_plan_field_is_not_provided_by_cortex() -> None:
    preview = project_studio_turn(
        {
            "phase": "ontology",
            "answer": "Ontology first.",
            "law": "Ontology first.",
            "ontology": _ONTOLOGY["ontology"],
        }
    )
    assert preview["plan"]["provided"] is False
    assert preview["plan"]["text"] == NOT_PROVIDED
    assert preview["plan"]["query_plan"] is None
    assert preview["clarify"]["provided"] is False
    assert preview["clarify"]["text"] == NOT_PROVIDED
    assert preview["clarify"]["blocks"] is False
    assert preview["ontology"]["tables"]["items"] == ["inventory"]
    assert preview["ontology"]["joins"]["items"][0]["from"] == "inventory"
    assert preview["ontology"]["metrics"]["items"] == ["sku_count"]
    # suggestions are follow-ups, not a fabricated clarify question
    assert project_studio_turn({"suggestions": ["Try top 5"]})["clarify"]["blocks"] is False


def test_followups_copy_suggestions_only() -> None:
    assert followups_from_envelope({"suggestions": ["Top 5 SKUs"]})["items"] == ["Top 5 SKUs"]
    assert followups_from_envelope({"suggestions": []})["provided"] is True
    assert followups_from_envelope({"suggestions": []})["items"] == []
    missing = followups_from_envelope({"text": "12"})
    assert missing["provided"] is False
    assert missing["text"] == NOT_PROVIDED


def test_plan_route_copies_ontology_and_does_not_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeCortex()
    res = _client(fake, monkeypatch).post(
        "/v1/studio/chat/plan", json={"question": "how many skus"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["sql_ran"] is False
    assert body["plan"]["text"] == NOT_PROVIDED
    assert body["ontology"]["tables"]["items"] == ["inventory"]
    assert body["ontology"]["metrics"]["items"] == ["sku_count"]
    assert fake.ontology_qs == ["how many skus"]
    assert fake.asks == []
    src = inspect.getsource(__import__("dms_api.routes.studio_chat", fromlist=["studio_chat"]))
    assert "insights_ask" not in src
    assert "live_ask" not in src


def test_edit_replans_on_the_route(monkeypatch: pytest.MonkeyPatch) -> None:
    second = {
        "ontology": {
            "locations": [{"id": "inventory", "where": {"table": "inventory"}}],
            "joins": [],
            "metrics": [{"id": "stock_value_myr"}],
        },
        "query_plan": {"measure": "stock_value_myr", "group_by": []},
    }
    fake = _FakeCortex([_ONTOLOGY, second])
    client = _client(fake, monkeypatch)
    first = client.post("/v1/studio/chat/plan", json={"question": "how many skus"})
    edited = client.post("/v1/studio/chat/plan", json={"question": "stock value"})
    assert first.status_code == 200
    assert first.json()["plan"]["provided"] is False
    assert edited.status_code == 200
    assert edited.json()["plan"]["query_plan"]["measure"] == "stock_value_myr"
    assert fake.ontology_qs == ["how many skus", "stock value"]
    assert fake.asks == []


def test_confirm_route_blocks_clarify_and_does_not_run_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = project_studio_turn(
        {
            "route": "needs_clarification",
            "answer": "Which warehouse?",
            "query_plan": {"measure": "sku_count"},
        }
    )
    fake = _FakeCortex()
    client = _client(fake, monkeypatch)
    blocked = client.post(
        "/v1/studio/chat/confirm",
        json={"question": "stock", "clarify_reply": "", "preview": preview},
    )
    assert blocked.status_code == 409
    allowed = client.post(
        "/v1/studio/chat/confirm",
        json={"question": "stock", "clarify_reply": "WH-A", "preview": preview},
    )
    assert allowed.status_code == 200
    assert allowed.json()["execute"] is True
    assert allowed.json()["sql_ran"] is False
    assert allowed.json()["question"] == "stock\nWH-A"
    assert fake.ontology_qs == []
    assert fake.asks == []


def test_plan_route_fail_closed_without_a_fabricated_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCortex()
    fake.error = InsightsError("down", status_code=503)
    res = _client(fake, monkeypatch).post(
        "/v1/studio/chat/plan", json={"question": "how many skus"}
    )
    assert res.status_code == 503
    assert "sku_count" not in res.text
    assert res.json()["sql_ran"] is False
