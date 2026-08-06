"""Library sources and previews respect active Space (SPACE-UI-ALL)."""

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

    path = tmp_path / "library_space.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


def test_finance_sources_exclude_company_gl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    get_settings.cache_clear()
    client = TestClient(create_app())

    fin = client.get(f"/v1/library/sources?space_id={FINANCE}").json()
    fin_refs = {s["ref"] for s in fin}
    assert "Company/finance/gl_export.csv" not in fin_refs
    assert any(ref.startswith("Finance/") for ref in fin_refs)

    all_src = client.get("/v1/library/sources").json()
    assert any(s["ref"] == "Company/finance/gl_export.csv" for s in all_src)


def test_finance_tree_excludes_company_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())
    tree = client.get(f"/v1/library/tree?space_id={FINANCE}").json()

    def walk(nodes: list[dict]) -> list[str]:
        out: list[str] = []
        for n in nodes:
            if n.get("id", "").startswith("source:"):
                out.append(str((n.get("meta") or {}).get("ref") or ""))
            out.extend(walk(n.get("children") or []))
        return out

    refs = set(walk(tree["nodes"]))
    assert "Company/finance/gl_export.csv" not in refs
    assert any(r.startswith("Finance/") for r in refs)


def test_warehouse_preview_refused_outside_space(warehouse: Path) -> None:
    client = TestClient(create_app())
    ok = client.get(f"/v1/library/warehouse/shipments/preview?space_id={WAREHOUSE_OPS}")
    assert ok.status_code == 200

    bad = client.get(f"/v1/library/warehouse/shipments/preview?space_id={FINANCE}")
    assert bad.status_code == 403
    assert bad.json()["detail"]["code"] == "warehouse_not_in_space"


def test_bronze_preview_refused_outside_ingest_space(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        data={"space_id": FINANCE},
        files={"file": ("scoped.csv", b"sku,qty\nA,1\n", "text/csv")},
    )
    assert r.status_code == 200
    table = r.json()["table"]
    assert table

    ok = client.get(
        f"/v1/library/bronze/{table}/preview?space_id={FINANCE}&limit=10"
    )
    assert ok.status_code == 200

    bad = client.get(
        f"/v1/library/bronze/{table}/preview?space_id={WAREHOUSE_OPS}&limit=10"
    )
    assert bad.status_code == 403
    assert bad.json()["detail"]["code"] == "bronze_not_in_space"


def test_data_map_warehouse_scoped_to_space(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())
    fin = client.get(f"/v1/library/data-map?space_id={FINANCE}").json()
    fin_tables = {t["table"] for t in fin["warehouse_tables"]}
    assert "transactions" in fin_tables
    assert "shipments" not in fin_tables
