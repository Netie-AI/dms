"""Constructor catalog is a source document, not a second ontology compiler."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from constructor_source import (  # noqa: E402
    FIXTURE_CATALOG,
    ConstructorSourceError,
    catalog_as_ask_plan,
    catalog_as_ingest_plan,
    pick_space_for_object,
    stage_catalog,
    validate_catalog,
)


def test_fixture_catalog_stages(tmp_path):
    dest = tmp_path / "stage.json"
    path = stage_catalog(FIXTURE_CATALOG, dest)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "inventory" in data["objects"]
    assert data["objects"]["inventory"]["primary_key"] == "sku"


def test_foundry_clone_dump_is_refused():
    with pytest.raises(ConstructorSourceError, match="Foundry"):
        validate_catalog({"ok": True, "objects": {"Place": {}}, "osdk": {"bundle": True}})


def test_empty_objects_refused():
    with pytest.raises(ConstructorSourceError, match="no objects"):
        validate_catalog({"ok": True, "objects": {}})


def test_catalog_becomes_ingest_plan_not_ontology_compile():
    plan = catalog_as_ingest_plan(FIXTURE_CATALOG)
    assert plan["source"] == "constructor"
    names = [t["name"] for t in plan["tables"]]
    assert names == ["inventory", "shipments"]
    assert plan["tables"][0]["primary_key"] == "sku"
    assert "qty" in plan["tables"][0]["columns"]
    asks = catalog_as_ask_plan(FIXTURE_CATALOG)
    assert [a["object"] for a in asks["asks"]] == ["inventory", "shipments"]
    assert "What is total stock value by category?" in {a["question"] for a in asks["asks"]}
    spaces = [
        {"id": "fin", "tables": ["inventory", "suppliers"]},
        {"id": "ops", "tables": ["inventory", "shipments"]},
    ]
    assert pick_space_for_object("shipments", spaces) == "ops"
    assert pick_space_for_object("suppliers", spaces) == "fin"
    assert pick_space_for_object("alerts", spaces) is None
    routed = catalog_as_ask_plan(FIXTURE_CATALOG, spaces=spaces)
    by_obj = {a["object"]: a["space_id"] for a in routed["asks"]}
    assert by_obj["inventory"] == "fin"
    assert by_obj["shipments"] == "ops"
    src = Path(__file__).resolve().parents[1] / "scripts" / "constructor_source.py"
    text = src.read_text(encoding="utf-8")
    assert "import ontology" not in text
    assert "from ontology" not in text
    assert "scripts/ontology.py" in text
