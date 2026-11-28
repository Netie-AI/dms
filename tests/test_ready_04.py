"""READY-04. Flag off is the current health and data-map body.

Flag on, a synthetic sqlite company can be created, granted, joined, and counted.
The questions call is a stub transport. It does not name a model.
"""

from __future__ import annotations

import json

import pytest
from cortex_client.gate import ComplianceDecision
from dms_core.control_plane.connect_grants import granted_tables
from dms_core.control_plane.connect_grants import reset as reset_grants
from dms_core.control_plane.ready_flow import (
    bind_questions_transport,
    health_status,
    infer_joins,
    parse_questions,
    profile_sqlite,
    questions_request,
    reset_questions_transport,
    write_demo_sqlite,
)
from dms_core.control_plane.ready_flow import (
    reset as reset_ready,
)
from fastapi.testclient import TestClient

FLAGS = (
    "DMS_READY_HEALTH",
    "DMS_READY_TENANT",
    "DMS_READY_GRANT",
    "DMS_READY_JOINS",
    "DMS_READY_GUIDE",
    "DMS_READY_DEMO",
)


def _allow(**kwargs):
    return ComplianceDecision(allowed=True, reason="ok", action=str(kwargs.get("action") or ""))


@pytest.fixture
def isolated():
    reset_ready()
    reset_grants()
    yield
    reset_ready()
    reset_grants()


def _flags(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    for name in FLAGS:
        if on:
            monkeypatch.setenv(name, "1")
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from dms_api.settings import get_settings

    get_settings.cache_clear()


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("dms_api.routes.ready.compliance_gate", _allow)
    monkeypatch.setattr("dms_api.routes.library.compliance_gate", _allow)
    from dms_api.app import create_app
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


def test_digit_one_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_core.control_plane.ready_flow import health_enabled

    monkeypatch.setenv("DMS_READY_HEALTH", "true")
    assert health_enabled() is False
    monkeypatch.setenv("DMS_READY_HEALTH", "1")
    assert health_enabled() is True
    assert health_status(True) == "ok"
    assert health_status(False) == "degraded"
    monkeypatch.delenv("DMS_READY_HEALTH", raising=False)
    assert health_status(False) == "ok"


def test_flag_off_health_and_map_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, isolated: None
) -> None:
    _flags(monkeypatch, False)
    client = _client(monkeypatch)
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert "degraded" not in health.text
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/v1/ready") for path in paths)
    data_map = client.get("/v1/library/data-map")
    assert data_map.status_code == 200
    assert "join_candidates" not in data_map.json()
    missing = client.post("/v1/ready/tenants", json={"name": "Synthetic Pharmacy"})
    assert missing.status_code == 404


