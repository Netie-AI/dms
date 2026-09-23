"""ONTOLOGY-MULTIGRAIN-02 / ONTOLOGY-MULTIGRAIN-01 / ONTOLOGY-COMPILE-01.

≥2 supply-chain grains (sku/supplier/plant/lane/day). try_compile_multi_grain
runs before one-grain GEN-01 plan. A granted plant join compiles; an
ungranted shipments path ABSTAINS ``missing_join``/plant -- never bare
``validate:ungranted:shipments``. bind_plan is not the confident path.
Not #178 COMPLETE. Not a bind_plan overlay. Bar (1) stays KEEP_HOLD.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import LedgerAppendResponse
from cortex_contract.execution import QueryResult
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import (
    CompiledQuery,
    Ontology,
    Refusal,
    demo_ontology,
    detect_supply_chain_grains,
    missing_join_for_ungranted,
    relation_tables,
    try_compile_multi_grain,
)


def _create_sc(con: Any) -> None:
    con.execute("CREATE TABLE plants (plant_id VARCHAR PRIMARY KEY, name VARCHAR)")
    con.execute("INSERT INTO plants VALUES ('P1','Plant 1'),('P2','Plant 2')")
    con.execute("CREATE TABLE products (sku VARCHAR PRIMARY KEY, category VARCHAR)")
    con.execute("INSERT INTO products VALUES ('SKU-A','RAW'),('SKU-B','PACK')")
    con.execute("CREATE TABLE suppliers (supplier_id VARCHAR PRIMARY KEY, name VARCHAR)")
    con.execute("INSERT INTO suppliers VALUES ('S1','North'),('S2','South')")
    con.execute("CREATE TABLE lanes (lane_id VARCHAR PRIMARY KEY, plant_id VARCHAR)")
    con.execute("INSERT INTO lanes VALUES ('L1','P1'),('L2','P2')")
    con.execute(
        "CREATE TABLE shipments ("
        "shipment_id VARCHAR PRIMARY KEY, sku VARCHAR, plant_id VARCHAR, "
        "lane_id VARCHAR, origin_id VARCHAR, supplier_id VARCHAR, cost DOUBLE)"
    )
    con.execute(
        "INSERT INTO shipments VALUES "
        "('H1','SKU-A','P1','L1','P2','S1',40),"
        "('H2','SKU-B','P1','L1','P2','S1',60),"
        "('H3','SKU-A','P2','L2','P1','S2',10)"
    )


def _con():  # noqa: ANN201
    import duckdb

    c = duckdb.connect(":memory:")
    _create_sc(c)
    return c


@pytest.fixture()
def con():  # noqa: ANN201
    c = _con()
    try:
        yield c
    finally:
        c.close()


def _ontology(*, supplier_link: bool = False, second_plant: bool = False) -> Ontology:
    o = Ontology()
    o.add_object("shipment", "shipments", ["shipment_id"])
    o.add_object("product", "products", ["sku"])
    o.add_object("plant", "plants", ["plant_id"])
    o.add_object("supplier", "suppliers", ["supplier_id"])
    o.add_object("lane", "lanes", ["lane_id"])
    o.add_link("ship_of_product", "shipment", ["sku"], "product", ["sku"])
    o.add_link("ship_to_plant", "shipment", ["plant_id"], "plant", ["plant_id"])
    o.add_link("ship_on_lane", "shipment", ["lane_id"], "lane", ["lane_id"])
    o.add_link("lane_to_plant", "lane", ["plant_id"], "plant", ["plant_id"])
    if supplier_link:
        o.add_link(
            "ship_from_supplier",
            "shipment",
            ["supplier_id"],
            "supplier",
            ["supplier_id"],
        )
    if second_plant:
        o.add_link("ship_from_plant", "shipment", ["origin_id"], "plant", ["plant_id"])
    o.add_measure(
        "shipping_cost_myr",
        "shipment",
        "SUM(f.cost)",
        description="shipment cost / freight billed, one contribution per shipment",
    )
    o.add_measure(
        "stock_value_myr",
        "shipment",
        "SUM(f.cost)",
        description="stock value / carrying value / inventory spend, one contribution per lot",
    )
    return o


def test_detect_two_grains_and_ignores_warehouse_climb_l0s() -> None:
    assert detect_supply_chain_grains("shipping cost by SKU and plant") == (
        "sku",
        "plant",
    )
    assert detect_supply_chain_grains("stock value by supplier and SKU") == (
        "sku",
        "supplier",
    )
    # Climb L0s that say warehouse/shipment must not look like ≥2 grains.
    assert detect_supply_chain_grains("SKU count by category") == ("sku",)
    assert detect_supply_chain_grains(
        "Which SKUs are below reorder level in warehouse A?"
    ) == ("sku",)
    assert detect_supply_chain_grains("shipment cost by SKU and day") == (
        "sku",
        "day",
    )
    assert detect_supply_chain_grains("shipment cost by SKU and lane") == (
        "sku",
        "lane",
    )


def test_two_grain_compile_ranks_where_paths_and_conserves(con) -> None:  # noqa: ANN001
    o = _ontology()
    assert not o.verify(con)
    got = o.compile_grains("shipping_cost_myr", ["sku", "plant"])
    assert isinstance(got, CompiledQuery)
    assert {p.grain for p in got.where_paths} == {"sku", "plant"}
    by_grain = {p.grain: p for p in got.where_paths if p.importance == 1}
    assert by_grain["sku"].steps == ("shipment", "product")
    assert by_grain["plant"].steps == ("shipment", "plant")
    assert by_grain["plant"].importance == 1
    longer = [p for p in got.where_paths if p.grain == "plant" and p.importance > 1]
    assert longer, "the two-hop lane route must still be ranked, not dropped"
    assert longer[0].importance == 2
    assert "lane" in longer[0].steps
    assert any("importance=1" in n for n in got.notes)
    rows = con.execute(got.sql).fetchall()
    assert sum(r[-1] for r in rows) == 110.0
    text = " ".join(str(r) for r in rows)
    assert "SKU-A" in text and "P1" in text


def test_compile_group_by_two_grains_attaches_ranked_paths(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    got = o.compile(
        "shipping_cost_myr",
        group_by=[("product", "sku"), ("plant", "plant_id")],
    )
    assert isinstance(got, CompiledQuery)
    assert len({p.grain for p in got.where_paths}) >= 2
    assert got.where_paths[0].importance == 1


def test_missing_day_join_abstains_naming_day(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    got = o.compile_grains("shipping_cost_myr", ["sku", "day"])
    assert isinstance(got, Refusal)
    assert got.reason == "missing_join"
    assert "day" in got.detail.lower()
    assert "undeclared" in got.detail.lower() or "join" in got.detail.lower()


def test_missing_metric_abstains_naming_the_measure(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    got = o.compile_grains("margin_usd", ["sku", "plant"])
    assert isinstance(got, Refusal)
    assert got.reason == "missing_metric"
    assert "margin_usd" in got.detail
    assert "sku" in got.detail and "plant" in got.detail


def test_missing_supplier_join_names_the_join(con) -> None:  # noqa: ANN001
    o = _ontology(supplier_link=False)
    o.verify(con)
    got = o.compile_grains("shipping_cost_myr", ["sku", "supplier"])
    assert isinstance(got, Refusal)
    assert got.reason == "missing_join"
    assert "supplier" in got.detail.lower()
    assert "shipment" in got.detail.lower()


def test_equal_importance_plant_paths_abstain_not_pick(con) -> None:  # noqa: ANN001
    o = _ontology(second_plant=True)
    o.verify(con)
    got = o.compile_grains("shipping_cost_myr", ["sku", "plant"])
    assert isinstance(got, Refusal)
    assert got.reason == "ambiguous_path"
    assert "plant" in got.detail.lower()
    assert "ship_to_plant" in got.detail and "ship_from_plant" in got.detail


def test_try_compile_multi_grain_skips_single_grain_climb_asks(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    assert try_compile_multi_grain(o, "shipping_cost_myr", "SKU count by category") is None
    assert try_compile_multi_grain(o, None, "shipment cost by destination") is None


def test_try_compile_multi_grain_is_ontology_plan_not_bind(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    got = try_compile_multi_grain(
        o, "shipping_cost_myr", "shipment cost by SKU and plant"
    )
    assert isinstance(got, CompiledQuery)
    assert got.where_paths
    assert not any("bind_plan" in n for n in got.notes)


def _seed(path: Path) -> None:
    import duckdb

    dst = duckdb.connect(str(path))
    try:
        _create_sc(dst)
    finally:
        dst.close()


def _sku_only_plan(measure: str = "shipping_cost_myr") -> dict[str, Any]:
    """Live KEEP_HOLD payload: ranking filled a one-grain GEN-01 plan."""
    return {
        "query_plan": {
            "measure": measure,
            "group_by": [["product", "sku"]],
        },
        "plan_source": "ontology_plan",
    }


def _ask(
    tmp_path: Path,
    question: str,
    *,
    payload: dict[str, Any],
    ontology: Ontology | None = None,
    bind_on_miss: bool = True,
    must_submit: bool = True,
) -> dict[str, Any]:
    warehouse = tmp_path / "sc.duckdb"
    if not warehouse.is_file():
        _seed(warehouse)
    from dms_executor.generative_ask import load_verified_ontology

    onto = load_verified_ontology(warehouse, ontology or _ontology())
    assert onto is not None
    submits: list[str] = []

    def submit(sql: str) -> QueryResult:
        submits.append(sql)
        import duckdb

        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            cols = [d[0] for d in con.execute(sql).description]
            rows = [dict(zip(cols, r, strict=True)) for r in con.execute(sql).fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_oc01", output={"rows": rows})

    def boom(_s: str) -> QueryResult:
        raise AssertionError("must not submit")

    env = maybe_generative_ask(
        question,
        warehouse=warehouse,
        grantable={"shipments", "products", "plants", "lanes", "suppliers"},
        compute=lambda _c: payload,
        submit=submit if must_submit else boom,
        ledger_append=(
            (lambda _b: LedgerAppendResponse(entry_id="led_oc01", hash="hash_oc01_not_entry"))
            if must_submit
            else (lambda _b: (_ for _ in ()).throw(AssertionError("must not append")))
        ),
        ontology=onto,
        bind_on_miss=bind_on_miss,
    )
    assert env is not None
    env["_submits"] = submits
    return env


def _grains_on_envelope(env: dict[str, Any]) -> set[str]:
    return {str(p.get("grain")) for p in (env.get("where_paths") or []) if p.get("grain")}


def test_ask_multi_join_is_ontology_plan_not_bind_plan(tmp_path: Path) -> None:
    warehouse = tmp_path / "sc.duckdb"
    _seed(warehouse)
    from dms_executor.generative_ask import load_verified_ontology

    onto = load_verified_ontology(warehouse, _ontology())
    assert onto is not None
    submits: list[str] = []

    def submit(sql: str) -> QueryResult:
        submits.append(sql)
        import duckdb

        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            cols = [d[0] for d in con.execute(sql).description]
            rows = [dict(zip(cols, r, strict=True)) for r in con.execute(sql).fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_oc01", output={"rows": rows})

    env = maybe_generative_ask(
        "shipment cost by SKU and plant",
        warehouse=warehouse,
        grantable={"shipments", "products", "plants", "lanes", "suppliers"},
        compute=lambda _c: {"answer": "not a plan"},
        submit=submit,
        ledger_append=lambda _b: LedgerAppendResponse(
            entry_id="led_oc01", hash="hash_oc01_not_entry"
        ),
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env.get("plan_source") == "ontology_plan"
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert any("where+importance" in str(a) for a in (env.get("assumptions") or []))
    assert env["rows"]
    assert "Found" in str(env.get("text") or env.get("answer") or "")
    assert submits and "JOIN" in submits[0].upper()
    total = sum(float(r.get("shipping_cost_myr") or 0) for r in env["rows"])
    assert total == 110.0
    paths = env.get("where_paths") or []
    assert {p.get("grain") for p in paths} == {"sku", "plant"}
    assert any(int(p.get("importance") or 0) == 1 for p in paths)


def test_ask_missing_join_abstains_naming_day(tmp_path: Path) -> None:
    warehouse = tmp_path / "sc.duckdb"
    _seed(warehouse)
    from dms_executor.generative_ask import load_verified_ontology

    onto = load_verified_ontology(warehouse, _ontology())
    assert onto is not None
    env = maybe_generative_ask(
        "shipment cost by SKU and day",
        warehouse=warehouse,
        grantable={"shipments", "products", "plants", "lanes", "suppliers"},
        compute=lambda _c: {"answer": "not a plan"},
        submit=lambda _s: (_ for _ in ()).throw(AssertionError("must not submit")),
        ledger_append=lambda _b: (_ for _ in ()).throw(AssertionError("must not append")),
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env.get("plan_source") == "ontology_plan"
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "missing_join" in blob and "day" in blob.lower()
    assert "bind_plan" not in blob


def test_ask_missing_metric_abstains_naming_metric(tmp_path: Path) -> None:
    warehouse = tmp_path / "sc.duckdb"
    _seed(warehouse)
    from dms_executor.generative_ask import load_verified_ontology

    onto = load_verified_ontology(warehouse, _ontology())
    assert onto is not None
    env = maybe_generative_ask(
        "mystery_metric by SKU and plant",
        warehouse=warehouse,
        grantable={"shipments", "products", "plants", "lanes", "suppliers"},
        compute=lambda _c: {"answer": "not a plan"},
        submit=lambda _s: (_ for _ in ()).throw(AssertionError("must not submit")),
        ledger_append=lambda _b: (_ for _ in ()).throw(AssertionError("must not append")),
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "missing_metric" in blob
    assert "bind_plan" not in blob


def _assert_multi_grain_l2(env: dict[str, Any], grains: set[str], measure: str) -> None:
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env.get("plan_source") == "ontology_plan"
    notes = env.get("assumptions") or []
    assert not any("bind_plan" in str(a) for a in notes)
    assert any("ontology_compile:where+importance" in str(a) for a in notes)
    assert _grains_on_envelope(env) == grains
    tops = [p for p in (env.get("where_paths") or []) if int(p.get("importance") or 0) == 1]
    assert {p.get("grain") for p in tops} == grains
    assert env["rows"]
    text = str(env.get("text") or "")
    assert "Found" in text
    sql = str(env.get("sql_used") or "")
    assert "JOIN" in sql.upper()
    total = sum(float(r.get(measure) or 0) for r in env["rows"])
    assert total == 110.0
    assert env.get("_submits")


def test_keep_hold_sku_plant_beats_one_grain_plan(tmp_path: Path) -> None:
    """#234 live: ranking plan grouped product_sku only. Must not drop plant."""
    env = _ask(
        tmp_path,
        "shipping cost by SKU and plant",
        payload=_sku_only_plan("shipping_cost_myr"),
    )
    _assert_multi_grain_l2(env, {"sku", "plant"}, "shipping_cost_myr")
    sql = str(env.get("sql_used") or "").lower()
    assert "sku" in sql and "plant" in sql
    blob = " ".join(str(r) for r in env["rows"])
    assert "SKU-A" in blob and "P1" in blob
    assert "insights_ranking:ontology_plan" not in " ".join(
        str(a) for a in (env.get("assumptions") or [])
    )


