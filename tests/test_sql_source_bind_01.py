"""FDE-SPACE-GRANT-01. A SQL land with a Space is a source, and it is not a grant.

``DMS_SQL_SOURCE_BIND`` defaults off. Off, the studio SQL receipt and the
Space source list stay as they are. On, the landed tables are proposed on
``dms.data_sources`` (the in-memory book when Postgres is parked) and none
of them are granted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_core.control_plane.connect_grants import reset as reset_grants
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install

SECRET = "rx-bind-secret"
TABLES = ("rx_sku", "rx_store", "rx_fill")


def _allow(*, action: str, **_: object) -> ComplianceDecision:
    return ComplianceDecision(allowed=True, reason="test_allow", action=action)


def _rows(name: str) -> tuple[list[str], list[list[object]]]:
    if name == "rx_sku":
        return ["sku_id", "label"], [["s1", "alpha"], ["s2", "beta"]]
    if name == "rx_store":
        return ["store_id", "label"], [["t1", "north"]]
    return ["fill_id", "sku_id"], [["f1", "s1"], ["f2", "s2"], ["f3", "s1"]]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "bind.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    from dms_api import settings as settings_mod
    from dms_api.routes import spaces as spaces_routes
    from dms_api.routes import studio as studio_routes

    settings_mod.get_settings.cache_clear()
    reset_grants()
    monkeypatch.setattr(studio_routes, "compliance_gate", _allow)
    monkeypatch.setattr(spaces_routes, "compliance_gate", _allow)
    catalog = [("dbo", name) for name in TABLES]
    data = {name: _rows(name) for name in TABLES}
    _install(monkeypatch, _FakeConnection(catalog, data))
    app = create_app()
    with TestClient(app) as http:
        yield http
    reset_grants()
    settings_mod.get_settings.cache_clear()


def _space(http: TestClient) -> str:
    created = http.post("/v1/spaces", json={"name": "Rx bind"})
    assert created.status_code == 201, created.text
    return str(created.json()["space"]["id"])


def _land(http: TestClient, space_id: str) -> dict[str, object]:
    body = {
        "kind": "sqlserver",
        "host": "db.example.net",
        "database": "rx_chain",
        "user": "reader",
        "password": SECRET,
        "tables": list(TABLES),
        "space_id": space_id,
    }
    res = http.post("/v1/studio/sources/sql", json=body)
    assert res.status_code == 200, res.text
    assert SECRET not in res.text
    return res.json()


def test_flag_off_does_not_register_a_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_SQL_SOURCE_BIND", raising=False)
    space_id = _space(client)
    landed = _land(client, space_id)
    assert "source_id" not in landed
    assert "granted_tables" not in landed
    listed = client.get(f"/v1/spaces/{space_id}/sources")
    assert listed.status_code == 200, listed.text
    assert listed.json()["count"] == 0
    assert SECRET not in listed.text


def test_flag_on_registers_one_source_and_grants_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SQL_SOURCE_BIND", "1")
    space_id = _space(client)
    landed = _land(client, space_id)
    assert landed["granted_tables"] == []
    assert len(landed["exposed_tables"]) == 3
    source_id = landed["source_id"]
    assert isinstance(source_id, str) and source_id
    listed = client.get(f"/v1/spaces/{space_id}/sources")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 1
    assert body["sources"][0]["id"] == source_id
    assert body["sources"][0]["kind"] == "sql"
    assert body["sources"][0]["space_id"] == space_id
    assert body["sources"][0]["granted_tables"] == []
    assert SECRET not in listed.text
    assert "rx-bind-secret" not in str(body["sources"][0]["ref"])
