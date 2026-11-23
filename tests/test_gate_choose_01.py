"""F5 call-through goes through choose, which records the event before it decides.

The gate used to post ``/dms/tasks/gate/check`` with a random event id. Cortex's
check finds no such event, updates zero rows, and still returns ``pass``, so a
mutation was allowed with nothing recorded. Choose creates the event and returns
the verdict in one call. These tests pin that request and the decision it yields.
"""

from __future__ import annotations

from typing import Any

import httpx
from cortex_client.gate import compliance_gate


class _FakeResp:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.content = b"{}" if body else b""

    def json(self) -> dict[str, Any]:
        return self._body


def _choose_body(event_id: str, status: str, executable: bool) -> _FakeResp:
    verdict = {"status": status, "executable": executable}
    return _FakeResp(200, {"ok": True, "event_id": event_id, "verdict": verdict})


def _fake_cortex(monkeypatch, response: _FakeResp) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, path: str, json: dict[str, Any] | None = None, headers: dict | None = None):
            captured["path"] = path
            captured["json"] = json or {}
            return response

    monkeypatch.setattr("cortex_client.gate.httpx.Client", _FakeClient)
    return captured


class _Client:
    base_url = "http://cortex.test"
    api_key = None


def test_gate_posts_to_choose_without_accepting_a_suggestion(monkeypatch) -> None:
    captured = _fake_cortex(monkeypatch, _choose_body("ev-1", "pass", True))
    compliance_gate(
        action="space.create",
        actor="steward",
        metadata={"task_id": "space.create", "name": "x"},
        client=_Client(),
    )
    assert captured["path"] == "/dms/tasks/choose"
    assert captured["json"]["task_id"] == "space.create"
    assert captured["json"]["accepted"] is False
    assert captured["json"]["actor"] == "steward"


def test_a_recorded_pass_allows_and_returns_the_event(monkeypatch) -> None:
    _fake_cortex(monkeypatch, _choose_body("ev-2", "pass", True))
    decision = compliance_gate(action="space.create", client=_Client())
    assert decision.allowed is True
    assert decision.reason == "pass"
    assert decision.event_id == "ev-2"


def test_a_recorded_fail_refuses_with_its_reason(monkeypatch) -> None:
    _fake_cortex(monkeypatch, _choose_body("ev-3", "fail", False))
    decision = compliance_gate(action="space.create", client=_Client())
    assert decision.allowed is False
    assert decision.reason == "fail"
    assert decision.event_id == "ev-3"


def test_an_unknown_task_still_fails_closed(monkeypatch) -> None:
    _fake_cortex(monkeypatch, _FakeResp(404, {"detail": "unknown task"}))
    decision = compliance_gate(action="space.create", client=_Client())
    assert decision.allowed is False
    assert decision.reason == "gate_task_unknown"
    assert decision.event_id is None


def test_an_unreachable_cortex_fails_closed_without_inventing_an_event(monkeypatch) -> None:
    class _Refused:
        def __enter__(self) -> _Refused:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, *args: Any, **kwargs: Any):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr("cortex_client.gate.httpx.Client", lambda *a, **k: _Refused())
    decision = compliance_gate(action="space.create", client=_Client())
    assert decision.allowed is False
    assert decision.reason == "gate_unavailable"
    assert decision.event_id is None
