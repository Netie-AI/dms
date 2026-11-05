"""PII-01 (dms#272) on the auto-ontology compile path (DMS_AO_COMPILE).

``onto_compile.compile_warehouse`` runs against a loopback fake OpenVault that
records every request body. The fixture table holds a person name, an email,
a phone, and a Malaysian IC column. No recorded model request may contain any
of those raw values. The non-personal FK values (``CUST-0001``) still reach
the FK-target prompt, and the inferred FK is still proposed.

The must-fail witness swaps the DMS guard for a pass-through (the code before
this fix) and shows the same check finds the raw values on the wire.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
import test_ao_ov_auth_01 as auth
from test_ao_ov_auth_01 import ISSUED_KEY, FakeOpenVault, SpyStore, onto_compile

PERSONAL = {
    "full_name": ["Aisyah Binti Rahman", "Tan Wei Ming", "Rajesh Kumar"],
    "email": ["aisyah.rahman@example.my", "wei.ming.tan@example.my", "rajesh.k@example.my"],
    "phone": ["+60 12-345 6789", "012-987 6543", "+60198765432"],
    "ic_number": ["850615-14-5678", "900101-10-1234", "781231-08-4321"],
}
CUSTOMER_IDS = ["CUST-0001", "CUST-0002", "CUST-0003"]
SEGMENTS = ["SME", "RETAIL", "SME"]


def _respond(body: dict[str, Any]) -> dict[str, Any]:
    """Deterministic model. The FK pick needs the non-personal sample values."""
    schema = body["schema"]
    text = "\n".join(message["content"] for message in body["messages"])
    table = ""
    for line in text.splitlines():
        if line.startswith("Table:"):
            table = line.split(":", 1)[1].strip().split(".")[-1]
            break
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
        columns = {
            "customers": [("customer_id", "Customer Id"), ("segment", "Segment")],
            "orders": [("order_id", "Order Id"), ("amount", "Amount")],
        }[table]
        name = {"customers": "Customer", "orders": "Order"}[table]
        return {
            "terms": [
                {
                    "name": name,
                    "description": "recorded",
                    "attributes": [
                        {"source_column": col, "display_name": label} for col, label in columns
                    ],
                }
            ]
        }
    if schema == "FkHitSelection":
        # Picks the target only when the FK column's own values are shown.
        picked = "customers.customer_id" if "CUST-0001" in text else None
        return {"selected_id": picked, "rationale": "recorded"}
    raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture
def ov(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOpenVault]:
    monkeypatch.setattr(auth, "_recorded", _respond)
    fake = FakeOpenVault()
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "pii.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(
        "CREATE TABLE customers ("
        "customer_id VARCHAR PRIMARY KEY, full_name VARCHAR, email VARCHAR, "
        "phone VARCHAR, ic_number VARCHAR, segment VARCHAR)"
    )
    for i, cid in enumerate(CUSTOMER_IDS):
        conn.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)",
            [
                cid,
                PERSONAL["full_name"][i],
                PERSONAL["email"][i],
                PERSONAL["phone"][i],
                PERSONAL["ic_number"][i],
                SEGMENTS[i],
            ],
        )
    # customer_id is not declared as an FK: the compile has to infer it.
    conn.execute(
        "CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id VARCHAR, amount INTEGER)"
    )
    conn.execute(
        "INSERT INTO orders VALUES (1, 'CUST-0001', 120), (2, 'CUST-0002', 80), "
        "(3, 'CUST-0001', 45)"
    )
    conn.close()
    return path


def _env(monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault) -> None:
    monkeypatch.setenv("DMS_AO_COMPILE", "1")
    monkeypatch.setenv("OPENVAULT_URL", ov.url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.setenv("OPENVAULT_API_KEY", ISSUED_KEY)


def _wire(ov: FakeOpenVault) -> list[str]:
    return [json.dumps(call["body"], ensure_ascii=False) for call in ov.requests]


def _leaked(bodies: list[str]) -> list[str]:
    raw = [value for values in PERSONAL.values() for value in values]
    return sorted({value for value in raw for body in bodies if value in body})


def _compile(warehouse: Path) -> dict[str, Any]:
    return onto_compile.compile_warehouse(warehouse, str(uuid.uuid4()), store=SpyStore())


def test_no_model_request_carries_a_raw_personal_value(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path
) -> None:
    _env(monkeypatch, ov)
    result = _compile(warehouse)
    bodies = _wire(ov)
    assert bodies, "the compile made no model call"
    assert result["model_calls"] == len(bodies)
    for values in PERSONAL.values():
        for value in values:
            for body in bodies:
                assert value not in body, value
    # The proposal the user stores carries none of them either.
    stored = json.dumps(result["proposal"], ensure_ascii=False)
    assert _leaked([stored]) == []
    # Personal columns are shown as typed placeholders, not dropped silently.
    wire = "\n".join(bodies)
    for kind in ("name", "email", "phone", "nric"):
        assert f"DMSMASK_{kind}_01" in wire, kind
    assert "DMSMASK_name_04" not in wire  # at most three placeholders a column


def test_non_personal_values_reach_the_model_and_the_fk_is_still_proposed(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path
) -> None:
    _env(monkeypatch, ov)
    result = _compile(warehouse)
    picks = [
        json.dumps(call["body"], ensure_ascii=False)
        for call in ov.requests
        if call["body"]["schema"] == "FkHitSelection"
    ]
    assert len(picks) == 1
    assert "CUST-0001" in picks[0]
    descriptions = [
        json.dumps(call["body"], ensure_ascii=False)
        for call in ov.requests
        if call["body"]["schema"] == "ColumnDescriptionResult"
    ]
    assert any("RETAIL" in body for body in descriptions)
    proposal = result["proposal"]
    assert proposal["inferred_fks"] == [
        {
            "source_table": "orders",
            "source_column": "customer_id",
            "target_table": "customers",
            "target_column": "customer_id",
        }
    ]
    assert {row["name"] for row in proposal["terms"]} == {"Customer", "Order"}
    attrs = {(row["table"], row["source_column"]) for row in proposal["attributes"]}
    assert ("customers", "segment") in attrs
    assert result["counts"]["link"] >= 1


def test_must_fail_witness_without_the_guard_the_raw_values_are_on_the_wire(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path
) -> None:
    """The pre-fix behaviour: every sampled value goes to the prompt as is."""
    import dms_executor.ao_compile as ao_compile

    _env(monkeypatch, ov)
    monkeypatch.setattr(ao_compile, "model_samples", lambda _t, _c, values: (list(values), False))
    _compile(warehouse)
    leaked = _leaked(_wire(ov))
    for values in PERSONAL.values():
        assert any(value in leaked for value in values), values


def test_no_bound_guard_sends_no_sample_values() -> None:
    from ao_vendor.utils.sample_guard import guard_samples, model_sample_values

    assert guard_samples("customers", "segment", ["SME"]) == ([], True)
    assert model_sample_values("customers", "email", PERSONAL["email"]) == []
    assert model_sample_values("customers", "email", None) is None


def test_guard_masks_by_name_and_by_value_and_caps_the_rest() -> None:
    from dms_executor.ao_compile import MODEL_SAMPLE_CAP, model_samples

    for column, values in PERSONAL.items():
        safe, personal = model_samples("customers", column, list(values))
        assert personal, column
        assert not set(values) & {str(v) for v in safe}, column
        assert len(safe) <= 3
    # A misnamed column is caught by its values.
    safe, personal = model_samples("orders", "contact", ["a.b@example.my", "c.d@example.my"])
    assert personal and safe == ["DMSMASK_email_01", "DMSMASK_email_02"]
    many = [f"CUST-{i:04d}" for i in range(500)]
    safe, personal = model_samples("orders", "customer_id", many)
    assert not personal
    assert safe == many[:MODEL_SAMPLE_CAP]
