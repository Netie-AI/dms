"""AO-KEY-01. A term key reaches Ontology.from_store. No skip, no xfail."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import duckdb
import psycopg
import pytest
from dms_core.control_plane.onto_data import (
    items_from_proposal,
    ontology_data_store,
    reset_ontology_data_store,
    store_proposal,
)
from dms_executor.grain_ai import approved_grain_names
from dms_executor.ontology import Ontology
from test_ao_vendor_01 import _recorded


def _clear_pg_ontology() -> None:
    """The flag-on store is durable. A test starts from an empty tenant."""
    if os.environ.get("DMS_ONTO_PG", "0").strip() != "1":
        return
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return
    from dms_core.control_plane.onto_data import _DEFAULT_TENANT
    from dms_core.control_plane.session import set_tenant_context

    tenant = os.environ.get("DMS_TENANT_ID", "").strip() or _DEFAULT_TENANT
    conn = psycopg.connect(url)
    try:
        set_tenant_context(conn, tenant, role="admin")
        conn.execute("DELETE FROM dms.ontology_items")
        conn.execute("DELETE FROM dms.ontology_versions")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    reset_ontology_data_store()
    _clear_pg_ontology()
    yield
    reset_ontology_data_store()
    _clear_pg_ontology()


def test_round_trip_from_store_has_three_objects_and_three_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_executor.ao_compile import compile_source

    _arm(monkeypatch)
    proposal = compile_source(_vendor_conn(), transport=_recorded_transport)
    body = proposal.to_json()
    assert body["unkeyed"] == []
    assert {row["table"]: row["key"] for row in body["terms"]} == {
        "customers": ["id"],
        "products": ["id"],
        "orders": ["id"],
    }
    stored = store_proposal(body, created_by="steward")
    store = ontology_data_store()
    for item in stored["items"]:
        assert store.approve(item["id"], actor="steward") is not None
    onto = Ontology.from_store()
    assert len(onto.objects) == 3
    assert set(onto.objects) == {"Customer", "Order", "Product"}
    assert len(onto.links) == 3
    assert all(obj.key == ("id",) for obj in onto.objects.values())
    assert approved_grain_names()


def test_no_pk_and_empty_model_pk_is_unkeyed_not_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_executor.ao_compile import compile_source

    _arm(monkeypatch)
    proposal = compile_source(
        _plain_conn(),
        transport=_pk_transport([]),
    )
    body = proposal.to_json()
    assert body["terms"] == []
    assert body["unkeyed"] == ["widgets"]
    stored = store_proposal(body, created_by="steward")
    assert stored["items"] == []
    dead = items_from_proposal(
        {
            "terms": [
                {
                    "name": "Widget",
                    "table": "widgets",
                    "description": "",
                    "key": [],
                    "attributes": [],
                }
            ]
        }
    )
    assert dead == []
    assert Ontology.from_store().objects == {}


def test_model_pk_column_names_become_the_term_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_executor.ao_compile import compile_source

    _arm(monkeypatch)
    proposal = compile_source(
        _plain_conn(),
        transport=_pk_transport(["sku"]),
    )
    body = proposal.to_json()
    assert body["unkeyed"] == []
    assert len(body["terms"]) == 1
    assert body["terms"][0]["key"] == ["sku"]
    stored = store_proposal(body, created_by="steward")
    objects = [item for item in stored["items"] if item["kind"] == "object"]
    assert len(objects) == 1
    assert objects[0]["payload"]["key"] == ["sku"]


def _arm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", "ov-key-01")
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DMS_AO_COMPILE", "1")


def _vendor_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR)")
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, sku VARCHAR)")
    conn.execute(
        "CREATE TABLE orders ("
        "id INTEGER PRIMARY KEY, "
        "customer_id INTEGER, "
        "product_id INTEGER, "
        "qty INTEGER, "
        "FOREIGN KEY (product_id) REFERENCES products(id))"
    )
    conn.execute("INSERT INTO customers VALUES (1, 'ada'), (2, 'bea')")
    conn.execute("INSERT INTO products VALUES (10, 'sku-a')")
    conn.execute("INSERT INTO orders VALUES (100, 1, 10, 3), (101, 1, 10, 1)")
    return conn


def _plain_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute("CREATE TABLE widgets (sku VARCHAR, name VARCHAR)")
    conn.execute("INSERT INTO widgets VALUES ('sku-a', 'one')")
    return conn


def _recorded_transport(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    assert url.endswith("/v1/chat/completions")
    assert headers.get("Authorization", "").startswith("Bearer ")
    return _recorded(body)


def _pk_transport(pk_column_names: list[str]):
    def transport(
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        assert url.endswith("/v1/chat/completions")
        assert headers.get("Authorization", "").startswith("Bearer ")
        schema = body["schema"]
        if schema == "ColumnDescriptionResult":
            return {"descriptions": []}
        if schema == "FkAndPkResult":
            return {
                "fk_suggestions": [],
                "pk_column_names": list(pk_column_names),
                "is_junction_table": False,
                "junction_table_rationale": "",
            }
        if schema == "RawTableTermsResult":
            return {
                "terms": [
                    {
                        "name": "Widget",
                        "description": "recorded",
                        "attributes": [
                            {"source_column": "sku", "display_name": "Sku"},
                            {"source_column": "name", "display_name": "Name"},
                        ],
                    }
                ]
            }
        raise AssertionError(f"unexpected schema {schema}")

    return transport