def test_keep_hold_sku_day_abstains_missing_join_despite_plan(tmp_path: Path) -> None:
    """#234 live: L2 sku-only. Unit compile is missing_join day; plan must not win."""
    env = _ask(
        tmp_path,
        "shipment cost by SKU and day",
        payload=_sku_only_plan("shipping_cost_myr"),
        must_submit=False,
    )
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env.get("plan_source") == "ontology_plan"
    assert not env.get("where_paths")
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "missing_join" in blob and "day" in blob.lower()
    assert "bind_plan" not in blob
    text = str(env.get("text") or "").lower()
    assert "cannot certify" in text


def test_keep_hold_supplier_sku_beats_one_grain_plan(tmp_path: Path) -> None:
    env = _ask(
        tmp_path,
        "stock value by supplier and SKU",
        payload=_sku_only_plan("stock_value_myr"),
        ontology=_ontology(supplier_link=True),
    )
    _assert_multi_grain_l2(env, {"sku", "supplier"}, "stock_value_myr")
    sql = str(env.get("sql_used") or "").lower()
    assert "sku" in sql and "supplier" in sql
    blob = " ".join(str(r) for r in env["rows"])
    assert "SKU-A" in blob and "S1" in blob


def test_keep_hold_sku_lane_beats_one_grain_plan(tmp_path: Path) -> None:
    env = _ask(
        tmp_path,
        "shipment cost by SKU and lane",
        payload=_sku_only_plan("shipping_cost_myr"),
    )
    _assert_multi_grain_l2(env, {"sku", "lane"}, "shipping_cost_myr")
    sql = str(env.get("sql_used") or "").lower()
    assert "sku" in sql and "lane" in sql
    blob = " ".join(str(r) for r in env["rows"])
    assert "SKU-A" in blob and "L1" in blob


