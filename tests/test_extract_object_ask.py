"""Extract -> object VIEW -> authored tool -> certified envelope + chart."""

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
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SECRET = "pw-test"


def _body() -> dict[str, object]:
    return {
        "kind": "postgres",
        "host": "db.example.net",
        "database": "sales",
        "user": "reader",
        "password": SECRET,
        "tables": ["orders"],
    }


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        studio_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "extract_object.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


@dataclass
class _MarkerCortex:
    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run_obj")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Cortex marker.",
            badge="certified",
            sql_used="SELECT 1 AS cortex_marker",
            rows=[{"cortex_marker": 1}],
            assumptions="obj-isolation",
            audit_id="aud_obj_cortex",
            route="sql",
        )


def _live_client(
    warehouse: Path,
    monkeypatch: pytest.MonkeyPatch,
    cortex: _MarkerCortex,
) -> TestClient:
    from dms_api import settings as settings_mod

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

    monkeypatch.setattr(m, "mint_manifest", _mint)
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    key = MagicMock()
    key.kid = "test-kid"
    key.sign.return_value = "dGVzdA"
    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=m,
        warehouse_path=warehouse,
    )
    app.state.cortex = cortex
    return TestClient(app)


def test_extract_object_tool_ask_is_certified_chart(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection(
        [("public", "orders")], (["order_id", "amount"], [["A-1", "10.50"]])
    )
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    cortex = _MarkerCortex()
    client = _live_client(warehouse, monkeypatch, cortex)

    landed = client.post("/v1/studio/sources/sql", json=_body())
    assert landed.status_code == 200, landed.text
    tables = landed.json()["tables"]
    assert tables[0]["columns"] == ["order_id", "amount"]
    assert tables[0]["extracted_at"]
    bronze = tables[0]["bronze_table"]

    pub = client.post(
        "/v1/studio/objects",
        json={
            "name": "order",
            "relation": bronze,
            "columns": {"id": "order_id", "order_value": "amount"},
            "space_id": FINANCE,
            "tool_name": "order_count",
            "tool_sql": "SELECT COUNT(*) AS row_count FROM objects.order",
            "tool_description": "Row count of landed orders.",
        },
    )
    assert pub.status_code == 200, pub.text
    assert pub.json()["objects"] == ["objects.order"]
    assert pub.json()["silver"] == "silver.order"
    assert pub.json()["tool"]["name"] == "order_count"

    from dms_api.wiring import library_tree as build_tree

    named = build_tree(sources=[], bronze=[], warehouse=[], space_id=FINANCE)
    named_ids = {n["id"] for n in named["nodes"]}
    assert "folder:objects" in named_ids
    assert "folder:silver" not in named_ids
    obj_leaves = next(n for n in named["nodes"] if n["id"] == "folder:objects")["children"]
    assert any(c.get("meta", {}).get("target") == "objects.order" for c in obj_leaves)

    lake = duckdb.connect(str(warehouse))
    try:
        obj_cols = [r[0] for r in lake.execute("DESCRIBE SELECT * FROM objects.order").fetchall()]
        sil_cols = [r[0] for r in lake.execute("DESCRIBE SELECT * FROM silver.order").fetchall()]
        assert obj_cols == ["id", "order_value"]
        assert sil_cols == ["id", "order_value"]
        gold = lake.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'gold' AND table_name = 'order'"
        ).fetchone()
        assert gold is not None and int(gold[0]) == 0
        row = lake.execute("SELECT id, order_value FROM objects.order").fetchone()
        assert row is not None
        assert row[0] == "A-1"
        assert "10.5" in str(row[1])
    finally:
        lake.close()

    hit = client.post(
        "/v1/chat/ask",
        json={
            "question": "order_count",
            "space_id": FINANCE,
            "session_id": "ses_obj_hit",
            "grounded_tables": ["objects.order"],
        },
    )
    assert hit.status_code == 200, hit.text
    env = hit.json()
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["route"] == "metric_tool"
    assert env["metric_tool"] == "order_count"
    assert env["sql_used"].upper().startswith("SELECT")
    assert int(env["rows"][0]["row_count"]) == 1
    assert env["chart"]["kind"] == "bignum"
    assert "Insights:" in env["text"]
    assert cortex.asks == []

    miss = client.post(
        "/v1/chat/ask",
        json={
            "question": "invent a join across every table",
            "space_id": FINANCE,
            "session_id": "ses_obj_miss",
            "grounded_tables": ["objects.order"],
        },
    )
    assert miss.status_code == 200, miss.text
    refused = miss.json()
    assert_envelope_valid(refused)
    assert refused["badge"] == "ABSTAIN"
    assert refused["abstained"] is True
    assert "order_count" in refused["text"]
    assert cortex.asks == []

    free = client.post(
        "/v1/chat/ask",
        json={
            "question": "invent a join across every table",
            "space_id": FINANCE,
            "session_id": "ses_obj_free",
        },
    )
    assert free.status_code == 200, free.text
    isolated = free.json()
    assert_envelope_valid(isolated)
    assert "This object answers through certified tools" not in isolated["text"]
    assert isolated["sql_used"] == "SELECT 1 AS cortex_marker"
    assert len(cortex.asks) == 1

    open_ask = client.post(
        "/v1/chat/ask",
        json={
            "question": "invent a join across every table",
            "session_id": "ses_obj_open",
        },
    )
    assert open_ask.status_code == 200, open_ask.text
    other = open_ask.json()
    assert_envelope_valid(other)
    assert "This object answers through certified tools" not in other["text"]

