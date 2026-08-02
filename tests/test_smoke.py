"""Smoke + health — ask defaults to live; demo fallback off unless opted in."""

from pathlib import Path

import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from fastapi.testclient import TestClient


def test_create_app():
    app = create_app()
    assert app.title == "DMS API"


def test_health_route():
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["product"] == "dms"
    assert body["contract"] == "1.2.0"
    assert body["ask_mode"] in ("live", "demo")
    assert body["demo_fallback"] is False or body["demo_fallback"] is True
    assert "contract_routes" in body["dependencies"]["cortex"]
    assert "jwks_refresh" in body["dependencies"]["cortex"]
    assert "database_configured" in body
    assert "trust" in body["dependencies"]["openvault"]


def test_list_spaces():
    client = TestClient(create_app())
    r = client.get("/v1/spaces")
    assert r.status_code == 200
    spaces = r.json()
    assert len(spaces) >= 2


def test_chat_ask_demo_never_certified(monkeypatch):
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": "What was total revenue?",
            "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["badge"] == "L2_VALIDATED"
    assert body["ask_mode"] == "demo"
    assert body["values"]


def test_chat_ask_divide_by_5(monkeypatch):
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": "Divide the revenue by 5",
            "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["badge"] == "L2_VALIDATED"
    ids = {v["id"] for v in body["values"]}
    assert "v_rev" in ids and "v_scaled" in ids
    total = next(v["value"] for v in body["values"] if v["id"] == "v_rev")
    scaled = next(v["value"] for v in body["values"] if v["id"] == "v_scaled")
    assert scaled == pytest.approx(total / 5)


def test_chat_ask_abstain(monkeypatch):
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": "What is the meaning of life?",
            "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        },
    )
    assert r.status_code == 200
    assert r.json()["badge"] == "ABSTAIN"


def test_chat_ask_unknown_space():
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.post(
        "/v1/chat/ask",
        json={"question": "hi", "space_id": "sp_missing"},
    )
    assert r.status_code == 404


def _gate_allows(monkeypatch) -> None:
    """Grant the compliance decision these tests assume, rather than bypass it.

    Ingest is a mutation and now fails closed when the gate cannot decide, which
    is what happens here: there is no Cortex, so ``compliance_gate`` returns
    ``gate_unavailable``. Supplying an allow keeps the test testing ingest; the
    fail-closed behaviour itself is covered by
    ``test_skeleton_ping_calls_gate_fail_closed`` and by the spaces regression
    guard in tests/test_product_surfaces.py.
    """
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        studio_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )


def test_studio_ingest_csv(monkeypatch):
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(Path("data/_smoke_ingest.duckdb").resolve()))
    get_settings.cache_clear()
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        files={"file": ("sales.csv", b"sku,qty\nA,1\nB,2\n", "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()
    # Receipt counts files (honest multi-file semantics), not rows
    assert body["files_seen"] == 1
    assert body["ingested"] == 1
    assert body["need_attention"] == 0
    assert body["table"]
    assert "TABULAR_CLEAN" in (body.get("per_class") or {})


def test_studio_quarantine_xlsx(monkeypatch):
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        files={"file": ("book.xlsx", b"PK\x03\x04fake", "application/vnd.ms-excel")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] == 0
    assert body["need_attention"] >= 1 or body["quarantined"] >= 1
    assert body["reasons"]


def test_skeleton_ping_calls_gate_fail_closed():
    client = TestClient(create_app())
    r = client.post("/v1/skeleton/ping", json={"message": "hi"})
    assert r.status_code == 403
    assert r.json()["detail"] == "gate_unavailable"


def test_library_and_audit_without_db():
    client = TestClient(create_app())
    assert client.get("/v1/library/sources").status_code == 200
    assert client.get("/v1/audit/ledger").status_code == 200
    assert client.get("/v1/amend/proposals").status_code == 200
