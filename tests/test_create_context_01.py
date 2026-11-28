"""DMS-CREATE-CONTEXT-01. Own chats, a questionnaire, or a project.

The flag is off unless it is the digit 1. A caller can pack only what they
own or what was shared to them. The provider sees a short pack, not a raw table.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor.create_context import context_enabled, layout_blocks, pack_sources
from fastapi.testclient import TestClient

FLAG = "DMS_CREATE"
CONTEXT = "DMS_CREATE_CONTEXT"
SPACE = "space-create-context-01"
ADMIN = "dms-context-admin"
VIEWER = "dms-context-viewer"
OV_KEY = "ov_customer_test"


def _headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", "X-OpenVault-Key": OV_KEY}


def _lake(tmp_path: Path) -> None:
    path = tmp_path / "orders.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders (month VARCHAR, amount INTEGER)")
    con.execute("INSERT INTO orders VALUES ('Jan', 10), ('Feb', 12)")
    con.close()


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, context: str | None) -> TestClient:
    monkeypatch.setenv(FLAG, "1")
    if context is None:
        monkeypatch.delenv(CONTEXT, raising=False)
    else:
        monkeypatch.setenv(CONTEXT, context)
    monkeypatch.setenv("DMS_API_KEYS", f"admin:{ADMIN};viewer:{VIEWER}")
    monkeypatch.setenv("DMS_CREATE_WAREHOUSE", str(tmp_path / "orders.duckdb"))
    monkeypatch.setenv("DMS_CREATE_GRANTS", "orders")
    monkeypatch.setenv("DMS_CREATE_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("DMS_CREATE_GENERATOR", "claude_api")
    _lake(tmp_path)
    get_settings.cache_clear()

    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        del action, actor
        return ComplianceDecision(allowed=True, reason="test_allow", action="studio.create")

    monkeypatch.setattr("dms_api.routes.studio_create.compliance_gate", allow)
    return TestClient(create_app())


def _chat(summary: str, rows: int = 1) -> dict[str, Any]:
    return {
        "title": "January orders",
        "summary": summary,
        "sql": ["SELECT month, amount FROM orders"],
        "results": [
            {
                "table": "orders",
                "rows": [{"month": f"m{i}", "amount": i} for i in range(rows)],
            }
        ],
        "schema_map": [{"name": "orders", "columns": ["month", "amount"]}],
        "ontology": [{"from": "orders", "to": "orders"}],
    }


def test_context_flag_accepts_only_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTEXT, raising=False)
    assert context_enabled() is False
    for raw in ("0", "true", "yes", "01", ""):
        monkeypatch.setenv(CONTEXT, raw)
        assert context_enabled() is False
    monkeypatch.setenv(CONTEXT, "1")
    assert context_enabled() is True
    assert layout_blocks("doc") == []


def test_flag_off_hides_the_routes_and_drops_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, context=None)
    missing = client.get("/v1/studio/create/context", headers=_headers(ADMIN))
    assert missing.status_code == 404
    opened = client.post(
        "/v1/studio/create/sessions",
        headers=_headers(ADMIN),
        json={
            "space_id": SPACE,
            "kind": "doc",
            "goal": "A one page note",
            "audience": "Founder",
            "context": {
                "chat_ids": ["a" * 32],
                "questionnaire": {"audience": "Founder", "goal": "Hidden goal text"},
            },
        },
    )
    assert opened.status_code == 200, opened.text
    stored = list((tmp_path / "store").glob("*.json"))
    assert stored
    body = json.loads(stored[0].read_text(encoding="utf-8"))
    assert "sources" not in body
    assert client.get("/v1/studio/create/status").json() == {"enabled": True}


def test_stranger_cannot_read_or_pack_until_shared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, context="1")
    assert client.get("/v1/studio/create/status").json()["context"] is True
    saved = client.post(
        "/v1/studio/create/context/chats",
        headers=_headers(ADMIN),
        json=_chat("only-admin-summary", rows=9),
    )
    assert saved.status_code == 200, saved.text
    chat_id = saved.json()["id"]
    listed = client.get("/v1/studio/create/context", headers=_headers(VIEWER))
    assert listed.status_code == 200
    assert listed.json()["chats"] == []
    assert "only-admin-summary" not in listed.text
    blocked = client.post(
        "/v1/studio/create/sessions",
        headers=_headers(VIEWER),
        json={
            "space_id": SPACE,
            "kind": "doc",
            "goal": "A note",
            "audience": "Founder",
            "context": {"chat_ids": [chat_id]},
        },
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "context_forbidden"
    shared = client.post(
        f"/v1/studio/create/context/chat/{chat_id}/share",
        headers=_headers(VIEWER),
        json={"share_with": "api_admin"},
    )
    assert shared.status_code == 403
    ok = client.post(
        f"/v1/studio/create/context/chat/{chat_id}/share",
        headers=_headers(ADMIN),
        json={"share_with": "api_viewer"},
    )
    assert ok.status_code == 200, ok.text
    visible = client.get("/v1/studio/create/context", headers=_headers(VIEWER))
    assert visible.json()["chats"][0]["id"] == chat_id
    assert "m8" not in visible.text


def test_pack_keeps_a_small_result_and_the_questionnaire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, context="1")
    saved = client.post(
        "/v1/studio/create/context/chats",
        headers=_headers(ADMIN),
        json=_chat("orders rose in February", rows=9),
    )
    chat_id = saved.json()["id"]
    project = client.post(
        "/v1/studio/create/context/projects",
        headers=_headers(ADMIN),
        json={
            "name": "Harborline",
            "summary": "Distributor project",
            "schema_map": [
                {"name": "orders", "columns": ["month"]},
                {"name": "payroll", "columns": ["salary"]},
            ],
            "ontology": [{"from": "payroll", "to": "orders"}],
            "sql": ["SELECT salary FROM payroll"],
            "results": [{"table": "payroll", "rows": [{"salary": "999"}]}],
        },
    )
    assert project.status_code == 200, project.text
    captured: list[dict[str, Any]] = []

    def transport(body: dict[str, Any], api_key: str) -> dict[str, Any]:
        del api_key
        captured.append(body)
        recorded = json.loads(
            (Path(__file__).parent / "fixtures" / "create" / "provider_app.json").read_text(
                encoding="utf-8"
            )
        )
        content = recorded["content"]
        assert isinstance(content, dict)
        return content

    monkeypatch.setattr("dms_core.create_generator.default_transport", transport)
    opened = client.post(
        "/v1/studio/create/sessions",
        headers=_headers(ADMIN),
        json={
            "space_id": SPACE,
            "kind": "app",
            "goal": "Board note",
            "audience": "Founder",
            "context": {
                "chat_ids": [chat_id],
                "project_id": project.json()["id"],
                "questionnaire": {
                    "audience": "Founder",
                    "goal": "Show the order trend",
                    "tone": "plain",
                    "length": "one page",
                    "must_include": ["orders", "payroll"],
                },
            },
        },
    )
    assert opened.status_code == 200, opened.text
    generated = client.post(
        f"/v1/studio/create/sessions/{opened.json()['session_id']}/generate",
        headers=_headers(ADMIN),
    )
    assert generated.status_code == 200, generated.text
    text = json.dumps(captured)
    assert "orders rose in February" in text
    assert "Show the order trend" in text
    assert "Distributor project" in text
    assert "m0" in text
    assert "m8" not in text
    assert "payroll" not in text
    assert "999" not in text
    assert "<sources>" in text


def test_layout_hook_stays_empty_until_a_block_is_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DMS_CREATE_STORE", str(tmp_path / "store"))
    monkeypatch.setenv(CONTEXT, "1")
    packed = pack_sources(
        actor="api_admin",
        kind="doc",
        granted={"orders"},
        questionnaire={
            "audience": "Founder",
            "goal": "A note",
            "tone": "",
            "length": "",
            "must_include": [],
        },
    )
    assert "layout_blocks" not in packed

    def blocks(kind: str) -> list[dict[str, str]]:
        return [{"kind": kind, "slot": "image"}]

    monkeypatch.setattr("dms_executor.create_context.layout_blocks", blocks)
    filled = pack_sources(
        actor="api_admin",
        kind="slides",
        granted={"orders"},
        questionnaire={
            "audience": "Founder",
            "goal": "A deck",
            "tone": "",
            "length": "",
            "must_include": [],
        },
    )
    assert filled["layout_blocks"] == [{"kind": "slides", "slot": "image"}]
