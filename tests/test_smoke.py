"""Smoke: app factory imports without CortexOS."""

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


def test_skeleton_ping_calls_gate_fail_closed():
    """Gate is Cortex call-through; without a client it fails closed (403)."""
    client = TestClient(create_app())
    r = client.post("/v1/skeleton/ping", json={"message": "hi"})
    assert r.status_code == 403
    assert r.json()["detail"] == "gate_unavailable"