def test_keep_hold_one_grain_plan_does_not_bind(tmp_path: Path) -> None:
    env = _ask(
        tmp_path,
        "shipping cost by SKU and plant",
        payload=_sku_only_plan(),
        bind_on_miss=False,
    )
    _assert_multi_grain_l2(env, {"sku", "plant"}, "shipping_cost_myr")
    assert env.get("plan_source") != "bind_plan"


# --- #254 ONTOLOGY-MULTIGRAIN-02: live mg_sku_plant leftover -----------------
# Studio Finance grant has no shipments. shipping_cost by SKU+plant used to
# compile then die as validate:ungranted:shipments. Named missing_join/plant
# or a granted plant path (stock_value via inventory+locations). Not COMPLETE.

FINANCE_GRANT = {"inventory", "locations", "transactions", "suppliers"}
OPS_GRANT = {"locations", "inventory", "shipments"}


def test_relation_tables_bare_and_subquery() -> None:
    assert relation_tables("shipments") == frozenset({"shipments"})
    assert relation_tables(
        "(SELECT sku, ANY_VALUE(category) AS category FROM inventory GROUP BY sku)"
    ) == frozenset({"inventory"})
    assert missing_join_for_ungranted("ungranted:shipments", ("sku", "plant")) is not None
    gap = missing_join_for_ungranted("ungranted:shipments", ("sku", "plant")) or ""
    assert "missing_join" in gap and "plant" in gap
    assert not gap.startswith("validate:")
    assert missing_join_for_ungranted("explain:BinderException", ("sku", "plant")) is None


