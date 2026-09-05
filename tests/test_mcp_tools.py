"""EPIC-014 MCP-01: thin wrap of existing HTTP. Flag-off is 404.

Asserts tool results match the UI/API JSON for the same inputs (MCP-02 smoke
shape: badge/abstained/values and preview rows). Not a second serving engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.settings import get_settings
from fastapi.testclient import TestClient


def _allow(*, action: str, **_: object) -> ComplianceDecision:
    return ComplianceDecision(allowed=True, reason="test_allow", action=action)


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.chat as chat_routes
    import dms_api.routes.library as library_routes
    import dms_api.routes.mcp as mcp_routes

    monkeypatch.setattr(mcp_routes, "compliance_gate", _allow)
    monkeypatch.setattr(chat_routes, "compliance_gate", _allow)
    monkeypatch.setattr(library_routes, "compliance_gate", _allow)


@pytest.fixture()
def mcp_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DMS_MCP", "1")
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "wh.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    _gate_allows(monkeypatch)
    from dms_api.app import create_app

    return TestClient(create_app())


def test_mcp_off_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_MCP", raising=False)
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "wh.duckdb"))
    get_settings.cache_clear()
    from dms_api.app import create_app

    client = TestClient(create_app())
    assert client.get("/v1/mcp/tools").status_code == 404
    assert client.post("/v1/mcp/call", json={"name": "ask"}).status_code == 404


def test_mcp_tools_lists_three(mcp_client: TestClient) -> None:
    resp = mcp_client.get("/v1/mcp/tools")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [t["name"] for t in body["tools"]]
    assert names == ["ask", "preview", "list_metrics"]


def test_mcp_ask_matches_http(mcp_client: TestClient) -> None:
    payload = {
        "question": "What was total revenue?",
        "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    }
    http = mcp_client.post("/v1/chat/ask", json=payload)
    mcp = mcp_client.post("/v1/mcp/call", json={"name": "ask", "arguments": payload})
    assert http.status_code == 200, http.text
    assert mcp.status_code == 200, mcp.text
    env = http.json()
    result = mcp.json()["result"]
    assert result["badge"] == env["badge"]
    assert result["abstained"] == env["abstained"]
    assert result["values"] == env["values"]
    assert result["text"] == env["text"]


def test_mcp_preview_matches_http(mcp_client: TestClient) -> None:
    http = mcp_client.get("/v1/library/warehouse/transactions/preview", params={"limit": 3})
    mcp = mcp_client.post(
        "/v1/mcp/call",
        json={"name": "preview", "arguments": {"table": "transactions", "limit": 3}},
    )
    assert http.status_code == 200, http.text
    assert mcp.status_code == 200, mcp.text
    body = http.json()
    result = mcp.json()["result"]
    assert result["table"] == body["table"]
    assert result["rows"] == body["rows"]
    assert result["columns"] == body["columns"]


def test_mcp_preview_still_refuses_ungranted(mcp_client: TestClient) -> None:
    http = mcp_client.get("/v1/library/warehouse/alerts/preview")
    mcp = mcp_client.post(
        "/v1/mcp/call",
        json={"name": "preview", "arguments": {"table": "alerts"}},
    )
    assert http.status_code == 403
    assert mcp.status_code == 403
    assert mcp.json()["detail"]["code"] == http.json()["detail"]["code"]


def test_mcp_list_metrics_matches_http(mcp_client: TestClient) -> None:
    http = mcp_client.get("/v1/ontology/metrics")
    mcp = mcp_client.post("/v1/mcp/call", json={"name": "list_metrics", "arguments": {}})
    assert http.status_code == mcp.status_code
    assert mcp.json()["result"] == http.json()


def test_mcp_unknown_tool_404(mcp_client: TestClient) -> None:
    resp = mcp_client.post("/v1/mcp/call", json={"name": "not_a_tool", "arguments": {}})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_tool"
