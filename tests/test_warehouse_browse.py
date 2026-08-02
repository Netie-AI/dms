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
    assert body["row_count"] > len(body["rows"])

    bad = client.get("/v1/library/warehouse/nope/preview")
    assert bad.status_code == 404


def test_every_tree_leaf_previews(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PREVIEW-01: no bronze/warehouse leaf from the tree may 404."""
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_api.app import create_app
    from dms_executor.bronze import ingest_csv_bytes
    from dms_executor.warehouse_browse import list_bronze_tables, list_warehouse_tables

    ingest_csv_bytes(filename="tree_leaf.csv", data=b"sku,qty\nA,1\nB,2\n", path=warehouse)

    client = TestClient(create_app())
    tree = client.get("/v1/library/tree").json()

    def walk(nodes: list[dict]) -> list[dict]:
        out: list[dict] = []
        for n in nodes:
            if n.get("kind") == "leaf" and n.get("id", "").startswith(("bronze:", "warehouse:")):
                out.append(n)
            out.extend(walk(n.get("children") or []))
        return out

    for leaf in walk(tree["nodes"]):
        node_id = leaf["id"]
        kind, table = node_id.split(":", 1)
        path = (
            f"/v1/library/warehouse/{table}/preview?limit=200"
            if kind == "warehouse"
            else f"/v1/library/bronze/{table}/preview?limit=200"
        )
        r = client.get(path)
        assert r.status_code == 200, f"{node_id} -> {r.status_code} {r.text}"
        body = r.json()
        assert "rows" in body and "columns" in body
        assert body["row_count"] >= len(body["rows"])

    for row in list_bronze_tables(path=warehouse):
        r = client.get(f"/v1/library/bronze/{row['table']}/preview?limit=200")
        assert r.status_code == 200
        assert r.json()["row_count"] == row["row_count"]

    for row in list_warehouse_tables(path=warehouse):
        r = client.get(f"/v1/library/warehouse/{row['table']}/preview?limit=200")
        assert r.status_code == 200
        assert r.json()["row_count"] == row["row_count"]


def test_preview_pagination_total_is_table_size(warehouse: Path) -> None:
    from dms_executor.warehouse_browse import preview_warehouse_table

    prev = preview_warehouse_table("transactions", limit=5, offset=0, path=warehouse)
    assert len(prev["rows"]) == 5
    assert prev["row_count"] == 15
    assert prev["row_count"] != len(prev["rows"])

    page2 = preview_warehouse_table("transactions", limit=5, offset=5, path=warehouse)
    assert len(page2["rows"]) == 5
    assert page2["row_count"] == 15


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