def _demo_onto(tmp_path: Path) -> tuple[Path, Ontology]:
    db = tmp_path / "mg254.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    return db, onto


def test_compile_finance_shipping_sku_plant_names_missing_join_plant(
    tmp_path: Path,
) -> None:
    _db, onto = _demo_onto(tmp_path)
    got = onto.compile_grains(
        "shipping_cost_myr", ["sku", "plant"], grantable=FINANCE_GRANT
    )
    assert isinstance(got, Refusal)
    assert got.reason == "missing_join"
    assert "plant" in got.detail.lower()
    assert "ungranted" in got.detail.lower()
    assert "shipments" in got.detail.lower()
    assert "validate:" not in got.detail


def test_compile_ops_shipping_sku_plant_is_granted_path(tmp_path: Path) -> None:
    _db, onto = _demo_onto(tmp_path)
    got = onto.compile_grains(
        "shipping_cost_myr", ["sku", "plant"], grantable=OPS_GRANT
    )
    assert isinstance(got, CompiledQuery)
    assert {p.grain for p in got.where_paths} == {"sku", "plant"}
    sql = got.sql.lower()
    assert "shipments" in sql
    assert "sku" in sql


def test_compile_warehouse_alias_counts_as_granted_plant_path(tmp_path: Path) -> None:
    _db, onto = _demo_onto(tmp_path)
    got = onto.compile_grains(
        "shipping_cost_myr",
        ["sku", "plant"],
        grantable={
            "warehouse_shipments",
            "warehouse_inventory",
            "warehouse_locations",
        },
    )
    assert isinstance(got, CompiledQuery)
    assert {p.grain for p in got.where_paths} >= {"sku", "plant"}


