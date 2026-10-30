"""WIRE-01a: the vendored contract and generated client are at 1.5.0.

Cortex main speaks contract 1.5.0. Its ask accepts ``dms_payload`` (the
tables, columns and joins a question may use, which switches on the Cortex
plan and SQL ladder) and its Answer can carry the ``reconfirm`` badge, the
ladder rung and per-step model calls. A 1.2.0 client cannot send the payload,
and it raises on the ``reconfirm`` badge, so the whole ask fails.

Nothing here changes what DMS sends today: with no payload the wire body is
byte-for-byte the 1.2.0 body.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "contract" / "openapi-1.5.0.json"

#: What DMS main (a52ccb2, client generated from 1.2.0) puts on the wire.
_MAIN_BODIES = [
    {"tenant_id": None, "question": "q?", "session_id": "ses_1", "space_id": "sp_1"},
    {"tenant_id": None, "question": "q?", "session_id": None, "space_id": None},
    {"tenant_id": "t", "question": "q?", "session_id": "s", "space_id": "p"},
]

_PAYLOAD = {
    "tables": [
        {
            "name": "inventory",
            "description": "stock on hand",
            "columns": [
                {"name": "sku", "type": "VARCHAR", "description": None},
                {"name": "quantity_kg", "type": "DOUBLE", "description": "kilograms"},
            ],
        },
        {"name": "locations", "columns": [{"name": "location_id", "type": "VARCHAR"}]},
    ],
    "joins": [
        {
            "left_table": "inventory",
            "left_column": "location_id",
            "right_table": "locations",
            "right_column": "location_id",
            "relation": "many_to_one",
        }
    ],
}

_ANSWER_150 = {
    "answer": "I could not ground that; did you mean stock by location?",
    "audit_id": "aud_1",
    "route": "abstain",
    "provenance": {"badge": "reconfirm", "layer": "plan_sql"},
    "plan_sql_rung": "error-fed-retry-1",
    "model_calls": {"plan": 1, "sql": 2, "total": 3},
    "reconfirm": {"why": "no column matched", "closest_question": "Stock by location?"},
    "served_provider": "openvault",
    "served_model": "some-model",
    "served_local": False,
}


def _recording_client(seen: list[Any], answer: dict[str, Any]) -> Any:
    from cortex_client.client import CortexClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=answer)

    client = CortexClient("http://cortex.test")
    client._client.set_httpx_client(
        httpx.Client(base_url="http://cortex.test", transport=httpx.MockTransport(handler))
    )
    return client


def test_the_vendored_150_spec_is_pinned_and_declares_dms_payload() -> None:
    digest = SPEC.with_name(SPEC.name + ".sha256")
    assert SPEC.is_file() and digest.is_file()
    expected = digest.read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(SPEC.read_bytes()).hexdigest() == expected
    schemas = json.loads(SPEC.read_text(encoding="utf-8"))["components"]["schemas"]
    assert "dms_payload" in schemas["AskRequest"]["properties"]
    assert {"AskPayload", "AskPayloadTable", "AskPayloadColumn", "AskPayloadJoin"} <= set(schemas)


def test_the_generated_ask_request_round_trips_a_payload() -> None:
    from cortex_client.generated.models import AskRequest as GenAskRequest

    body = GenAskRequest.from_dict({"question": "q?", "dms_payload": _PAYLOAD})
    assert body.to_dict()["dms_payload"] == _PAYLOAD


def test_a_150_answer_with_the_reconfirm_badge_parses_through_the_client() -> None:
    seen: list[Any] = []
    from cortex_client.models import AskRequest

    resp = _recording_client(seen, _ANSWER_150).ask(AskRequest(question="q?"))
    assert resp.badge == "reconfirm"
    assert resp.plan_sql_rung == "error-fed-retry-1"
    assert resp.model_calls == {"plan": 1, "sql": 2, "total": 3}
    assert resp.reconfirm == {"why": "no column matched", "closest_question": "Stock by location?"}
    assert resp.served_model == "some-model" and resp.served_provider == "openvault"
    assert resp.served_local is False


@pytest.mark.parametrize("index", range(len(_MAIN_BODIES)))
def test_without_a_payload_the_wire_body_is_unchanged(index: int) -> None:
    from cortex_client.models import AskRequest

    sent = _MAIN_BODIES[index]
    seen: list[Any] = []
    answer = {
        "answer": "x",
        "audit_id": "a1",
        "route": "sql",
        "provenance": {"badge": "certified", "layer": "L0"},
    }
    _recording_client(seen, answer).ask(
        AskRequest(
            question=sent["question"],
            session_id=sent["session_id"],
            space_id=sent["space_id"],
            tenant_id=sent["tenant_id"],
        )
    )
    assert seen == [sent]


def test_a_payload_and_a_confirm_reach_the_wire_only_when_set() -> None:
    from cortex_client.models import AskRequest

    seen: list[Any] = []
    answer = {
        "answer": "x",
        "audit_id": "a1",
        "route": "sql",
        "provenance": {"badge": "certified", "layer": "L0"},
    }
    _recording_client(seen, answer).ask(
        AskRequest(question="q?", session_id="s", dms_payload=_PAYLOAD, plan_sql_confirm="yes")
    )
    assert seen[0]["dms_payload"] == _PAYLOAD
    assert seen[0]["plan_sql_confirm"] == "yes"
    assert seen[0]["question"] == "q?" and seen[0]["session_id"] == "s"
