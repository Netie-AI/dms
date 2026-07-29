"""Smoke: app factory imports without CortexOS."""

import pytest
from dms_api.app import create_app
from fastapi.testclient import TestClient


def test_create_app():
    app = create_app()
    assert app.title == "DMS API"


def test_health_route():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["product"] == "dms"


def test_skeleton_ping_calls_gate_stub():
    """Gate is a Cortex call-through; stub raises until sync-contract."""
    client = TestClient(create_app(), raise_server_exceptions=True)
    with pytest.raises(NotImplementedError, match="sync-contract"):
        client.post("/v1/skeleton/ping", json={"message": "hi"})