def test_new_tenant_demo_answers_on_a_synthetic_db(
    monkeypatch: pytest.MonkeyPatch, isolated: None
) -> None:
    _flags(monkeypatch, True)
    seen: dict = {}

    def transport(body: dict) -> dict:
        seen["body"] = body
        return {
            "questions": [
                "How many orders are there?",
                "How many customers are there?",
                "Which products are not on an order?",
                "Which customer is on more than one order?",
                "How many distinct sku values are on orders?",
            ]
        }

    token = bind_questions_transport(transport)
    try:
        client = _client(monkeypatch)
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "degraded"
        assert health.json()["backend"] == "memory"

        demo = client.post("/v1/ready/demo")
        assert demo.status_code == 201, demo.text
        payload = demo.json()
        assert payload["company"]["persisted"] is False
        assert "password" not in demo.text.lower()
        assert payload["company"]["name"] == "Ready demo"
        source = payload["source"]
        assert source["granted_tables"] == []
        assert source["grant_proposal"]["privilege"] == "read"
        assert set(source["exposed_tables"]) == {"customers", "orders", "products"}
        origins = {row["origin"] for row in source["join_candidates"]}
        assert "values" in origins
        assert "name_and_values" in origins
        id_pair = [
            row
            for row in source["join_candidates"]
            if row["from_column"] == "id" and row["to_column"] == "id"
        ]
        assert id_pair == []
        value_join = next(
            row
            for row in source["join_candidates"]
            if row["from_column"] == "customer_id" and row["to_column"] == "id"
        )
        assert value_join["origin"] == "values"
        assert value_join["value_support"] is True

        space_id = payload["company"]["space_id"]
        assert granted_tables(space_id) == ()
        partial = client.post(
            f"/v1/ready/sources/{source['source_id']}/grants",
            json={"tables": ["orders"]},
        )
        assert partial.status_code == 200, partial.text
        assert partial.json()["granted_tables"] == ["orders"]
        assert partial.json()["privilege"] == "read"
        refused = client.post(
            f"/v1/ready/sources/{source['source_id']}/grants",
            json={"tables": ["not_a_table"]},
        )
        assert refused.status_code == 400

        early = client.post("/v1/ready/answer", json={"table": "customers"})
        assert early.status_code == 403

        confirmed = client.post(
            "/v1/ready/joins/confirm",
            json={"ids": [row["id"] for row in source["join_candidates"]]},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"

        data_map = client.get("/v1/library/data-map").json()
        assert any(row["origin"] == "values" for row in data_map["join_candidates"])

        asked = client.post("/v1/ready/questions")
        assert asked.status_code == 200, asked.text
        assert len(asked.json()["questions"]) == 5
        assert seen["body"]["role"] == "questions"
        assert "model" not in seen["body"]
        assert "strict" not in seen["body"]
        prompt = seen["body"]["messages"][1]["content"]
        assert "Ada" not in prompt
        assert "Bolt" not in prompt

        counted = client.post("/v1/ready/answer", json={"table": "orders"})
        assert counted.status_code == 200, counted.text
        assert counted.json()["measured_rows"] == 3

        guide = client.get("/v1/ready/guide")
        assert guide.status_code == 200, guide.text
        body = guide.json()
        assert body["progress"] == {"done": 5, "total": 5}
        assert [step["n"] for step in body["steps"]] == [1, 2, 3, 4, 5]
        assert all(step["proves"] for step in body["steps"])
        assert body["demo"] is True
        assert len(body["questions"]) == 5
    finally:
        reset_questions_transport(token)


def test_questions_reject_a_short_list() -> None:
    assert parse_questions({"questions": ["only one"]}) is None
    assert parse_questions("not json") is None


def test_infer_uses_values_when_no_foreign_key_is_declared(tmp_path) -> None:
    path = write_demo_sqlite(tmp_path)
    tables = profile_sqlite(path)
    found = infer_joins(tables)
    by_origin = {row.origin for row in found}
    assert "values" in by_origin
    declared = [row for row in found if row.origin == "declared"]
    assert declared == []
    body = questions_request(tables)
    encoded = json.dumps(body)
    assert "Ada" not in encoded
    assert body["role"] == "questions"


def test_missing_user_fk_stays_500_until_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bare 500 is the flag-off body. Flag on names the missing user."""
    from dms_api.tenant_http import response_for_foreign_key

    class _Diag:
        constraint_name = "spaces_created_by_fkey"

    class _Fk(Exception):
        diag = _Diag()

    monkeypatch.delenv("DMS_READY_TENANT", raising=False)
    bare = response_for_foreign_key(_Fk())
    assert bare.status_code == 500
    assert bare.body == b'{"detail":"Internal Server Error"}'
    monkeypatch.setenv("DMS_READY_TENANT", "1")
    typed = response_for_foreign_key(_Fk())
    assert typed.status_code == 409
    assert typed.body == b'{"detail":{"code":"actor_not_in_tenant"}}'


def test_viewer_cannot_confirm(monkeypatch: pytest.MonkeyPatch, isolated: None) -> None:
    _flags(monkeypatch, True)
    monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    client = _client(monkeypatch)
    res = client.post("/v1/ready/demo")
    assert res.status_code == 403
    assert res.json()["detail"] == "steward_grant_required"
