"""WIRE-01b: the dms_payload builder. Synthetic demo warehouse only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from dms_core.pii import classify_column
from dms_executor.ask_payload import (
    MAX_PAYLOAD_BYTES,
    MAX_TABLES,
    MAX_TYPE_CHARS,
    build_dms_payload,
    physical_table,
)
from dms_executor.demo_warehouse import DEMO_TABLES, connect_file, ensure_demo_warehouse
from dms_executor.ontology import LinkType, ObjectType, Ontology, demo_ontology
from dms_executor.schema_context import INDEX_COLUMN_CAP

#: The demo lake's join keys are text. Text goes in only with a steward's
#: non_personal tag, so join tests tag their keys.
_DEMO_KEYS = (
    ("inventory", "sku"),
    ("inventory", "location_id"),
    ("inventory", "supplier_id"),
    ("locations", "location_id"),
    ("suppliers", "supplier_id"),
    ("transactions", "sku"),
    ("transactions", "location_id"),
)


def _tag(onto: Ontology, *columns: tuple[str, str]) -> Ontology:
    """Mark ``(table, column)`` pairs non_personal with source and field."""
    onto.non_personal = [  # type: ignore[attr-defined]
        {"table": t, "column": c, "source": "steward", "field": c} for t, c in columns
    ]
    return onto


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("payload") / "lake.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR, "
            "email VARCHAR, segment VARCHAR, region VARCHAR, credit_limit_myr DOUBLE)"
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
    return _tag(onto, *_DEMO_KEYS)


def _accepts(payload: dict[str, Any]) -> Any:
    """The payload is an AskPayload the vendored client can send.

    ``cortex_contract.answer.AskPayload`` exists from 1.5.0. The pin floor is
    1.2.0, where that import fails at collection, so it is not imported here.
    When this install has it, that check runs too.
    """
    from cortex_client.generated.models.ask_payload import AskPayload as ClientAsk

    model = ClientAsk.from_dict(payload)
    assert model.to_dict() == payload
    try:
        from cortex_contract.answer import AskPayload as ContractAsk
    except ImportError:
        return model
    ContractAsk.model_validate(payload)
    return model


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
    _accepts(payload)


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
    # segment and region are text without a non_personal tag: Cortex would
    # sample their values, so they are left out.
    assert "segment" not in cols
    assert "region" not in cols
    assert cols == ["customer_id", "credit_limit_myr"]


def test_text_columns_come_first_then_catalog_order(lake: Path) -> None:
    onto = _tag(Ontology(), ("customers", "region"))
    payload = build_dms_payload("customers", serving=lake, readable={"customers"}, ontology=onto)
    assert payload is not None
    assert _cols(payload, "customers") == ["region", "customer_id", "credit_limit_myr"]


def test_verified_many_to_one_links_between_physical_tables_become_joins(
    lake: Path, verified: Ontology
) -> None:
    readable = {"inventory", "locations", "suppliers", "transactions"}
    payload = build_dms_payload(
        "stock value by site", serving=lake, readable=readable, ontology=verified
    )
    assert payload is not None
    _accepts(payload)
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
    _tag(
        onto,
        ("lot_counts", "sku"),
        ("lot_counts", "location_id"),
        ("inventory", "sku"),
        ("inventory", "location_id"),
    )
    payload = build_dms_payload(
        "q", serving=lake, readable={"lot_counts", "inventory"}, ontology=onto
    )
    assert payload is not None
    _accepts(payload)
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
            con.execute(f"CREATE TABLE filler_{i:02d} (a_count INTEGER)")
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
    model = _accepts(payload)
    assert {t.name for t in model.tables} == set(DEMO_TABLES)


# --- #452 review: a column is sent only when it is positively cleared ---------

_NEUTRAL_PERSON_COLUMNS = (
    "handled_by",
    "pic",
    "salesperson",
    "consignee",
    "approved_by",
    "contact_person",
    "assignee",
    "recipient",
)


def _lake_with(tmp_path: Path, *statements: str, name: str = "lake") -> Path:
    db = tmp_path / f"{name}.duckdb"
    con = duckdb.connect(str(db))
    try:
        for sql in statements:
            con.execute(sql)
    finally:
        con.close()
    return db


def _all_cols(payload: dict[str, Any]) -> set[str]:
    return {c["name"] for t in payload["tables"] for c in t["columns"]}


def _cortex_sample(db: Path, table: str, column: str) -> list[str]:
    """Every value Cortex's sample SQL can draw from one payload column.

    Cortex runs ``SELECT DISTINCT "col" AS v FROM "t" WHERE "col" IS NOT NULL
    LIMIT 8`` and sends ``str(v)``; which 8 it gets is not fixed, so all of them.
    """
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            f'SELECT DISTINCT "{column}" AS v FROM "{table}" WHERE "{column}" IS NOT NULL'
        ).fetchall()
    finally:
        con.close()
    return [str(v) for (v,) in rows]


def _sent_type_is_integer(type_name: object) -> bool:
    """Independent of the builder, so a gutted normalizer stays red.

    ``integer`` does not end in ``int``. ``uinteger`` does not start with ``uint``.
    """
    token = str(type_name or "").lower().split("(", 1)[0].strip().split(" ")[0]
    if "." in token:
        token = token.rsplit(".", 1)[-1]
    if token in {"integer", "uinteger"}:
        return True
    return (
        token.endswith("int")
        or token.endswith("serial")
        or token.startswith("uint")
        or (token.startswith("int") and token[3:].isdigit())
    )


def _assert_cortex_samples_nothing_personal(db: Path, payload: dict[str, Any]) -> None:
    for table in payload["tables"]:
        for column in table["columns"]:
            integer = _sent_type_is_integer(column.get("type"))
            for value in _cortex_sample(db, table["name"], column["name"]):
                forms = [value]
                # An integer sample lost any leading zero. The padded spellings
                # are what a MyKad or a local phone looks like.
                if integer and value.isdigit():
                    forms.append("0" + value)
                    if len(value) < 12:
                        forms.append(value.zfill(12))
                for form in forms:
                    found = classify_column(column["name"], [form], table=table["name"])
                    assert found is None, (table["name"], column["name"], value, form, found)


def test_person_columns_under_neutral_names_never_leave_dms(tmp_path: Path) -> None:
    people = ", ".join(f"{c} VARCHAR" for c in _NEUTRAL_PERSON_COLUMNS)
    names = ", ".join(
        f"'{n}'"
        for n in (
            "Ahmad bin Ismail",
            "Tan Wei Ming",
            "Siti Nurhaliza",
            "Lim Kok Wing",
            "Nur Aisyah",
            "Raj Kumar",
            "Wong Mei Ling",
            "Farid Hassan",
        )
    )
    db = _lake_with(
        tmp_path,
        f"CREATE TABLE orders (order_id INTEGER, total_myr DOUBLE, {people}, status VARCHAR)",
        f"INSERT INTO orders VALUES (1, 10.0, {names}, 'open')",
    )
    payload = build_dms_payload("orders by status", serving=db, readable={"orders"})
    assert payload is not None
    _accepts(payload)
    # status is untagged text too, so it stays home with the people.
    assert _cols(payload, "orders") == ["order_id", "total_myr"]
    assert not _all_cols(payload) & set(_NEUTRAL_PERSON_COLUMNS)


@pytest.mark.parametrize(
    ("column", "kind", "value"),
    [
        ("contact_code", "VARCHAR", "'ahmad.ismail@example.com'"),
        ("ref_id", "VARCHAR", "'900101-14-5678'"),
        ("owner_code", "VARCHAR", "'+60 12-345 6789'"),
        ("approver_id", "VARCHAR", "'Ahmad'"),
        ("holder_id", "BIGINT", "900101145678"),
        ("caller_id", "BIGINT", "60123456789"),
    ],
)
def test_a_cleared_name_holding_personal_values_is_left_out(
    tmp_path: Path, column: str, kind: str, value: str
) -> None:
    db = _lake_with(
        tmp_path,
        f"CREATE TABLE claims (claim_id INTEGER, amount_myr DOUBLE, {column} {kind})",
        f"INSERT INTO claims VALUES (1, 5.0, {value})",
    )
    payload = build_dms_payload("claims", serving=db, readable={"claims"})
    assert payload is not None
    assert _cols(payload, "claims") == ["claim_id", "amount_myr"]


def test_only_positively_cleared_columns_are_sent(tmp_path: Path) -> None:
    db = _lake_with(
        tmp_path,
        "CREATE TABLE members (member_ref INTEGER, segment VARCHAR, notes VARCHAR, "
        "joined DATE, created_at TIMESTAMP, is_active BOOLEAN, region VARCHAR, "
        "visits_count INTEGER, weight DOUBLE)",
        "INSERT INTO members VALUES (1, 'gold', 'call back', DATE '1990-01-01', "
        "TIMESTAMP '2026-01-01 10:00:00', true, 'north', 3, 70.5)",
    )
    payload = build_dms_payload("members", serving=db, readable={"members"})
    assert payload is not None
    # Booleans by their values; visits_count and created_at by names
    # dms_core.pii treats as non-personal. region is a cleared name but text
    # without a tag. The rest is unsure and stays home.
    assert _cols(payload, "members") == ["is_active", "created_at", "visits_count"]


def test_values_that_cannot_be_read_clear_only_booleans(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_executor.ask_payload as mod

    # Unread (or over the cap) is absent from the read.
    monkeypatch.setattr(mod, "_read_values", lambda *_a, **_k: {})
    payload = build_dms_payload("sites", serving=lake, readable={"locations", "inventory"})
    assert payload is not None
    assert _names(payload) == {"locations"}
    assert _cols(payload, "locations") == ["is_cold_storage"]


def test_a_non_personal_tag_clears_a_neutral_text_column(tmp_path: Path) -> None:
    db = _lake_with(
        tmp_path,
        "CREATE TABLE stores (store_id INTEGER, banner VARCHAR)",
        "INSERT INTO stores VALUES (1, 'hypermarket')",
    )
    onto = Ontology()
    plain = build_dms_payload("stores", serving=db, readable={"stores"}, ontology=onto)
    assert plain is not None and _cols(plain, "stores") == ["store_id"]
    onto.non_personal = [  # type: ignore[attr-defined]
        {"table": "stores", "column": "banner", "source": "steward", "field": "banner"}
    ]
    tagged = build_dms_payload("stores", serving=db, readable={"stores"}, ontology=onto)
    assert tagged is not None and _cols(tagged, "stores") == ["banner", "store_id"]


# --- #452 review r2: every value Cortex can sample is classified -----------------


@pytest.mark.parametrize(
    ("column", "kind", "filler", "rare"),
    [
        (
            "contact_code",
            "VARCHAR",
            "'WALKIN'",
            ("'ahmad.ismail@example.com'", "'900101-14-5678'", "'+60 12-345 6789'"),
        ),
        ("buyer_ref_id", "BIGINT", "0", ("900101145678", "60123456789")),
    ],
)
def test_a_rare_personal_value_past_the_first_rows_keeps_the_column_home(
    tmp_path: Path, column: str, kind: str, filler: str, rare: tuple[str, ...]
) -> None:
    # A cash-sale code column: 250 rows of one code, then a few rows where the
    # cashier typed the buyer's email, IC or phone. DISTINCT brings them forward.
    create = f"CREATE TABLE tickets (ticket_id INTEGER, {column} {kind})"
    fill = f"INSERT INTO tickets SELECT i, {filler} FROM range(1, 251) t(i)"
    # Tagged, so the text case tests the value read and not the text rule.
    onto = _tag(Ontology(), ("tickets", column))
    clean = _lake_with(tmp_path, create, fill, name="clean")
    sent = build_dms_payload(
        "tickets by contact", serving=clean, readable={"tickets"}, ontology=onto
    )
    assert sent is not None and column in _cols(sent, "tickets")

    db = _lake_with(
        tmp_path,
        create,
        fill,
        *(f"INSERT INTO tickets VALUES ({251 + n}, {v})" for n, v in enumerate(rare)),
    )
    assert len(_cortex_sample(db, "tickets", column)) == len(rare) + 1
    payload = build_dms_payload(
        "tickets by contact", serving=db, readable={"tickets"}, ontology=onto
    )
    assert payload is not None
    assert _cols(payload, "tickets") == ["ticket_id"]
    _assert_cortex_samples_nothing_personal(db, payload)


def test_a_column_with_more_distinct_values_than_the_cap_stays_home(tmp_path: Path) -> None:
    # Cortex may sample any distinct value; past the cap DMS has not classified
    # them all, so the column is left out whole, as the value index omits it.
    db = _lake_with(
        tmp_path,
        "CREATE TABLE receipts (receipt_id BIGINT, amount_myr DOUBLE, unit_kg DOUBLE)",
        f"INSERT INTO receipts SELECT i, i * 1.5, i % {INDEX_COLUMN_CAP} "
        f"FROM range(0, {INDEX_COLUMN_CAP + 1}) t(i)",
        # The one IC sits past every row the old window read.
        "INSERT INTO receipts VALUES (900101145678, 900101145678, 0)",
    )
    payload = build_dms_payload("receipts", serving=db, readable={"receipts"})
    assert payload is not None
    # unit_kg holds exactly the cap of distinct values, all classified.
    assert _cols(payload, "receipts") == ["unit_kg"]
    _assert_cortex_samples_nothing_personal(db, payload)


_NAMES_UNDER_CLEARED_NAMES = {
    "agent_code": ("TAN AH KOW", "AHMAD BIN ISMAIL", "MUTHU A/L RAJAN"),
    "salesman_id": ("AHMAD", "SITI", "WONG"),
    "driver_code": ("Muthu a/l Rajan", "Lee Chong Wei", "Lee Chong Wei"),
    "candidate": ("Ahmad bin Ismail", "Nur Aisyah binti Hassan", "Ahmad bin Ismail"),
}


def test_untagged_text_never_leaves_dms_even_under_a_cleared_name(tmp_path: Path) -> None:
    # Each name passes the mask's metric-skip suffix (code, id, date), and names
    # in capitals or several words pass every value check. Cortex would sample
    # them. The house sample rule sends text only with a non_personal tag.
    names = list(_NAMES_UNDER_CLEARED_NAMES)
    rows = ", ".join(
        "(" + ", ".join([str(i), *(f"'{_NAMES_UNDER_CLEARED_NAMES[c][i]}'" for c in names)]) + ")"
        for i in range(3)
    )
    db = _lake_with(
        tmp_path,
        "CREATE TABLE sales (sale_id INTEGER, "
        + ", ".join(f"{c} VARCHAR" for c in names)
        + ", amount_myr DOUBLE)",
        f"INSERT INTO sales (sale_id, {', '.join(names)}) VALUES {rows}",
    )
    payload = build_dms_payload("sales by agent", serving=db, readable={"sales"})
    assert payload is not None
    assert _cols(payload, "sales") == ["sale_id", "amount_myr"]
    _assert_cortex_samples_nothing_personal(db, payload)


# --- #452 review: payload size is bounded ---------------------------------------


def test_an_enum_type_never_carries_its_labels(tmp_path: Path) -> None:
    labels = ",".join(f"'Person Number {i}'" for i in range(2000))
    db = _lake_with(
        tmp_path,
        f"CREATE TYPE approver AS ENUM ({labels})",
        "CREATE TYPE pair AS ENUM ('Ahmad bin Ismail', 'Tan Wei Ming')",
        "CREATE TABLE claims (claim_id INTEGER, status approver, kind_type pair, "
        "amount_myr DOUBLE)",
    )
    # An ENUM is text: it goes in only tagged. The tag admits the column; the
    # labels still never ride on its type.
    onto = _tag(Ontology(), ("claims", "status"), ("claims", "kind_type"))
    payload = build_dms_payload("claims", serving=db, readable={"claims"}, ontology=onto)
    assert payload is not None
    _accepts(payload)
    types = {c["name"]: c["type"] for c in payload["tables"][0]["columns"]}
    assert types["status"] == "ENUM"
    assert types["kind_type"] == "ENUM"
    wire = json.dumps(payload)
    assert "Person Number" not in wire and "Ahmad" not in wire
    assert len(wire.encode()) <= MAX_PAYLOAD_BYTES


def test_a_long_type_is_cut_to_its_head_word(tmp_path: Path) -> None:
    fields = ", ".join(f"field_{i} VARCHAR" for i in range(500))
    db = _lake_with(
        tmp_path,
        f"CREATE TABLE wide (wide_id INTEGER, detail_code STRUCT({fields}), "
        "price DECIMAL(18, 3), amount_myr DECIMAL(18, 3))",
    )
    onto = _tag(Ontology(), ("wide", "detail_code"))
    payload = build_dms_payload("wide", serving=db, readable={"wide"}, ontology=onto)
    assert payload is not None
    types = {c["name"]: c["type"] for c in payload["tables"][0]["columns"]}
    assert types["detail_code"] == "STRUCT"
    assert types["amount_myr"] == "DECIMAL(18,3)"
    assert all(len(t or "") <= MAX_TYPE_CHARS for t in types.values())


def test_the_payload_never_passes_the_byte_cap(tmp_path: Path) -> None:
    statements = []
    for t in range(MAX_TABLES + 6):
        cols = ", ".join(f"measure_{t:02d}_{c:02d}_quantity_kg DOUBLE" for c in range(60))
        statements.append(f"CREATE TABLE ledger_{t:02d} (ledger_{t:02d}_id INTEGER, {cols})")
    statements.append("CREATE TABLE freight_invoices (invoice_id INTEGER, freight_myr DOUBLE)")
    db = _lake_with(tmp_path, *statements)
    readable = {f"ledger_{t:02d}" for t in range(MAX_TABLES + 6)} | {"freight_invoices"}
    payload = build_dms_payload("freight invoices this month", serving=db, readable=readable)
    assert payload is not None
    _accepts(payload)
    assert len(json.dumps(payload).encode()) <= MAX_PAYLOAD_BYTES
    # Over the cap the least similar tables go first.
    assert payload["tables"][0]["name"] == "freight_invoices"


def test_one_table_wider_than_the_cap_keeps_its_leading_columns(tmp_path: Path) -> None:
    cols = ", ".join(f"c{c:03d}_{'x' * 100}_amount DOUBLE" for c in range(60))
    db = _lake_with(tmp_path, f"CREATE TABLE huge (huge_id INTEGER, {cols})")
    payload = build_dms_payload("huge", serving=db, readable={"huge"})
    assert payload is not None
    _accepts(payload)
    assert len(json.dumps(payload).encode()) <= MAX_PAYLOAD_BYTES
    names = _cols(payload, "huge")
    assert names[0] == "huge_id" and 1 < len(names) < 61


# --- #452 review: row for row is an allowlist -----------------------------------


_ROW_CHANGING = [
    "(SELECT * FROM inventory PIVOT (SUM(quantity_kg) FOR location_id IN ('WH-A', 'WH-B')))",
    "(SELECT * FROM inventory UNPIVOT (q FOR loc IN (quantity_kg, reorder_level_kg)))",
    "(SELECT * FROM inventory TABLESAMPLE (10 PERCENT))",
    "(SELECT * FROM inventory USING SAMPLE 10%)",
    "(SELECT *, UNNEST([1, 2]) AS n FROM inventory)",
    "(SELECT *, generate_series(1, 3) AS g FROM inventory)",
    "(SELECT *, UPPER(category) AS cat FROM inventory)",
    "(SELECT * REPLACE (sku || 'x' AS sku) FROM inventory)",
    "(SELECT inventory.* FROM inventory)",
    "(SELECT * FROM inventory ORDER BY sku)",
    "(SELECT * FROM inventory WINDOW w AS (PARTITION BY sku))",
    "(SELECT * FROM inventory AS i(a, b))",
    "(SELECT 1 FROM inventory)",
]


@pytest.mark.parametrize("relation", _ROW_CHANGING)
def test_a_row_changing_form_is_not_its_table(relation: str) -> None:
    assert physical_table(relation) is None


@pytest.mark.parametrize(
    "relation",
    [
        "inventory",
        "(SELECT * FROM inventory)",
        "(FROM inventory)",
        "(SELECT sku, location_id FROM inventory)",
        "(SELECT * FROM inventory AS i)",
        "(SELECT *, CAST(expiry_date AS VARCHAR) AS expiry_text FROM inventory)",
    ],
)
def test_a_plain_select_over_one_table_is_its_table(relation: str) -> None:
    assert physical_table(relation) == "inventory"


@pytest.mark.parametrize("relation", _ROW_CHANGING[:6])
def test_a_row_changing_relation_is_never_joined_on_its_base_table(
    lake: Path, relation: str
) -> None:
    # The review repro: product as a PIVOT over inventory, many_to_one from
    # transactions. Joined on inventory.sku, SUM(quantity_kg) counts each lot.
    onto = Ontology(verified=True)
    onto.objects["transaction"] = ObjectType("transaction", "transactions", ("txn_id",))
    onto.objects["product"] = ObjectType("product", relation, ("sku",))
    onto.objects["location"] = ObjectType("location", "locations", ("location_id",))
    onto.links["txn_of_product"] = LinkType(
        "txn_of_product", "transaction", ("sku",), "product", ("sku",), "many_to_one"
    )
    onto.links["txn_at_location"] = LinkType(
        "txn_at_location",
        "transaction",
        ("location_id",),
        "location",
        ("location_id",),
        "many_to_one",
    )
    _tag(onto, *_DEMO_KEYS)
    readable = {"transactions", "inventory", "locations"}
    payload = build_dms_payload("sales by sku", serving=lake, readable=readable, ontology=onto)
    assert payload is not None
    named = {j["relation"].split()[0] for j in payload.get("joins", [])}
    assert named == {"txn_at_location"}


def test_a_renamed_key_is_never_joined_on_its_base_table(lake: Path) -> None:
    swapped = "(SELECT location_id AS sku, sku AS location_id FROM inventory)"
    assert physical_table(swapped) == "inventory"
    onto = Ontology(verified=True)
    onto.objects["transaction"] = ObjectType("transaction", "transactions", ("txn_id",))
    onto.objects["lot"] = ObjectType("lot", swapped, ("sku",))
    onto.links["txn_of_lot"] = LinkType(
        "txn_of_lot", "transaction", ("sku",), "lot", ("sku",), "many_to_one"
    )
    # sku is text. Untagged, it never enters the payload, and the membership
    # check drops the join before the rename guard runs.
    _tag(onto, ("transactions", "sku"), ("inventory", "sku"))
    payload = build_dms_payload(
        "q", serving=lake, readable={"transactions", "inventory"}, ontology=onto
    )
    assert payload is not None
    assert "sku" in _cols(payload, "transactions")
    assert "sku" in _cols(payload, "inventory")
    assert "joins" not in payload


@pytest.mark.parametrize(
    "values",
    [
        (123456789, 1123456789),
        (10203145678, 50607085432),
    ],
)
def test_an_integer_that_lost_its_leading_zero_stays_home(
    tmp_path: Path, values: tuple[int, int]
) -> None:
    # member_id is a cleared name. Stored as BIGINT, 012/011 mobiles and
    # MyKad years 2001 and 2005 lose the leading 0, and the classifier misses
    # the shortened form. The zero-padded form is classified, so the column
    # stays home. points_count has no such value and is sent.
    db = _lake_with(
        tmp_path,
        "CREATE TABLE loyalty (member_id BIGINT, points_count INTEGER)",
        f"INSERT INTO loyalty VALUES ({values[0]}, 4), ({values[1]}, 9)",
    )
    payload = build_dms_payload("loyalty points", serving=db, readable={"loyalty"})
    assert payload is not None
    assert _cols(payload, "loyalty") == ["points_count"]
    _assert_cortex_samples_nothing_personal(db, payload)


# Catalog spellings of one whole number. DuckDB stores INT / INT4 as INTEGER
# and INT8 as BIGINT. Postgres int/int4/int8/bigint and MySQL INT are these.
_INTEGER_SPELLINGS = (
    "INTEGER",
    "INT",
    "INT4",
    "INT8",
    "BIGINT",
    "HUGEINT",
    "UBIGINT",
)


@pytest.mark.parametrize("spelling", _INTEGER_SPELLINGS)
def test_a_phone_under_every_integer_spelling_stays_home(tmp_path: Path, spelling: str) -> None:
    db = _lake_with(
        tmp_path,
        f"CREATE TABLE loyalty (member_id {spelling}, points_count INTEGER)",
        "INSERT INTO loyalty VALUES (123456789, 4), (1123456789, 9)",
    )
    payload = build_dms_payload("loyalty points", serving=db, readable={"loyalty"})
    assert payload is not None
    assert _cols(payload, "loyalty") == ["points_count"]
    _assert_cortex_samples_nothing_personal(db, payload)


def test_grant_names_fold_ascii_only(tmp_path: Path) -> None:
    db = _lake_with(
        tmp_path,
        'CREATE TABLE "Äpfel" (apfel_id INTEGER)',
        'CREATE TABLE "äpfel" (apfel_id INTEGER)',
        "CREATE TABLE Pears (pear_id INTEGER)",
    )
    payload = build_dms_payload("q", serving=db, readable={"Äpfel", "pears"})
    assert payload is not None
    assert _names(payload) == {"Äpfel", "Pears"}


# --- served: the payload as it goes over HTTP to Cortex -------------------------


def test_the_payload_on_the_wire_carries_no_person_column_and_the_answer_is_served(
    tmp_path: Path,
) -> None:
    from cortex_client.client import CortexClient
    from cortex_client.models import AskRequest

    labels = ",".join(f"'Person Number {i}'" for i in range(2000))
    db = _lake_with(
        tmp_path,
        f"CREATE TYPE approver AS ENUM ({labels})",
        "CREATE TABLE orders (order_id INTEGER, location_id VARCHAR, total_myr DOUBLE, "
        "handled_by VARCHAR, pic VARCHAR, consignee VARCHAR, status VARCHAR, "
        "approval_status approver)",
        "INSERT INTO orders VALUES (1, 'WH-A', 10.0, 'Ahmad bin Ismail', 'Tan Wei Ming', "
        "'Lim Kok Wing', 'open', 'Person Number 7')",
        "CREATE TABLE locations (location_id VARCHAR, capacity_kg DOUBLE)",
        "INSERT INTO locations VALUES ('WH-A', 100.0)",
    )
    onto = Ontology(verified=True)
    onto.objects["order"] = ObjectType("order", "orders", ("order_id",))
    onto.objects["location"] = ObjectType("location", "locations", ("location_id",))
    onto.objects["site"] = ObjectType(
        "site", "(SELECT * FROM locations USING SAMPLE 50%)", ("location_id",)
    )
    onto.links["order_at_location"] = LinkType(
        "order_at_location", "order", ("location_id",), "location", ("location_id",),
        "many_to_one",
    )
    onto.links["order_at_site"] = LinkType(
        "order_at_site", "order", ("location_id",), "site", ("location_id",), "many_to_one"
    )
    _tag(onto, ("orders", "location_id"), ("locations", "location_id"))
    question = "open order value by site"
    payload = build_dms_payload(
        question, serving=db, readable={"orders", "locations"}, ontology=onto
    )
    assert payload is not None

    seen: list[bytes] = []
    answer = {
        "answer": "Open orders at WH-A total RM 10.00.",
        "audit_id": "aud_452",
        "route": "plan_sql",
        "provenance": {"badge": "session", "layer": "plan_sql"},
        "plan_sql_rung": "plan",
        "sql_used": "SELECT o.location_id, SUM(o.total_myr) AS total_myr FROM orders o "
        "JOIN locations l ON o.location_id = l.location_id WHERE o.status = 'open' "
        "GROUP BY 1",
        "rows": [{"location_id": "WH-A", "total_myr": 10.0}],
        "row_count": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=answer)

    client = CortexClient("http://cortex.test")
    client._client.set_httpx_client(
        httpx.Client(base_url="http://cortex.test", transport=httpx.MockTransport(handler))
    )
    resp = client.ask(
        AskRequest(question=question, session_id="s1", space_id="sp1", dms_payload=payload)
    )

    (raw,) = seen
    body = json.loads(raw)
    sent = body["dms_payload"]
    assert sent == payload
    _accepts(sent)
    assert len(json.dumps(sent).encode()) <= MAX_PAYLOAD_BYTES
    text = raw.decode()
    for leaked in (
        "handled_by",
        "pic",
        "consignee",
        "approval_status",
        "Ahmad",
        "Tan Wei Ming",
        "Person Number",
    ):
        assert leaked not in text
    # Text goes only tagged: the two keys. status and the ENUM stay home.
    assert {t["name"]: [c["name"] for c in t["columns"]] for t in sent["tables"]} == {
        "orders": ["location_id", "order_id", "total_myr"],
        "locations": ["location_id", "capacity_kg"],
    }
    _assert_cortex_samples_nothing_personal(db, sent)
    assert [j["relation"] for j in sent["joins"]] == ["order_at_location many_to_one"]
    # What the customer is shown: the served answer text and its rows.
    assert resp.answer == "Open orders at WH-A total RM 10.00."
    assert resp.rows == [{"location_id": "WH-A", "total_myr": 10.0}]
    assert resp.badge == "session"
