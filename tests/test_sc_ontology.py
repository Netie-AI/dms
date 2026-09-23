"""SC-ONTOLOGY-01: named supply-chain grains + honest abstain.

SKU / supplier / plant / lane / day. Compile ontology_plan or ABSTAIN
naming the missing metric/join. Numeric answers carry include/exclude/unsure.
Does not invent COMPLETE / 1PB LIVE / fake lane metrics. Not pack shrink.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import (
    NO_SILENT_PAD,
    SUPPLY_CHAIN_GRAINS,
    CompiledQuery,
    Coverage,
    Refusal,
    coverage_valid,
    demo_ontology,
)


def _thin(tmp_path: Path) -> tuple[Path, Any]:
    db = tmp_path / "sc_onto.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    assert onto.verified
    return db, onto


def _submit_ok(sql: str) -> Any:
    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_sc_onto",
        output={"rows": [{"product_sku": "SKU-ALPHA", "outbound_value_myr": 4830.0}]},
    )


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_sc_onto", hash="hash_sc_onto_not_entry")


def test_named_grains_sku_supplier_plant_day_present_lane_missing(
    tmp_path: Path,
) -> None:
    _db, onto = _thin(tmp_path)
    catalog = onto.supply_chain_catalog()
    assert tuple(catalog) == SUPPLY_CHAIN_GRAINS
    assert catalog["sku"]["present"] is True
    assert catalog["sku"]["object"] == "product"
    assert catalog["supplier"]["present"] is True
    assert catalog["plant"]["present"] is True
    assert catalog["plant"]["object"] == "location"
    assert catalog["day"]["present"] is True
    assert catalog["lane"]["present"] is False
    assert "origin_location_id" in (catalog["lane"]["missing"] or "")
    assert "No silent pad" in (catalog["lane"]["missing"] or "")


def test_sku_alias_compiles_same_as_product(tmp_path: Path) -> None:
    db, onto = _thin(tmp_path)
    via_sku = onto.compile(
        "outbound_value_myr", group_by=[("sku", "sku")], limit=8
    )
    via_product = onto.compile(
        "outbound_value_myr", group_by=[("product", "sku")], limit=8
    )
    assert isinstance(via_sku, CompiledQuery)
    assert isinstance(via_product, CompiledQuery)
    assert coverage_valid(via_sku.coverage)
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        a = dict(con.execute(via_sku.sql).fetchall())
        b = dict(con.execute(via_product.sql).fetchall())
    finally:
        con.close()
    assert a == b
    assert a
    assert sum(a.values()) > 0


def test_plant_alias_groups_like_location(tmp_path: Path) -> None:
    db, onto = _thin(tmp_path)
    via_plant = onto.compile(
        "outbound_value_myr", group_by=[("plant", "location_code")]
    )
    via_loc = onto.compile(
        "outbound_value_myr", group_by=[("location", "location_code")]
    )
    assert isinstance(via_plant, CompiledQuery)
    assert isinstance(via_loc, CompiledQuery)
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        assert dict(con.execute(via_plant.sql).fetchall()) == dict(
            con.execute(via_loc.sql).fetchall()
        )
    finally:
        con.close()


def test_day_grain_compiles_and_conserves(tmp_path: Path) -> None:
    db, onto = _thin(tmp_path)
    by_day = onto.compile("outbound_value_myr", group_by=[("day", "day")])
    total = onto.compile("outbound_value_myr")
    assert isinstance(by_day, CompiledQuery)
    assert isinstance(total, CompiledQuery)
    assert coverage_valid(by_day.coverage)
    assert NO_SILENT_PAD in by_day.coverage.exclude
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        grouped = sum(v for _, v in con.execute(by_day.sql).fetchall())
        one = con.execute(total.sql).fetchone()[0]
    finally:
        con.close()
    assert grouped == one
    assert "GENERATE_SERIES" not in by_day.sql.upper()


def test_lane_group_abstains_naming_missing_join(tmp_path: Path) -> None:
    _db, onto = _thin(tmp_path)
    got = onto.compile(
        "shipping_cost_myr", group_by=[("lane", "origin_plant_id")]
    )
    assert isinstance(got, Refusal)
    assert got.reason == "missing_join"
    assert "lane" in got.detail
    assert "origin_location_id" in got.detail


def test_unknown_metric_is_named_not_guessed(tmp_path: Path) -> None:
    _db, onto = _thin(tmp_path)
    got = onto.compile("lane_throughput_pb")
    assert isinstance(got, Refusal)
    assert got.reason == "unknown_measure"
    assert "lane_throughput_pb" in got.detail


def test_join_importance_ranks_sku_plant_day(tmp_path: Path) -> None:
    _db, onto = _thin(tmp_path)
    ranked = onto.join_importance("outbound_value_myr")
    assert ranked["missing_metric"] is False
    grains = ranked["grains"]
    assert grains["sku"]["importance"] == 1
    assert "txn_of_product" in grains["sku"]["path"]
    assert grains["plant"]["importance"] == 1
    assert "txn_at_location" in grains["plant"]["path"]
    assert grains["day"]["importance"] == 1
    assert "txn_on_day" in grains["day"]["path"]
    assert grains["lane"]["importance"] is None
    assert grains["lane"]["missing"]
    # Thin seed: one lot per sku, so txn_of_lot is many-to-one and supplier
    # is a 2-hop grouping path. A lake with many lots per sku is filter-only.
    assert grains["supplier"]["importance"] in (2, 3)
    if grains["supplier"]["importance"] == 3:
        assert grains["supplier"].get("filter_only") is True
    else:
        assert grains["supplier"]["path"]


def test_numeric_compile_always_has_include_exclude_unsure(tmp_path: Path) -> None:
    _db, onto = _thin(tmp_path)
    got = onto.compile("sku_count")
    assert isinstance(got, CompiledQuery)
    cov = got.coverage
    assert isinstance(cov, Coverage)
    assert coverage_valid(cov)
    lines = cov.assumption_lines()
    assert lines[0].startswith("include:")
    assert lines[1].startswith("exclude:")
    assert lines[2].startswith("unsure:")
    assert NO_SILENT_PAD in cov.exclude


def test_ask_sku_grain_is_ontology_plan_with_coverage(tmp_path: Path) -> None:
    db, onto = _thin(tmp_path)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_source": "ontology_plan",
            "query_plan": {
                "measure": "outbound_value_myr",
                "group_by": [["sku", "sku"]],
                "limit": 8,
            },
        }

    env = maybe_generative_ask(
        "What is outbound value by SKU?",
        warehouse=db,
        grantable={"transactions", "inventory", "locations", "suppliers", "shipments"},
        compute=compute,
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["plan_source"] == "ontology_plan"
    cov = env.get("coverage")
    assert isinstance(cov, dict)
    assert cov.get("include")
    assert NO_SILENT_PAD in (cov.get("exclude") or [])
    assert "unsure" in cov
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "include:" in blob
    assert "exclude:" in blob
    assert "unsure:" in blob
    assert_envelope_valid(env)


def test_ask_lane_grain_abstains_naming_missing_join(tmp_path: Path) -> None:
    db, onto = _thin(tmp_path)
    submits: list[str] = []

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_source": "ontology_plan",
            "query_plan": {
                "measure": "shipping_cost_myr",
                "group_by": [["lane", "origin_plant_id"]],
            },
        }

    env = maybe_generative_ask(
        "What is shipment cost by lane?",
        warehouse=db,
        grantable={"transactions", "inventory", "locations", "suppliers", "shipments"},
        compute=compute,
        submit=lambda sql: submits.append(sql) or _submit_ok(sql),
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert submits == []
    text = (env.get("text") or "").lower()
    assumptions = " ".join(str(a) for a in (env.get("assumptions") or [])).lower()
    assert "missing_join" in text or "missing_join" in assumptions
    assert "lane" in text or "lane" in assumptions
    assert "origin_location_id" in assumptions or "origin_location_id" in text
    assert_envelope_valid(env)


def test_spine_names_grains_without_sql() -> None:
    import yaml

    root = Path(__file__).resolve().parents[1]
    path = root / "packages" / "executor" / "dms_executor" / "ontology_spine.yaml"
    blob = path.read_text(encoding="utf-8")
    assert "SELECT" not in blob.upper()
    data = yaml.safe_load(blob)
    grains = data.get("grains") or {}
    assert tuple(grains) == SUPPLY_CHAIN_GRAINS
    assert grains["sku"] == "product"
    assert grains["plant"] == "location"


def test_lane_present_when_origin_column_exists(tmp_path: Path) -> None:
    """Origin+dest is a real lane. Thin seed has dest only; this lake has both."""
    import duckdb

    db = tmp_path / "lane.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "CREATE TABLE locations (location_id VARCHAR PRIMARY KEY, "
            "location_code VARCHAR, capacity_kg DOUBLE, current_load_kg DOUBLE)"
        )
        con.execute("INSERT INTO locations VALUES ('WH-A','WH-A',1,0),('WH-B','WH-B',1,0)")
        con.execute(
            "CREATE TABLE shipments (shipment_id VARCHAR PRIMARY KEY, sku VARCHAR, "
            "origin_location_id VARCHAR, destination_location_id VARCHAR, cost_myr DOUBLE)"
        )
        con.execute(
            "INSERT INTO shipments VALUES "
            "('SH1','SKU-A','WH-A','WH-B',10),('SH2','SKU-A','WH-B','WH-A',20)"
        )
        for table in (
            "transactions",
            "inventory",
            "suppliers",
            "alerts",
        ):
            if table == "transactions":
                con.execute(
                    "CREATE TABLE transactions (txn_id VARCHAR PRIMARY KEY, sku VARCHAR, "
                    "location_id VARCHAR, txn_type VARCHAR, quantity_kg DOUBLE, "
                    "unit_cost_myr DOUBLE, ts TIMESTAMP)"
                )
                con.execute(
                    "INSERT INTO transactions VALUES "
                    "('T1','SKU-A','WH-A','outbound',1,1,'2026-07-01 00:00:00')"
                )
            elif table == "inventory":
                con.execute(
                    "CREATE TABLE inventory (sku VARCHAR, location_id VARCHAR, "
                    "quantity_kg DOUBLE, unit_cost_myr DOUBLE, category VARCHAR)"
                )
                con.execute("INSERT INTO inventory VALUES ('SKU-A','WH-A',1,1,'RAW')")
            elif table == "suppliers":
                con.execute(
                    "CREATE TABLE suppliers (supplier_id VARCHAR PRIMARY KEY, "
                    "supplier_name VARCHAR)"
                )
                con.execute("INSERT INTO suppliers VALUES ('SUP-01','North')")
            else:
                con.execute(
                    "CREATE TABLE alerts (alert_id VARCHAR PRIMARY KEY, location_id VARCHAR)"
                )
                con.execute("INSERT INTO alerts VALUES ('AL-1','WH-A')")
    finally:
        con.close()
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    catalog = onto.supply_chain_catalog()
    assert catalog["lane"]["present"] is True
    got = onto.compile(
        "shipping_cost_myr", group_by=[("lane", "origin_plant_id")]
    )
    assert isinstance(got, CompiledQuery)
    assert coverage_valid(got.coverage)
    import duckdb as d2

    c2 = d2.connect(str(db), read_only=True)
    try:
        rows = dict(c2.execute(got.sql).fetchall())
    finally:
        c2.close()
    assert rows == {"WH-A": 10.0, "WH-B": 20.0}
