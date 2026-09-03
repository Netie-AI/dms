"""EPIC-020 ticket 5 (dms#114): one extracted_at on receipt, preview, tree, ask."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import duckdb
import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor import Executor
from dms_executor.bronze import write_bronze_rows
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.warehouse_browse import preview_bronze_table, stamp_source_watermarks
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install
from test_sql_source_route import _body, _gate_allows

ORDERS_SOURCE = "sqlserver://db.example.net:1433/sales#dbo.orders"


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "sqlsrc05.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


def _bronze_node(tree: dict[str, Any], bronze_table: str) -> dict[str, Any]:
    wanted = f"bronze:{bronze_table}"
    for folder in tree["nodes"]:
        if folder["id"] != "folder:bronze":
            continue
        for node in folder.get("children") or []:
            if node["id"] == wanted:
                return node
    raise AssertionError(f"no tree node {wanted}")


def test_four_artifacts_share_one_extracted_at(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receipt, preview, tree meta, and ask sources[] carry the identical ISO string."""
    con = _FakeConnection(
        [("dbo", "orders")],
        (["order_id", "amount"], [["A-1", "10.50"]]),
    )
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post("/v1/studio/sources/sql", json=_body(tables=["orders"]))
    assert r.status_code == 200, r.text
    landed = r.json()["tables"][0]
    stamp = landed["extracted_at"]
    assert stamp.endswith("Z") and "T" in stamp
    table = landed["bronze_table"]

    prev = client.get(f"/v1/library/bronze/{table}/preview")
    assert prev.status_code == 200, prev.text
    preview = prev.json()
    assert preview["extracted_at"] == stamp
    assert preview["source_kind"] == "sql"
    assert preview["source"] == ORDERS_SOURCE
    assert preview["truncated"] is False

    tree = client.get("/v1/library/tree").json()
    node = _bronze_node(tree, table)
    assert node["meta"]["extracted_at"] == stamp
    assert node["meta"]["source_kind"] == "sql"
    assert node["meta"]["truncated"] is False

    env = _ask_citing(warehouse, monkeypatch, container="dbo_orders")
    assert_envelope_valid(env)
    cited = env["contributing_sources"][0]
    assert cited["container"] == "dbo_orders"
    assert cited["extracted_at"] == stamp
    assert cited["source_kind"] == "sql"

    # Cortex citation under a non-registry name: stamp is absent, not skipped.
    env_miss = _ask_citing(warehouse, monkeypatch, container="orders_elsewhere")
    assert_envelope_valid(env_miss)
    assert env_miss["contributing_sources"][0]["extracted_at"] is None


def test_truncated_true_on_preview_and_tree_node(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection(
        [("dbo", "orders")],
        (["order_id"], [["1"], ["2"]]),
    )
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/sources/sql",
        json=_body(tables=["orders"], max_rows=1),
    )
    assert r.status_code == 200, r.text
    landed = r.json()["tables"][0]
    assert landed["truncated"] is True
    table = landed["bronze_table"]

    preview = client.get(f"/v1/library/bronze/{table}/preview").json()
    assert preview["truncated"] is True
    node = _bronze_node(client.get("/v1/library/tree").json(), table)
    assert node["meta"]["truncated"] is True


def test_csv_ingest_is_file_kind_not_extracted(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        files={"file": ("sales.csv", b"sku,qty\nA,1\nB,2\n", "text/csv")},
    )
    assert r.status_code == 200, r.text
    table = r.json()["table"]
    assert table
    preview = client.get(f"/v1/library/bronze/{table}/preview").json()
    assert preview["source_kind"] == "file"
    assert preview["extracted_at"]
    assert preview["truncated"] is not True
    node = _bronze_node(client.get("/v1/library/tree").json(), table)
    assert node["meta"]["source_kind"] == "file"


def test_write_bronze_rows_has_null_watermark(warehouse: Path) -> None:
    write_bronze_rows(
        table="orphan_orders",
        columns=["order_id"],
        rows=[["1"]],
        path=warehouse,
    )
    prev = preview_bronze_table("orphan_orders", path=warehouse)
    assert prev["extracted_at"] is None
    assert prev["source_kind"] is None


def test_stamp_tolerates_bronze_spellings_on_one_connection(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection([("dbo", "orders")], (["order_id"], [["1"]]))
    _install(monkeypatch, con)
    from dms_executor.db_connector import SourceConfig, ingest_source_table

    pull = ingest_source_table(
        SourceConfig(
            kind="sqlserver",
            host="db.example.net",
            database="sales",
            user="reader",
            password="p;w}d",
        ),
        "orders",
        path=warehouse,
    )
    n = {"n": 0}
    real = duckdb.connect

    def counted(*a: Any, **k: Any) -> Any:
        n["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr("dms_executor.warehouse_browse.duckdb.connect", counted)
    sources = [
        {"container": "dbo_orders"},
        {"container": "bronze.dbo_orders"},
        {"container": "bronze:dbo_orders"},
    ]
    stamp_source_watermarks(sources, path=warehouse)
    assert n["n"] == 1
    assert {s["extracted_at"] for s in sources} == {pull.extracted_at}


@dataclass
class _SqlCiteCortex:
    container: str
    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-sqlsrc05")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse.model_validate(
            {
                "answer": "Order amount is 10.5.",
                "audit_id": "aud_sqlsrc05",
                "route": "query_skill",
                "provenance": {"badge": "query_skill", "layer": "L2"},
                "sql_used": "SELECT amount FROM bronze.dbo_orders",
                "rows": [{"amount": 10.5}],
                "drillthrough_token": "dt_sqlsrc05",
                "contributing_sources": [
                    {
                        "container": self.container,
                        "kind": "sql",
                        "row_count": 1,
                        "contribution": 1.0,
                    }
                ],
            }
        )


def _minter() -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    key = MagicMock()
    key.kid = "test-kid"
    key.sign.return_value = "dGVzdA"
    return m


def _ask_citing(
    warehouse: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    container: str,
) -> dict[str, Any]:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()

    cortex = _SqlCiteCortex(container=container)
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex, minter=_minter(), warehouse_path=warehouse
    )  # type: ignore[arg-type]
    app.state.cortex = cortex
    ask_client = TestClient(app)
    body = ask_client.post(
        "/v1/chat/ask",
        json={"question": "What is the order amount?", "session_id": "ses_sqlsrc05"},
    ).json()
    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()
    return body
