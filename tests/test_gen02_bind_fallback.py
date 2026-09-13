"""GEN-02: Cortex compute miss binds retrieved ontology; traps stay ungreen.

Does not expand certified packs. Offline counts only. Not COMPLETE.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.semantic_retrieve import bind_plan, retrieve_short_context


def _submit_ok(_sql: str) -> Any:
    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_gen02",
        output={"rows": [{"sku": "SKU-ALPHA", "outbound_value_myr": 10.0}]},
    )


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_gen02", hash="hash_gen02_not_entry")


def test_demo_ontology_verifies_on_thin_reseed(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    assert onto.verified
    assert "storage_bin" not in onto.objects["lot"].key
    assert "sku_count" in onto.measures
    assert "outbound_kg" in onto.measures
    assert "ship_from_supplier" not in onto.links


def test_bind_plan_stock_value_by_category(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    ctx = retrieve_short_context(
        "What is total stock value by category?",
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        ontology=onto,
    )
    out = bind_plan("What is total stock value by category?", ctx)
    assert out is not None
    assert out.get("unsure") is not True
    plan = out["query_plan"]
    assert plan["measure"] == "stock_value_myr"
    assert plan["group_by"] == [["product", "category"]]


def test_bind_plan_quantity_sold_is_not_net_movement(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    q = "Top 3 SKUs by quantity sold"
    ctx = retrieve_short_context(
        q,
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        ontology=onto,
    )
    out = bind_plan(q, ctx)
    assert out is not None
    assert out["query_plan"]["measure"] == "outbound_kg"
    assert out["query_plan"]["group_by"] == [["product", "sku"]]
    assert out["query_plan"]["limit"] == 3


def test_bind_plan_misses_list_and_untyped_filter(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    grants = {"inventory", "locations", "transactions", "suppliers"}
    for q in (
        "List chemicals in inventory",
        "Which locations are cold storage?",
        "Which SKUs are below reorder level in warehouse A?",
        "Show the CCTV camera for warehouse A?",
        "Which locations are above 90 percent capacity?",
    ):
        ctx = retrieve_short_context(q, warehouse=db, grantable=grants, ontology=onto)
        assert bind_plan(q, ctx) is None, q


def test_compute_miss_binds_and_validates(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        "What is total stock value by category?",
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        compute=lambda _c: None,
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["sql_used"]
    assert "stock_value" in (env["sql_used"] or "").lower()
    assert any("compute_fallback:bind_plan" in str(a) for a in (env.get("assumptions") or []))


def test_compute_unsure_is_not_overridden(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    submits: list[str] = []
    env = maybe_generative_ask(
        "What is total stock value by category?",
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        compute=lambda _c: {"unsure": True},
        submit=lambda sql: submits.append(sql) or _submit_ok(sql),
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert submits == []


def test_ops_spend_does_not_green_ungranted_suppliers(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        "What is our total spend by supplier country?",
        warehouse=db,
        grantable={"inventory", "locations", "shipments"},
        compute=lambda _c: None,
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
