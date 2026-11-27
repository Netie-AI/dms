"""PAYLOAD-MASK-FLAG-01 (dms#456): AskPayloadColumn.masked is a JSON bool.

A masked or PII column never goes out as false. A column whose mask state
is unknown goes out as true. The wire field is a bool, never a string or a
number. Sample cells are not sent and not logged.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from dms_executor.ask_column_mask import column_masked, stamp_ask_payload

_EMAIL = "aisha.zulkifli@pii-seed.example"
_CARD = "4111111111111111"
_SENTINEL = "SENTINEL-VALUE-NOT-ON-WIRE"
_TAGGED = {
    "non_personal": [
        {
            "table": "orders",
            "column": "region",
            "source": "steward",
            "field": "region",
        }
    ]
}


def _payload() -> dict[str, Any]:
    return {
        "tables": [
            {
                "name": "orders",
                "columns": [
                    {"name": "email", "type": "VARCHAR"},
                    {"name": "col1", "type": "VARCHAR"},
                    {"name": "quantity", "type": "DOUBLE"},
                    {"name": "status", "type": "INTEGER"},
                    {"name": "region", "type": "VARCHAR"},
                    {"name": "is_open", "type": "BOOLEAN"},
                    {"name": "birth_date", "type": "DATE"},
                ],
            }
        ],
        "joins": [],
    }


def _samples() -> dict[tuple[str, str], list[str]]:
    return {
        ("orders", "email"): [_EMAIL],
        ("orders", "col1"): ["x"],
        ("orders", "quantity"): ["10", "20"],
        ("orders", "status"): [_CARD],
        ("orders", "region"): ["east"],
        ("orders", "is_open"): ["true"],
        ("orders", "birth_date"): ["1990-01-15"],
    }


def _flags(payload: dict[str, Any]) -> dict[str, bool]:
    columns = payload["tables"][0]["columns"]
    return {column["name"]: column["masked"] for column in columns}


def _recording_client(seen: list[Any]) -> Any:
    from cortex_client.client import CortexClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "answer": "x",
                "audit_id": "a1",
                "route": "sql",
                "provenance": {"badge": "certified", "layer": "L0"},
            },
        )

    client = CortexClient("http://cortex.test")
    client._client.set_httpx_client(
        httpx.Client(base_url="http://cortex.test", transport=httpx.MockTransport(handler))
    )
    return client


def test_must_fail_masked_column_never_sends_false() -> None:
    """email by name, and a cleared name whose values are a card, stay true."""
    flags = _flags(stamp_ask_payload(_payload(), samples=_samples()) or {})
    assert flags["email"] is True
    assert flags["status"] is True
    assert column_masked("orders", "email", type_name="VARCHAR", values=[_EMAIL]) is True
    assert column_masked("orders", "quantity", type_name="DOUBLE", values=[_EMAIL]) is True


def test_must_fail_unknown_column_sends_true() -> None:
    flags = _flags(stamp_ask_payload(_payload(), samples=_samples()) or {})
    assert flags["col1"] is True
    assert column_masked("orders", "col1", type_name="VARCHAR", values=["x"]) is True
    assert column_masked(None, "quantity", type_name="DOUBLE", values=["10"]) is True
    assert column_masked("orders", "quantity", type_name="DOUBLE", values=None) is True
    # Text is not cleared by its name. No tag, so the mask state is unknown.
    assert flags["region"] is True


def test_clear_column_sends_false_only_with_evidence() -> None:
    flags = _flags(stamp_ask_payload(_payload(), ontology=_TAGGED, samples=_samples()) or {})
    assert flags["quantity"] is False
    assert flags["is_open"] is False
    assert flags["region"] is False
    assert flags["birth_date"] is True
    assert type(flags["quantity"]) is bool


def test_stamp_drops_sample_cells(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(0)
    stamped = stamp_ask_payload(
        _payload(),
        samples={("orders", "quantity"): [_SENTINEL, "10"]},
    )
    body = json.dumps(stamped)
    assert _SENTINEL not in body
    assert _EMAIL not in body
    assert "values" not in body
    assert "samples" not in body
    assert _SENTINEL not in caplog.text
    assert "quantity" not in caplog.text
    assert "email" not in caplog.text


def test_must_fail_wire_field_is_a_bool_never_a_string_or_number() -> None:
    from cortex_client.models import AskRequest

    stamped = stamp_ask_payload(_payload(), ontology=_TAGGED, samples=_samples())
    raw = {
        "tables": [
            {
                "name": "orders",
                "columns": [
                    {"name": "email", "type": "VARCHAR", "masked": "false", "values": [_EMAIL]},
                    {"name": "quantity", "type": "DOUBLE", "masked": 0, "samples": ["10"]},
                    {"name": "region", "type": "VARCHAR", "masked": "true"},
                    {"name": "is_open", "type": "BOOLEAN", "masked": False},
                ],
            }
        ]
    }
    seen: list[Any] = []
    _recording_client(seen).ask(AskRequest(question="q?", session_id="s", dms_payload=stamped))
    _recording_client(seen).ask(AskRequest(question="q?", session_id="s", dms_payload=raw))
    built = seen[0]["dms_payload"]["tables"][0]["columns"]
    built_flags = {column["name"]: column["masked"] for column in built}
    forced = {
        column["name"]: column["masked"]
        for column in seen[1]["dms_payload"]["tables"][0]["columns"]
    }
    for column in built:
        assert type(column["masked"]) is bool
    assert built_flags["email"] is True
    assert built_flags["status"] is True
    assert built_flags["col1"] is True
    assert built_flags["quantity"] is False
    assert forced["email"] is True
    assert forced["quantity"] is True
    assert forced["region"] is True
    assert forced["is_open"] is False
    for column in seen[1]["dms_payload"]["tables"][0]["columns"]:
        assert type(column["masked"]) is bool
    wire = json.dumps(seen)
    assert _EMAIL not in wire
    assert _SENTINEL not in wire
    assert '"masked": "false"' not in wire
    assert '"masked": 0' not in wire
    assert '"masked": "true"' not in wire


def test_installed_contract_accepts_a_bool_masked_flag() -> None:
    """The installed AskPayloadColumn takes a bool ``masked`` without a 422.

    Cortex main's 1.7 model has no ``masked`` field and no ``extra=forbid``,
    so the key is dropped. Contract 1.9 (Cortex#386) declares the field as
    ``bool | None`` and keeps it. Either way a JSON bool is safe. Cortex
    does not advertise the contract version on ``/health`` or
    ``/health/features``, so DMS sends the bool instead of gating on an
    advertisement that is not there. A string is never sent; the client
    turns that into true before the body is built.
    """
    from cortex_contract.answer import AskPayloadColumn

    parsed = AskPayloadColumn.model_validate({"name": "email", "type": "VARCHAR", "masked": True})
    dumped = parsed.model_dump()
    assert dumped["name"] == "email"
    if "masked" in dumped:
        assert dumped["masked"] is True


def test_generated_client_keeps_masked_as_a_bool() -> None:
    from cortex_client.generated.models import AskRequest as GenAskRequest

    body = GenAskRequest.from_dict(
        {
            "question": "q?",
            "dms_payload": {
                "tables": [{"name": "orders", "columns": [{"name": "sku", "masked": False}]}]
            },
        }
    )
    column = body.to_dict()["dms_payload"]["tables"][0]["columns"][0]
    assert column["masked"] is False
    assert type(column["masked"]) is bool