def test_compile_finance_stock_sku_plant_is_granted_path(tmp_path: Path) -> None:
    """Finance can group stock_value by SKU+plant through inventory+locations."""
    _db, onto = _demo_onto(tmp_path)
    got = onto.compile_grains(
        "stock_value_myr", ["sku", "plant"], grantable=FINANCE_GRANT
    )
    assert isinstance(got, CompiledQuery)
    assert {p.grain for p in got.where_paths} == {"sku", "plant"}
    sql = got.sql.lower()
    assert "inventory" in sql
    assert "shipments" not in sql


def _demo_ask(
    tmp_path: Path,
    question: str,
    *,
    grantable: set[str],
    payload: dict[str, Any] | None = None,
    must_submit: bool = True,
) -> dict[str, Any]:
    warehouse, onto = _demo_onto(tmp_path)
    submits: list[str] = []

    def submit(sql: str) -> QueryResult:
        submits.append(sql)
        import duckdb

        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            cols = [d[0] for d in con.execute(sql).description]
            rows = [dict(zip(cols, r, strict=True)) for r in con.execute(sql).fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_mg254", output={"rows": rows})

    def boom(_s: str) -> QueryResult:
        raise AssertionError("must not submit")

    env = maybe_generative_ask(
        question,
        warehouse=warehouse,
        grantable=grantable,
        compute=lambda _c: payload or _sku_only_plan("shipping_cost_myr"),
        submit=submit if must_submit else boom,
        ledger_append=(
            (lambda _b: LedgerAppendResponse(entry_id="led_mg254", hash="hash_mg254_not_entry"))
            if must_submit
            else (lambda _b: (_ for _ in ()).throw(AssertionError("must not append")))
        ),
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    env["_submits"] = submits
    return env


def test_mg_sku_plant_finance_abstains_naming_missing_join_plant(
    tmp_path: Path,
) -> None:
    """Live leftover: ABSTAIN validate:ungranted:shipments. Must name the grain."""
    env = _demo_ask(
        tmp_path,
        "shipping cost by SKU and plant",
        grantable=FINANCE_GRANT,
        must_submit=False,
    )
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env.get("plan_source") == "ontology_plan"
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    text = str(env.get("text") or "")
    combined = f"{blob} {text}".lower()
    assert "missing_join" in combined
    assert "plant" in combined
    assert "validate:ungranted:shipments" not in combined
    assert "gap:" in text.lower()
    assert "bind_plan" not in blob
    assert_envelope_valid(env)


def test_mg_sku_plant_ops_grants_plant_path(tmp_path: Path) -> None:
    env = _demo_ask(
        tmp_path,
        "shipping cost by SKU and plant",
        grantable=OPS_GRANT,
    )
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env.get("plan_source") == "ontology_plan"
    assert _grains_on_envelope(env) == {"sku", "plant"}
    notes = env.get("assumptions") or []
    assert not any("bind_plan" in str(a) for a in notes)
    assert any("ontology_compile:where+importance" in str(a) for a in notes)
    sql = str(env.get("sql_used") or "").lower()
    assert "sku" in sql
    assert env["rows"]
    assert "Found" in str(env.get("text") or "")
    assert env.get("_submits")
    assert_envelope_valid(env)


def test_mg_sku_plant_finance_stock_value_grants_plant_path(tmp_path: Path) -> None:
    env = _demo_ask(
        tmp_path,
        "stock value by SKU and plant",
        grantable=FINANCE_GRANT,
        payload=_sku_only_plan("stock_value_myr"),
    )
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert _grains_on_envelope(env) == {"sku", "plant"}
    sql = str(env.get("sql_used") or "").lower()
    assert "inventory" in sql
    assert "shipments" not in sql
    assert env["rows"]
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert_envelope_valid(env)
