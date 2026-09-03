"""LINEAGE-03: promote targets appear on the company-default Library tree."""

from __future__ import annotations

from pathlib import Path

import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor.pipeline_loader import load_pipeline_yaml
from dms_executor.promote import run_promote
from fastapi.testclient import TestClient
from test_pipeline_promote import PIPE_YAML, _seed_sales

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.library as library_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        library_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "lineage03.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


def _leaves(tree: dict, folder_id: str) -> list[dict]:
    for node in tree["nodes"]:
        if node.get("id") == folder_id:
            return list(node.get("children") or [])
    return []


def test_silver_sales_node_after_promote_and_demo_tables_remain(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_sales(warehouse, bad_frac=0.1, n=100)
    run_promote(load_pipeline_yaml(PIPE_YAML), path=warehouse)
    _gate_allows(monkeypatch)
    tree = TestClient(create_app()).get("/v1/library/tree").json()
    silver = _leaves(tree, "folder:silver")
    targets = [n["meta"]["target"] for n in silver]
    assert "silver.sales" in targets
    node = next(n for n in silver if n["meta"]["target"] == "silver.sales")
    assert node["id"] == "silver:silver.sales"
    warehouse_labels = {n["label"] for n in _leaves(tree, "folder:warehouse")}
    assert {
        "transactions",
        "locations",
        "inventory",
        "suppliers",
        "shipments",
    } <= warehouse_labels
    assert "alerts" not in warehouse_labels


def test_company_default_lists_empty_silver_gold_folders_before_promote(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    tree = TestClient(create_app()).get("/v1/library/tree").json()
    ids = {n["id"] for n in tree["nodes"]}
    assert "folder:silver" in ids
    assert "folder:gold" in ids
    assert _leaves(tree, "folder:silver") == []
    assert _leaves(tree, "folder:gold") == []


def test_preview_warehouse_table_refuses_silver_sales(warehouse: Path) -> None:
    from dms_executor.warehouse_browse import preview_warehouse_table

    with pytest.raises(ValueError, match="disallowed"):
        preview_warehouse_table("silver.sales", path=warehouse)


def test_named_space_hides_promote_targets(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_sales(warehouse, bad_frac=0.1, n=100)
    run_promote(load_pipeline_yaml(PIPE_YAML), path=warehouse)
    _gate_allows(monkeypatch)
    tree = TestClient(create_app()).get(
        "/v1/library/tree", params={"space_id": FINANCE}
    ).json()
    ids = {n["id"] for n in tree["nodes"]}
    assert "folder:silver" not in ids
    assert "folder:gold" not in ids
    assert "folder:warehouse" in ids
    warehouse_labels = {n["label"] for n in _leaves(tree, "folder:warehouse")}
    assert warehouse_labels == {
        "locations",
        "inventory",
        "transactions",
        "suppliers",
    }
    dumped = str(tree)
    assert "silver.sales" not in dumped
