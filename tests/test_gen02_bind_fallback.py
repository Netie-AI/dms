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
    assert "below_reorder_lots" in onto.measures
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
    assert "hybrid_fuse" in (ctx.get("methods") or [])
    assert "ontology" in (ctx.get("methods") or [])
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
    q = "Rank suppliers by combined risk and lead time score"
    ctx = retrieve_short_context(q, warehouse=db, grantable=grants, ontology=onto)
    assert bind_plan(q, ctx) is None


def test_bind_plan_above_90_keep_gt(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    q = "Which locations are above 90 percent capacity?"
    ctx = retrieve_short_context(
        q,
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        ontology=onto,
    )
    out = bind_plan(q, ctx)
    assert out is not None
    plan = out["query_plan"]
    assert plan["measure"] == "utilisation_pct"
    assert plan["keep_gt"] == 90.0
    assert plan["group_by"] == [["location", "location_code"]]


def test_bind_plan_types_cold_storage_and_wh_a(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    grants = {"inventory", "locations", "transactions", "suppliers"}
    ctx = retrieve_short_context(
        "Which locations are cold storage?",
        warehouse=db,
        grantable=grants,
        ontology=onto,
    )
    assert "spine_yaml" in (ctx.get("methods") or [])
    out = bind_plan("Which locations are cold storage?", ctx)
    assert out is not None
    plan = out["query_plan"]
    assert plan["measure"] == "utilisation_pct"
    assert ["location", "is_cold_storage", "=", True] in plan["filters"]
    q2 = "Show the CCTV camera for warehouse A"
    ctx2 = retrieve_short_context(q2, warehouse=db, grantable=grants, ontology=onto)
    out2 = bind_plan(q2, ctx2)
    assert out2 is not None
    assert out2["query_plan"]["group_by"] == [["location", "cctv_camera_id"]]
    assert any(
        f[0] == "location" and f[1] == "location_code" for f in out2["query_plan"]["filters"]
    )


def test_generative_cold_storage_validates(tmp_path: Path) -> None:
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        "Which locations are cold storage?",
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        compute=lambda _c: None,
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert "is_cold_storage" in (env.get("sql_used") or "")


def test_generative_above_90_keep_gt_validates(tmp_path: Path) -> None:
    from dms_executor.demo_warehouse import connect_file

    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))

    def submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_gt", output={"rows": rows})

    env = maybe_generative_ask(
        "Which locations are above 90 percent capacity?",
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        compute=lambda _c: None,
        submit=submit,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["rows"]
    for row in env["rows"]:
        nums = [v for v in row.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        assert nums and max(float(v) for v in nums) > 90


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
        bind_on_miss=True,
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
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True


def test_product_path_compute_miss_does_not_bind(tmp_path: Path) -> None:
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
    assert env is None


def test_isolated_gen_untyped_miss_abstains_after_retrieve(tmp_path: Path) -> None:
    """Founder lock: try retrieve+bind, then ABSTAIN. Do not silent-None the gen lane."""
    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        "Rank suppliers by combined risk and lead time score",
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        compute=lambda _c: None,
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    notes = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "query_plan was not typed after retrieve" in notes


def test_ontology_spine_yaml_is_slot_names_not_sql(tmp_path: Path) -> None:
    import yaml

    db = tmp_path / "thin.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    root = Path(__file__).resolve().parents[1]
    spine_path = root / "packages" / "executor" / "dms_executor" / "ontology_spine.yaml"
    blob = spine_path.read_text(encoding="utf-8")
    assert "SUM(" not in blob.upper()
    assert "SELECT" not in blob.upper()
    data = yaml.safe_load(blob)
    assert data["kind"] == "dms.ontology_spine"
    assert data["not_certified_sql"] is True
    assert data["source"] == "demo_ontology"
    for name in data["objects"]:
        assert name in onto.objects, name
    for name in data["measures"]:
        assert name in onto.measures, name
