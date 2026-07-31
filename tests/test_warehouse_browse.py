"""Warehouse browse + library preview API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "browse.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    return path


def test_list_and_preview(warehouse: Path) -> None:
    from dms_executor.warehouse_browse import list_warehouse_tables, preview_warehouse_table

    tables = list_warehouse_tables(path=warehouse)
    names = {t["table"] for t in tables}
    assert "transactions" in names
    assert all(t["row_count"] >= 0 for t in tables)

    prev = preview_warehouse_table("transactions", limit=5, path=warehouse)
    assert prev["table"] == "transactions"
    assert len(prev["rows"]) <= 5
    assert "sku" in prev["columns"]


def test_preview_rejects_unknown(warehouse: Path) -> None:
    from dms_executor.warehouse_browse import preview_warehouse_table

    with pytest.raises(ValueError):
        preview_warehouse_table("drop_me;--", path=warehouse)


def test_library_preview_route(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_api.app import create_app

    client = TestClient(create_app())
    r = client.get("/v1/library/warehouse/transactions/preview?limit=3")
    assert r.status_code == 200
    body = r.json()
    assert body["table"] == "transactions"
    assert len(body["rows"]) <= 3

    bad = client.get("/v1/library/warehouse/nope/preview")
    assert bad.status_code == 404


def test_data_map_notes_missing_database(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from dms_api.app import create_app
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.get("/v1/library/data-map")
    assert r.status_code == 200
    body = r.json()
    assert body["database_configured"] is False
    assert "DATABASE_URL" in body["note"]
    assert isinstance(body["warehouse_tables"], list)
