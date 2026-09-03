"""EPIC-020 ticket 5: one extracted_at on receipt, preview, tree, envelope."""

from __future__ import annotations

from pathlib import Path

import pytest
from cortex_client.models import AskResponse
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor import map_ask_response_to_envelope
from dms_executor.bronze import write_bronze_rows
from dms_executor.envelope import assert_envelope_valid
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install
from test_sql_source_route import _body, _gate_allows


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "sql_watermark.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


def _bronze_meta(tree: dict, table: str) -> dict:
    needle = table.split(".", 1)[-1]
    for folder in tree["nodes"]:
        if folder.get("id") != "folder:bronze":
            continue
        for node in folder.get("children") or []:
            meta = node.get("meta") or {}
            label = str(meta.get("table") or node.get("label") or "")
            if needle in label or table in label:
                return meta
    raise AssertionError(f"no bronze node for {table!r} in {tree}")


def _ask_citing(container: str) -> dict:
    resp = AskResponse.model_validate(
        {
            "answer": "Orders total 10.50.",
            "audit_id": "aud_wm",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": "SELECT SUM(amount) FROM dbo_orders",
            "rows": [{"total": 10.50}],
            "drillthrough_token": "dt_watermark_ok",
            "contributing_sources": [
                {
                    "ref_id": "s1",
                    "container": container,
                    "kind": "sql",
                    "row_count": 1,
                    "contribution": 1,
                }
            ],
        }
    )
    env = map_ask_response_to_envelope(resp)
    assert_envelope_valid(env)
    return env


def test_extracted_at_is_the_same_string_on_four_artifacts(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection(
        [("dbo", "orders")], (["order_id", "amount"], [["A-1", "10.50"]])
    )
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post("/v1/studio/sources/sql", json=_body(tables=["orders"]))
    assert r.status_code == 200, r.text
    landed = r.json()["tables"][0]
    stamp = landed["extracted_at"]
    assert stamp
    table = landed["bronze_table"]

    prev = client.get(f"/v1/library/bronze/{table}/preview")
    assert prev.status_code == 200, prev.text
    body = prev.json()
    assert body["extracted_at"] == stamp
    assert body["source_kind"] == "sql"

    tree = client.get("/v1/library/tree").json()
    meta = _bronze_meta(tree, table)
    assert meta["extracted_at"] == stamp
    assert meta["source_kind"] == "sql"

    env = _ask_citing(table.split(".", 1)[-1])
    src = env["contributing_sources"][0]
    assert src["extracted_at"] == stamp
    assert src["source_kind"] == "sql"


def test_unknown_container_stamps_null_not_a_skip(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection([("dbo", "orders")], (["order_id"], [["1"]]))
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    TestClient(create_app()).post("/v1/studio/sources/sql", json=_body(tables=["orders"]))
    env = _ask_citing("not_in_the_registry")
    src = env["contributing_sources"][0]
    assert src["extracted_at"] is None
    assert src["source_kind"] is None


def test_truncated_flag_on_preview_and_tree(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection([("dbo", "orders")], (["order_id"], [["1"], ["2"]]))
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/sources/sql", json=_body(tables=["orders"], max_rows=1)
    )
    assert r.status_code == 200, r.text
    table = r.json()["tables"][0]["bronze_table"]
    prev = client.get(f"/v1/library/bronze/{table}/preview").json()
    assert prev["truncated"] is True
    meta = _bronze_meta(client.get("/v1/library/tree").json(), table)
    assert meta["truncated"] is True


def test_csv_ingest_is_file_not_extract(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        files={"file": ("sales.csv", b"sku,qty\nA,1\n", "text/csv")},
    )
    assert r.status_code == 200, r.text
    table = r.json()["table"]
    prev = client.get(f"/v1/library/bronze/{table}/preview").json()
    assert prev["source_kind"] == "file"
    assert prev["extracted_at"]
    meta = _bronze_meta(client.get("/v1/library/tree").json(), table)
    assert meta["source_kind"] == "file"


def test_write_without_registry_has_no_watermark(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_bronze_rows(
        table="orphan_rows", columns=["k"], rows=[["1"]], path=warehouse
    )
    client = TestClient(create_app())
    prev = client.get("/v1/library/bronze/orphan_rows/preview").json()
    assert prev["extracted_at"] is None
    assert prev["source_kind"] is None
