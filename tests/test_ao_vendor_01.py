"""AO-VENDOR-01: vendored auto-ontology compile, OpenVault role, flag off."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

VENDOR_ROOT = Path(__file__).resolve().parents[1] / "packages" / "ao_vendor"
PIN = "eb8dd0621d07db3033fe3fddc68f62ff72614610"
_MODEL_KEYS = frozenset(
    {
        "model",
        "model_id",
        "model_name",
        "served_model",
        "served_provider",
        "provider",
        "strict",
    }
)


def test_license_files_present() -> None:
    license_text = (VENDOR_ROOT / "LICENSE").read_text(encoding="utf-8")
    notice = (VENDOR_ROOT / "OPEN_SOURCE_NOTICE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "NVIDIA" in license_text
    assert "opensource.nvidia.com" in notice
    sources = list((VENDOR_ROOT / "ao_vendor").rglob("*.py"))
    assert sources
    for path in sources:
        head = path.read_text(encoding="utf-8")[:500]
        assert "SPDX-License-Identifier: Apache-2.0" in head, path.name
        assert "SPDX-FileCopyrightText:" in head, path.name


def test_flag_off_has_no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.delenv("DMS_AO_COMPILE", raising=False)
    sys.modules.pop("dms_executor.ao_compile", None)
    for name in list(sys.modules):
        if name == "ao_vendor" or name.startswith("ao_vendor."):
            del sys.modules[name]

    import dms_executor.ao_compile as mod

    assert not _ao_loaded()

    class Boom:
        def __getattribute__(self, name: str) -> Any:
            raise AssertionError(f"connection touched: {name}")

    proposal = mod.compile_source(Boom())
    body = proposal.to_json()
    assert proposal.status == "off"
    assert proposal.served is False
    assert body["status"] == "off"
    assert body["terms"] == []
    assert body["attributes"] == []
    assert body["inferred_fks"] == []
    json.dumps(body)
    assert not _ao_loaded()


def test_compile_proposes_terms_attributes_and_inferred_fk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_executor.ao_compile import PROPOSAL_JSON_SCHEMA, check_proposal_schema, compile_source

    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_filetoken")
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DMS_AO_COMPILE", "1")

    captured: list[dict[str, Any]] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        assert url == "http://127.0.0.1:9/v1/chat/completions"
        assert headers.get("Authorization") == "Bearer ov_filetoken"
        captured.append(body)
        _reject_model_ids(body, secret="ov_filetoken")
        return _recorded(body)

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

    proposal = compile_source(conn, transport=transport)
    body = proposal.to_json()
    json.dumps(body)
    check_proposal_schema(body)
    assert body["status"] == "proposed"
    assert body["served"] is False
    assert body["vendor_sha"] == PIN
    assert PROPOSAL_JSON_SCHEMA["properties"]["status"]["const"] == "proposed"
    assert PROPOSAL_JSON_SCHEMA["properties"]["served"]["const"] is False

    terms = {row["name"]: row["table"] for row in body["terms"]}
    assert terms["Customer"] == "customers"
    assert terms["Product"] == "products"
    assert terms["Order"] == "orders"

    attributes = {
        (row["table"], row["source_column"]): row["display_name"] for row in body["attributes"]
    }
    assert attributes[("customers", "name")] == "Customer Name"
    assert attributes[("products", "sku")] == "Sku"
    assert attributes[("orders", "qty")] == "Quantity"
    assert ("orders", "customer_id") not in attributes

    inferred = body["inferred_fks"]
    assert inferred == [
        {
            "source_table": "orders",
            "source_column": "customer_id",
            "target_table": "customers",
            "target_column": "id",
        }
    ]
    declared = {
        (row["source_table"], row["source_column"], row["target_table"], row["target_column"])
        for row in body["declared_fks"]
    }
    assert ("orders", "product_id", "products", "id") in declared
    assert ("orders", "customer_id", "customers", "id") not in declared
    assert body["joins"]
    assert captured
    assert {row["schema"] for row in captured} >= {
        "FkAndPkResult",
        "RawTableTermsResult",
        "FkHitSelection",
    }
    for row in captured:
        assert row["role"] == "ontology"


def test_openvault_key_wins_over_cortex_key_and_names_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ao_vendor.llm.ov_client import OpenVaultOntologyClient
    from ao_vendor.semantic.models import FkHitSelection

    monkeypatch.setenv("CORTEX_API_KEY", "ov_cortexkey")
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_envkey")
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    seen: list[dict[str, str]] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        assert url.endswith("/v1/chat/completions")
        seen.append(headers)
        _reject_model_ids(body, secret="ov_envkey")
        assert "ov_cortexkey" not in json.dumps(body)
        return {"selected_id": None, "rationale": ""}

    from ao_vendor.llm.messages import HumanMessage

    client = OpenVaultOntologyClient(transport=transport)
    parsed = client.complete_structured([HumanMessage(content="ping")], FkHitSelection)
    assert parsed.selected_id is None
    assert seen[0]["Authorization"] == "Bearer ov_envkey"


def _ao_loaded() -> bool:
    import sys

    return any(name == "ao_vendor" or name.startswith("ao_vendor.") for name in sys.modules)


def _reject_model_ids(body: dict[str, Any], *, secret: str) -> None:
    encoded = json.dumps(body)
    assert secret not in encoded
    for needle in ("gpt-", "claude", "llama", "gemini"):
        assert needle not in encoded.casefold()
    _walk(body)


def _walk(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert str(key).casefold() not in _MODEL_KEYS
            _walk(item)
    elif isinstance(value, list):
        for item in value:
            _walk(item)


def _table_name(body: dict[str, Any]) -> str:
    text = "\n".join(message["content"] for message in body["messages"])
    for line in text.splitlines():
        if line.startswith("Table:"):
            token = line.split(":", 1)[1].strip()
            return token.split(".")[-1]
    return ""


def _recorded(body: dict[str, Any]) -> dict[str, Any]:
    schema = body["schema"]
    table = _table_name(body)
    if schema == "ColumnDescriptionResult":
        return {"descriptions": []}
    if schema == "FkAndPkResult":
        suggestions = []
        if table == "orders":
            suggestions = [{"column_name": "customer_id", "rationale": "recorded"}]
        return {
            "fk_suggestions": suggestions,
            "pk_column_names": [],
            "is_junction_table": False,
            "junction_table_rationale": "",
        }
    if schema == "RawTableTermsResult":
        return _terms_for(table)
    if schema == "FkHitSelection":
        return {"selected_id": "customers.id", "rationale": "recorded"}
    raise AssertionError(f"unexpected schema {schema}")


def _terms_for(table: str) -> dict[str, Any]:
    columns = {
        "customers": [
            ("id", "Customer Id"),
            ("name", "Customer Name"),
        ],
        "products": [
            ("id", "Product Id"),
            ("sku", "Sku"),
        ],
        "orders": [
            ("id", "Order Id"),
            ("qty", "Quantity"),
        ],
    }
    names = {"customers": "Customer", "products": "Product", "orders": "Order"}
    if table not in columns:
        raise AssertionError(f"unexpected table {table}")
    return {
        "terms": [
            {
                "name": names[table],
                "description": "recorded",
                "attributes": [
                    {"source_column": column, "display_name": label}
                    for column, label in columns[table]
                ],
            }
        ]
    }
