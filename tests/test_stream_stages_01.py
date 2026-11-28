"""STREAM-STAGES-01. Stage events over SSE. Flag DMS_ASK_STREAM, default off.

Flag off: the events route answers enabled false and stores nothing, and a
POST /v1/chat/ask body matches the flag-on body. Flag on: events are
{stage, t_ms, status} in ladder order.
An attempt's SQL and row values are not copied into any event.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.ask_stream import (
    bind_ask,
    clear_asks,
    finish_ask,
    mark_cancelled,
    note_rungs,
    note_stage,
    wait_events,
)
from fastapi.testclient import TestClient

_ASK = "stream-stages-ask-01"
_LEAK_RELATION = "zz_secret_relation"
_LEAK_ROW = "zz-leak-row-value"
_ENVELOPE = {
    "answer_id": "ans_stream",
    "text": "no number",
    "badge": "ABSTAIN",
    "abstained": True,
    "assumptions": ["GEN-01: test"],
    "values": [],
    "rows": [],
}


def _events(body: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        out.append(json.loads(line[len("data: ") :]))
    return out


@pytest.fixture(autouse=True)
def _reset_stream() -> None:
    clear_asks()
    yield
    clear_asks()


def _app(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> TestClient:
    if flag is None:
        monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    else:
        monkeypatch.setenv("DMS_ASK_STREAM", flag)
    get_settings.cache_clear()
    app = create_app()
    app.state.cortex = SimpleNamespace(base_url="")
    ask = app.state.ask_service

    def live_ask(*_a: Any, **_k: Any) -> dict[str, Any]:
        note_stage("retrieve_context", "started")
        note_stage("retrieve_context", "done")
        note_stage("schema_context", "done")
        note_stage("insights_generate", "started")
        note_stage("insights_generate", "done")
        note_stage("validate_explain", "done")
        note_rungs(
            [
                {
                    "rung": "rung2",
                    "sql": f"SELECT {_LEAK_ROW} FROM {_LEAK_RELATION}",
                    "rows": [{"sku": _LEAK_ROW}],
                    "prompt": _LEAK_RELATION,
                    "outcome": _LEAK_ROW,
                }
            ]
        )
        note_stage("sql_vote", "done")
        note_stage(_LEAK_RELATION, "done")
        return json.loads(json.dumps(_ENVELOPE))

    ask.live_ask = live_ask  # type: ignore[method-assign]
    ask.offer_reconfirm = lambda env, **_k: env  # type: ignore[method-assign]
    return TestClient(app)


def test_flag_off_events_are_empty_and_ask_bytes_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """UX-TRUST-01. The route answers so the console is not a 404. No stages. Ask bytes match."""
    client = _app(monkeypatch, None)
    paths = client.app.openapi()["paths"]
    assert "/v1/studio/ask/{ask_id}/events" in paths
    first = client.post("/v1/chat/ask", json={"question": "how many"}).content
    got = client.get(f"/v1/studio/ask/{_ASK}/events")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("text/event-stream")
    assert '"enabled":false' in got.text
    assert "stage" not in got.text
    second = client.post("/v1/chat/ask", json={"question": "how many"}).content
    assert first == second


def test_events_in_order_and_envelope_bytes_match(monkeypatch: pytest.MonkeyPatch) -> None:
    off = _app(monkeypatch, None)
    off_body = off.post("/v1/chat/ask", json={"question": "how many"}).content
    on = _app(monkeypatch, "1")
    on_res = on.post(
        "/v1/chat/ask",
        json={"question": "how many"},
        headers={"X-Dms-Ask-Id": _ASK},
    )
    assert on_res.status_code == 200
    assert on_res.content == off_body
    listed = on.get(f"/v1/studio/ask/{_ASK}/events")
    assert listed.status_code == 200
    assert listed.headers["content-type"].startswith("text/event-stream")
    events = _events(listed.text)
    assert [event["stage"] for event in events] == [
        "retrieve",
        "retrieve",
        "plan",
        "generate",
        "generate",
        "validate",
        "retry",
        "vote",
        "reconfirm",
        "reconfirm",
    ]
    assert [event["status"] for event in events] == [
        "started",
        "done",
        "done",
        "started",
        "done",
        "done",
        "rung2",
        "done",
        "started",
        "done",
    ]
    assert events == sorted(events, key=lambda event: event["t_ms"])
    for event in events:
        assert set(event) == {"stage", "t_ms", "status"}


def test_events_do_not_carry_ungranted_names_or_row_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _app(monkeypatch, "1")
    client.post(
        "/v1/chat/ask",
        json={"question": "how many"},
        headers={"X-Dms-Ask-Id": _ASK},
    )
    body = client.get(f"/v1/studio/ask/{_ASK}/events").text
    assert _LEAK_RELATION not in body
    assert _LEAK_ROW not in body


def test_cancel_marks_the_open_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ASK_STREAM", "1")
    token = bind_ask("cancelstage1")
    note_stage("insights_generate", "started")
    mark_cancelled()
    finish_ask(token)
    batch, _done = wait_events("cancelstage1", 0, 0.01)
    assert [(event["stage"], event["status"]) for event in batch] == [
        ("generate", "started"),
        ("generate", "cancelled"),
    ]


def test_flag_off_publisher_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    token = bind_ask(_ASK)
    assert token is None
    note_stage("retrieve_context", "started")
    note_rungs([{"rung": "rung1", "sql": _LEAK_RELATION}])
    finish_ask(token)
    batch, done = wait_events(_ASK, 0, 0.01)
    assert batch == []
    assert done is False


def test_generative_miss_emits_in_order_and_returns_the_same(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A compute miss is None with the flag off and on. On, retrieve then generate."""
    from dms_executor.generative_ask import maybe_generative_ask

    def run() -> object:
        return maybe_generative_ask(
            "how many items",
            warehouse=None,
            grantable=set(),
            compute=lambda _ctx: None,
            submit=lambda _sql: None,
            ledger_append=lambda _payload: None,
            dialect="duckdb",
        )

    monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    off = run()
    monkeypatch.setenv("DMS_ASK_STREAM", "1")
    token = bind_ask("genstream01")
    try:
        on = run()
    finally:
        finish_ask(token)
    assert off is None
    assert on is None
    batch, _done = wait_events("genstream01", 0, 0.01)
    assert [(event["stage"], event["status"]) for event in batch] == [
        ("retrieve", "started"),
        ("retrieve", "done"),
        ("generate", "started"),
        ("generate", "done"),
    ]
