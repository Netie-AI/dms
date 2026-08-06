"""STUDIO-01 / P-DMS-26 — ingest carries space_id; grants and lists agree."""

from __future__ import annotations

from pathlib import Path

import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WAREHOUSE_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"


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
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "studio_space.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


def test_ingest_records_space_and_filters_bronze_list(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        data={"space_id": FINANCE},
        files={"file": ("sales.csv", b"sku,qty\nA,1\nB,2\n", "text/csv")},
    )
    assert r.status_code == 200
    table = r.json()["table"]
    assert table

    fin_bronze = client.get(f"/v1/studio/bronze?space_id={FINANCE}").json()
    ops_bronze = client.get(f"/v1/studio/bronze?space_id={WAREHOUSE_OPS}").json()
    fin_tables = {row["table"] for row in fin_bronze}
    ops_tables = {row["table"] for row in ops_bronze}
    assert table in fin_tables
    assert table not in ops_tables

    tree = client.get(f"/v1/library/tree?space_id={FINANCE}").json()
    bronze_ids = {
        n["id"]
        for folder in tree["nodes"]
        if folder["id"] == "folder:bronze"
        for n in folder.get("children") or []
    }
    assert f"bronze:{table}" in bronze_ids


def test_upload_grantable_only_in_ingest_space(warehouse: Path) -> None:
    from dms_core.ask import GroundingRefused
    from dms_executor import Executor
    from dms_executor.bronze import ingest_csv_bytes

    receipt = ingest_csv_bytes(
        filename="units.csv",
        data=b"sku,qty\nA,3\n",
        path=warehouse,
        space_id=FINANCE,
    )
    table = receipt.table
    assert table

    exe = Executor(cortex=None, warehouse_path=warehouse)
    acl = exe.demo_acl(session_id="ses_fin", space_id=FINANCE, tables=[table])
    assert table in acl.row_predicates

    with pytest.raises(GroundingRefused) as caught:
        exe.demo_acl(session_id="ses_ops", space_id=WAREHOUSE_OPS, tables=[table])
    assert table in caught.value.ungrantable


def test_live_demo_hides_offline_company_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    get_settings.cache_clear()
    client = TestClient(create_app())
    sources = client.get("/v1/library/sources").json()
    refs = {s["ref"] for s in sources}
    assert "Company/finance/gl_export.csv" not in refs
    assert any("Finance/" in ref for ref in refs)