def test_mysql_extract_grouped_tool_is_pie(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection(
        [("sales", "orders")],
        (
            ["warehouse", "qty"],
            [["WH-A", "12"], ["WH-B", "8"], ["WH-A", "3"]],
        ),
    )
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    cortex = _MarkerCortex()
    client = _live_client(warehouse, monkeypatch, cortex)
    landed = client.post(
        "/v1/studio/sources/sql",
        json={
            "kind": "mysql",
            "host": "db.example.net",
            "database": "sales",
            "user": "reader",
            "password": SECRET,
            "tables": ["orders"],
        },
    )
    assert landed.status_code == 200, landed.text
    bronze = landed.json()["tables"][0]["bronze_table"]
    pub = client.post(
        "/v1/studio/objects",
        json={
            "name": "order_fill",
            "relation": bronze,
            "columns": {"warehouse": "warehouse", "qty": "qty"},
            "space_id": FINANCE,
            "tool_name": "stock_by_warehouse",
            "tool_sql": (
                "SELECT warehouse, SUM(CAST(qty AS DOUBLE)) AS stock_on_hand "
                "FROM objects.order_fill GROUP BY warehouse"
            ),
            "tool_description": "Stock on hand by warehouse.",
        },
    )
    assert pub.status_code == 200, pub.text
    assert pub.json()["silver"] == "silver.order_fill"
    hit = client.post(
        "/v1/chat/ask",
        json={
            "question": "stock_by_warehouse",
            "space_id": FINANCE,
            "session_id": "ses_pie_obj",
            "grounded_tables": ["objects.order_fill"],
        },
    )
    assert hit.status_code == 200, hit.text
    env = hit.json()
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["route"] == "metric_tool"
    assert env["chart"]["kind"] == "pie"
    assert "Insights:" in env["text"]
    assert "group(s)" in env["text"]
    assert cortex.asks == []


def test_sqlserver_extract_object_tool_ask_is_certified_chart(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQL Server is extract-only into bronze, then the same objects.* + tool path."""
    con = _FakeConnection(
        [("dbo", "orders")], (["order_id", "amount"], [["A-1", "10.50"]])
    )
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    cortex = _MarkerCortex()
    client = _live_client(warehouse, monkeypatch, cortex)
    landed = client.post(
        "/v1/studio/sources/sql",
        json={
            "kind": "sqlserver",
            "host": "db.example.net",
            "database": "sales",
            "user": "reader",
            "password": SECRET,
            "tables": ["orders"],
        },
    )
    assert landed.status_code == 200, landed.text
    body = landed.json()
    assert "sqlserver://" in body["source"]
    bronze = body["tables"][0]["bronze_table"]
    pub = client.post(
        "/v1/studio/objects",
        json={
            "name": "mssql_order",
            "relation": bronze,
            "columns": {"id": "order_id", "order_value": "amount"},
            "space_id": FINANCE,
            "tool_name": "mssql_order_count",
            "tool_sql": "SELECT COUNT(*) AS row_count FROM objects.mssql_order",
            "tool_description": "Row count of SQL Server orders.",
        },
    )
    assert pub.status_code == 200, pub.text
    assert pub.json()["objects"] == ["objects.mssql_order"]
    assert pub.json()["silver"] == "silver.mssql_order"
    lake = duckdb.connect(str(warehouse))
    try:
        gold = lake.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'gold' AND table_name = 'mssql_order'"
        ).fetchone()
        assert gold is not None and int(gold[0]) == 0
    finally:
        lake.close()
    hit = client.post(
        "/v1/chat/ask",
        json={
            "question": "mssql_order_count",
            "space_id": FINANCE,
            "session_id": "ses_mssql_obj",
            "grounded_tables": ["objects.mssql_order"],
        },
    )
    assert hit.status_code == 200, hit.text
    env = hit.json()
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["route"] == "metric_tool"
    assert env["metric_tool"] == "mssql_order_count"
    assert int(env["rows"][0]["row_count"]) == 1
    assert env["chart"]["kind"] == "bignum"
    assert "Insights:" in env["text"]
    assert cortex.asks == []

