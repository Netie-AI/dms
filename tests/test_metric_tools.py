"""Allowlisted metric tools -- the model names a tool, DuckDB runs authored SQL."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.manifest import SecurityEvent
from dms_executor.metric_tools import (
    MetricTool,
    UnknownMetricTool,
    call_metric_tool,
    list_metric_tools,
)


def test_list_metric_tools_hides_sql() -> None:
    listed = list_metric_tools()
    assert listed
    assert set(listed[0]) == {"name", "description"}
    assert "SELECT" not in listed[0]["description"].upper()


def test_call_metric_tool_runs_authored_select() -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE t (revenue DOUBLE)")
        con.execute("INSERT INTO t VALUES (10), (5)")
        tool = MetricTool(
            name="revenue_total",
            sql="SELECT SUM(revenue) AS revenue FROM t",
            description="Total revenue",
        )
        out = call_metric_tool(con, "revenue_total", tools={"revenue_total": tool})
        assert out["sql"].startswith("SELECT")
        assert out["rows"][0]["revenue"] == 15
        assert out["row_count"] == 1
    finally:
        con.close()


def test_unknown_tool_refuses() -> None:
    con = duckdb.connect(":memory:")
    try:
        with pytest.raises(UnknownMetricTool):
            call_metric_tool(con, "drop_everything")
    finally:
        con.close()


def test_update_tool_is_refused() -> None:
    con = duckdb.connect(":memory:")
    try:
        tool = MetricTool(
            name="bad",
            sql="UPDATE t SET x = 1",
            description="no",
        )
        with pytest.raises(SecurityEvent):
            call_metric_tool(con, "bad", tools={"bad": tool})
    finally:
        con.close()


def test_maybe_metric_ask_hits_name_and_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.metric_tools import maybe_metric_ask

    db = tmp_path / "metric.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    hit = maybe_metric_ask("stock_on_hand", warehouse=db, grantable={"inventory"})
    alias = maybe_metric_ask("stock on hand?", warehouse=db, grantable={"inventory"})
    assert hit is not None and alias is not None
    assert_envelope_valid(hit)
    assert hit["badge"] == "L0_CERTIFIED"
    assert hit["route"] == "metric_tool"
    assert hit["metric_tool"] == "stock_on_hand"
    assert hit["sql_used"].startswith("SELECT")
    assert hit["chart"]["kind"] == "bignum"
    assert float(hit["rows"][0]["stock_on_hand"]) == 7890.0
    assert "Insights:" in hit["text"]
    assert hit["suggestions"] == ["stock_on_hand"]
    assert alias["rows"][0]["stock_on_hand"] == hit["rows"][0]["stock_on_hand"]


def test_maybe_metric_ask_misses_free_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.metric_tools import maybe_metric_ask

    db = tmp_path / "metric_miss.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    assert maybe_metric_ask("What is total inventory quantity?", warehouse=db) is None
    assert maybe_metric_ask("stock_on_hand", warehouse=db, grantable=set()) is None
    assert (
        maybe_metric_ask(
            "stock_on_hand",
            warehouse=db,
            grantable={"inventory"},
            tables=["transactions"],
        )
        is None
    )


def test_ask_metric_tool_is_l0_without_cortex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/chat/ask names the tool; Cortex is not called."""
    from dataclasses import dataclass, field
    from typing import Any
    from unittest.mock import MagicMock

    from cortex_client.models import AskRequest, AskResponse
    from cortex_contract.execution import Manifest, QueryResult
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.manifest import ManifestMinter, SessionAcl
    from fastapi.testclient import TestClient

    finance = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    db = tmp_path / "metric_ask.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(db)
    get_settings.cache_clear()

    @dataclass
    class _MarkerCortex:
        asks: list[AskRequest] = field(default_factory=list)

        def submit(self, req: Any) -> QueryResult:
            return QueryResult(ok=True, status="bound", run_id="run_metric")

        def ask(self, req: AskRequest) -> AskResponse:
            self.asks.append(req)
            return AskResponse(
                answer="Cortex marker.",
                badge="certified",
                sql_used="SELECT 1 AS cortex_marker",
                rows=[{"cortex_marker": 1}],
                assumptions="metric-isolation",
                audit_id="aud_metric_cortex",
                route="sql",
            )

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

    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    cortex = _MarkerCortex()
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=m,
        warehouse_path=db,
    )
    app.state.cortex = cortex
    client = TestClient(app)
    hit = client.post(
        "/v1/chat/ask",
        json={
            "question": "stock_on_hand",
            "space_id": finance,
            "session_id": "ses_metric_fin",
        },
    )
    assert hit.status_code == 200, hit.text
    env = hit.json()
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["route"] == "metric_tool"
    assert env["metric_tool"] == "stock_on_hand"
    assert float(env["rows"][0]["stock_on_hand"]) == 7890.0
    assert env["chart"]["kind"] == "bignum"
    assert cortex.asks == []

    miss = client.post(
        "/v1/chat/ask",
        json={
            "question": "What is total inventory quantity?",
            "space_id": finance,
            "session_id": "ses_metric_miss",
        },
    )
    assert miss.status_code == 200, miss.text
    foreign = miss.json()
    assert foreign.get("route") != "metric_tool"
    assert cortex.asks != []

def test_grouped_metric_chart_is_pie() -> None:
    from dms_executor.metric_tools import _chart_for_rows

    chart = _chart_for_rows(
        [
            {"category": "Electronics", "revenue": 12},
            {"category": "Food", "revenue": 5},
        ],
        "sales_mix",
    )
    assert chart == {
        "kind": "pie",
        "x": "category",
        "y": "revenue",
        "title": "sales_mix",
    }


def test_wide_or_negative_metric_chart_stays_hbar() -> None:
    from dms_executor.metric_tools import _chart_for_rows

    wide = _chart_for_rows([{"c": str(i), "n": i} for i in range(9)], "wide")
    assert wide is not None and wide["kind"] == "hbar"
    neg = _chart_for_rows(
        [{"c": "a", "n": 1}, {"c": "b", "n": -2}],
        "neg",
    )
    assert neg is not None and neg["kind"] == "hbar"


def test_time_series_metric_chart_is_line() -> None:
    from dms_executor.metric_tools import _chart_for_rows

    chart = _chart_for_rows(
        [
            {"month": "2024-01-01", "revenue": 10},
            {"month": "2024-02-01", "revenue": 12},
            {"month": "2024-03-01", "revenue": 9},
        ],
        "revenue_by_month",
    )
    assert chart == {
        "kind": "line",
        "x": "month",
        "y": "revenue",
        "title": "revenue_by_month",
    }
    sku = _chart_for_rows(
        [{"sku": "SKU-1", "n": 1}, {"sku": "SKU-2", "n": 2}],
        "sku",
    )
    assert sku is not None and sku["kind"] == "pie"

def test_grouped_tool_ask_is_pie_with_insights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.metric_tools import MetricTool, maybe_metric_ask

    db = tmp_path / "metric_pie.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    tool = MetricTool(
        name="stock_by_warehouse",
        sql=(
            "SELECT location_id, SUM(quantity_kg) AS stock_on_hand "
            "FROM inventory GROUP BY location_id"
        ),
        description="Stock on hand by warehouse.",
    )
    env = maybe_metric_ask(
        "stock_by_warehouse",
        warehouse=db,
        grantable={"inventory"},
        tools=(tool,),
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["chart"]["kind"] == "pie"
    assert env["chart"]["x"] == "location_id"
    assert "Insights:" in env["text"]
    assert "group(s)" in env["text"]
    assert "Highest stock_on_hand" in env["text"]

