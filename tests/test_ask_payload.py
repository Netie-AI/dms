"""WIRE-01b: the dms_payload builder. Synthetic demo warehouse only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_contract.answer import AskPayload
from dms_executor.ask_payload import MAX_TABLES, build_dms_payload, physical_table
from dms_executor.demo_warehouse import DEMO_TABLES, connect_file, ensure_demo_warehouse
from dms_executor.ontology import LinkType, ObjectType, Ontology, demo_ontology


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("payload") / "lake.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR, "
            "email VARCHAR, segment VARCHAR, credit_limit_myr DOUBLE)"
        )
        con.execute("CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, total_myr DOUBLE)")
    finally:
        con.close()
    return db


@pytest.fixture(scope="module")
def verified(lake: Path) -> Ontology:
    onto = demo_ontology(lake)
    con = connect_file(lake)
    try:
        assert onto.verify(con) == []
    finally:
        con.close()
    assert onto.verified
    return onto


def _names(payload: dict[str, Any]) -> set[str]:
    return {t["name"] for t in payload["tables"]}


def _cols(payload: dict[str, Any], table: str) -> list[str]:
    for t in payload["tables"]:
        if t["name"] == table:
            return [c["name"] for c in t["columns"]]
    raise AssertionError(table)


def test_tables_are_exactly_the_readable_tables_the_lake_holds(lake: Path) -> None:
    readable = {"inventory", "locations", "orders", "not_in_lake"}
    payload = build_dms_payload("stock by site", serving=lake, readable=readable)
    assert payload is not None
    assert _names(payload) == {"inventory", "locations", "orders"}
    AskPayload.model_validate(payload)


def test_a_table_outside_the_grant_never_appears(lake: Path) -> None:
    payload = build_dms_payload("alerts and stock", serving=lake, readable={"inventory"})
    assert payload is not None
    assert _names(payload) == {"inventory"}
    # A warehouse_ alias is not the name Cortex compares, so it adds nothing.
    aliased = build_dms_payload("stock", serving=lake, readable={"warehouse_inventory"})
    assert aliased is None


def test_no_readable_table_is_no_payload(lake: Path, tmp_path: Path) -> None:
    assert build_dms_payload("q", serving=lake, readable=set()) is None
    assert build_dms_payload("q", serving=lake, readable={"nope"}) is None
    assert (
        build_dms_payload("q", serving=tmp_path / "missing.duckdb", readable={"inventory"}) is None
    )
    assert build_dms_payload("q", serving=None, readable={"inventory"}) is None


def test_person_columns_never_leave_dms(lake: Path) -> None:
    payload = build_dms_payload("customers by segment", serving=lake, readable={"customers"})
    assert payload is not None
    cols = _cols(payload, "customers")
    assert "full_name" not in cols
    assert "email" not in cols
    assert {"customer_id", "segment", "credit_limit_myr"} <= set(cols)


def test_text_columns_come_first_then_catalog_order(lake: Path) -> None:
    payload = build_dms_payload("customers", serving=lake, readable={"customers"})
    assert payload is not None
    assert _cols(payload, "customers") == ["segment", "customer_id", "credit_limit_myr"]


def test_verified_many_to_one_links_between_physical_tables_become_joins(
    lake: Path, verified: Ontology
) -> None:
    readable = {"inventory", "locations", "suppliers", "transactions"}
    payload = build_dms_payload(
        "stock value by site", serving=lake, readable=readable, ontology=verified
    )
    assert payload is not None
    AskPayload.model_validate(payload)
    joins = {
        (j["left_table"], j["left_column"], j["right_table"], j["right_column"])
        for j in payload["joins"]
    }
    assert ("inventory", "location_id", "locations", "location_id") in joins
    assert ("inventory", "supplier_id", "suppliers", "supplier_id") in joins
    # transactions is SELECT *, CAST(ts AS DATE) AS day FROM transactions: row for row.
    assert ("transactions", "location_id", "locations", "location_id") in joins
    for join in payload["joins"]:
        assert join["left_table"] in readable and join["right_table"] in readable
        assert "many_to_one" in join["relation"]


def test_a_grouped_relation_is_never_joined_on_its_base_table(
    lake: Path, verified: Ontology
) -> None:
    # product is SELECT sku, ANY_VALUE(category) ... GROUP BY sku over inventory.
    # txn_of_product joined on inventory.sku would repeat each sale once per lot.
    payload = build_dms_payload(
        "sales by category", serving=lake, readable={"inventory", "transactions"}, ontology=verified
    )
    assert payload is not None
    to_product = {n for n, link in verified.links.items() if link.to_object == "product"}
    assert to_product
    named = {j["relation"].split()[0] for j in payload.get("joins", [])}
    assert not named & to_product
    # txn_of_lot joins transactions to inventory itself, which verify()
    # measured many-to-one on this data; that link is offered.
    assert "txn_of_lot" in named
    assert physical_table(verified.objects["product"].relation) is None
    assert physical_table(verified.objects["day"].relation) is None
    assert physical_table(verified.objects["transaction"].relation) == "transactions"
    assert physical_table("inventory") == "inventory"


@pytest.mark.parametrize(
    "relation",
    [
        "(SELECT * FROM inventory WHERE quantity_kg > 0)",
        "(SELECT DISTINCT sku FROM inventory)",
        "(SELECT * FROM inventory LIMIT 5)",
        "(SELECT i.* FROM inventory i JOIN locations l USING (location_id))",
        "(SELECT COUNT(*) AS n FROM inventory)",
        "(SELECT * FROM inventory UNION ALL SELECT * FROM inventory)",
        "(WITH x AS (SELECT * FROM inventory) SELECT * FROM x)",
        "(SELECT * FROM other.inventory)",
        "not sql at all (",
    ],
)
def test_only_a_row_for_row_relation_is_its_table(relation: str) -> None:
    assert physical_table(relation) is None


def test_unverified_or_many_to_many_links_give_no_join(lake: Path) -> None:
    onto = Ontology()
    onto.objects["order"] = ObjectType("order", "orders", ("order_id",))
    onto.objects["customer"] = ObjectType("customer", "customers", ("customer_id",))
    onto.links["order_of_customer"] = LinkType(
        "order_of_customer", "order", ("customer_id",), "customer", ("customer_id",), "unverified"
    )
    readable = {"orders", "customers"}
    onto.verified = True
    assert "joins" not in (
        build_dms_payload("q", serving=lake, readable=readable, ontology=onto) or {}
    )
    onto.links["order_of_customer"] = LinkType(
        "order_of_customer", "order", ("customer_id",), "customer", ("customer_id",), "many_to_many"
    )
    assert "joins" not in (
        build_dms_payload("q", serving=lake, readable=readable, ontology=onto) or {}
    )
    onto.links["order_of_customer"] = LinkType(
        "order_of_customer", "order", ("customer_id",), "customer", ("customer_id",), "many_to_one"
    )
    joined = build_dms_payload("q", serving=lake, readable=readable, ontology=onto)
    assert joined is not None and len(joined["joins"]) == 1
    # Not verified against this data: tables only.
    onto.verified = False
    plain = build_dms_payload("q", serving=lake, readable=readable, ontology=onto)
    assert plain is not None
    assert "joins" not in plain
    assert _names(plain) == readable


def test_a_composite_key_gives_one_join_row_per_key_pair(lake: Path) -> None:
    con = duckdb.connect(str(lake))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS lot_counts (sku VARCHAR, location_id VARCHAR, n INTEGER)"
        )
    finally:
        con.close()
    onto = Ontology(verified=True)
    onto.objects["count"] = ObjectType("count", "lot_counts", ("sku", "location_id"))
    onto.objects["lot"] = ObjectType("lot", "inventory", ("sku", "location_id", "supplier_id"))
    onto.links["count_of_lot"] = LinkType(
        "count_of_lot",
        "count",
        ("sku", "location_id"),
        "lot",
        ("sku", "location_id"),
        "many_to_one",
    )
    payload = build_dms_payload(
        "q", serving=lake, readable={"lot_counts", "inventory"}, ontology=onto
    )
    assert payload is not None
    AskPayload.model_validate(payload)
    assert [(j["left_column"], j["right_column"]) for j in payload["joins"]] == [
        ("sku", "sku"),
        ("location_id", "location_id"),
    ]


def test_a_join_on_a_person_column_is_not_offered(lake: Path) -> None:
    onto = Ontology(verified=True)
    onto.objects["order"] = ObjectType("order", "orders", ("order_id",))
    onto.objects["customer"] = ObjectType("customer", "customers", ("customer_id",))
    onto.links["by_email"] = LinkType(
        "by_email", "order", ("customer_id",), "customer", ("email",), "many_to_one"
    )
    payload = build_dms_payload("q", serving=lake, readable={"orders", "customers"}, ontology=onto)
    assert payload is not None
    assert "joins" not in payload


def test_measures_describe_their_table(lake: Path, verified: Ontology) -> None:
    payload = build_dms_payload(
        "stock value", serving=lake, readable={"inventory"}, ontology=verified
    )
    assert payload is not None
    (table,) = payload["tables"]
    assert "stock_value_myr" in table["description"]
    assert len(table["description"]) <= 400


def test_more_tables_than_the_cap_keep_the_closest_to_the_question(
    tmp_path: Path,
) -> None:
    db = tmp_path / "wide.duckdb"
    con = duckdb.connect(str(db))
    try:
        for i in range(MAX_TABLES + 6):
            con.execute(f"CREATE TABLE filler_{i:02d} (a INTEGER)")
        con.execute("CREATE TABLE freight_invoices (invoice_id INTEGER, freight_myr DOUBLE)")
    finally:
        con.close()
    readable = {f"filler_{i:02d}" for i in range(MAX_TABLES + 6)} | {"freight_invoices"}
    payload = build_dms_payload("freight invoices this month", serving=db, readable=readable)
    assert payload is not None
    assert len(payload["tables"]) == MAX_TABLES
    assert "freight_invoices" in _names(payload)


def test_a_builder_error_is_no_payload_not_a_failed_ask(
    lake: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import dms_executor.ask_payload as mod

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("catalog down")

    monkeypatch.setattr(mod, "read_catalog", boom)
    assert build_dms_payload("q", serving=lake, readable={"inventory"}) is None
    assert "dms_payload_unbuilt RuntimeError" in caplog.text


def test_demo_lake_payload_validates_against_the_contract(lake: Path, verified: Ontology) -> None:
    payload = build_dms_payload(
        "anything", serving=lake, readable=set(DEMO_TABLES), ontology=verified
    )
    assert payload is not None
    model = AskPayload.model_validate(payload)
    assert {t.name for t in model.tables} == set(DEMO_TABLES)
