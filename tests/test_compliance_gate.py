"""F5 call-through must not go out as an anonymous POST.

S1 (22 Aug): Studio ingest returned 403 on a stack that could answer chat.
``compliance_gate`` opened a bare httpx client against Cortex and posted
``/dms/tasks/gate/check`` with no ``X-API-Key`` and actor defaulting to
``"user"``. Read surfaces send ``settings.cortex_api_key``. A Cortex that
requires the viewer key on F5 therefore refused every mutation as anonymous,
which is indistinguishable from a catalog miss unless we look at the request.

These tests pin the request we send. They do not prove Cortex packed
``studio.ingest`` (P-DMS-4 / gate_task_unknown is Cortex-side).
Recovered from the abandoned branch ``cursor/ff02-polarity-acl-ingest-3ebf``
(never opened as a PR). Two of its four tests now hold: F5 forwards
``X-API-Key`` from the Cortex client, and Studio ingest names
``settings.dms_actor_user_id`` (DR-0004 Option A - identity from config).
"""

from __future__ import annotations

from typing import Any

from cortex_client.gate import compliance_gate
from dms_api.app import create_app
from dms_api.settings import get_settings
from fastapi.testclient import TestClient


class _FakeResp:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.content = b"{}" if body else b""

    def json(self) -> dict[str, Any]:
        return self._body


def test_gate_without_api_key_stays_anonymous(monkeypatch) -> None:
    """A client that has no key must not invent one - fail closed, not spoof."""
    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, path: str, json: dict[str, Any] | None = None, headers: dict | None = None):
            captured["headers"] = headers or {}
            return _FakeResp(401)

    monkeypatch.setattr("cortex_client.gate.httpx.Client", _FakeClient)

    class _Client:
        base_url = "http://cortex.test"
        api_key = None

    decision = compliance_gate(action="studio.ingest", client=_Client())
    assert decision.allowed is False
    assert decision.reason == "gate_refused"
    assert "X-API-Key" not in captured["headers"]


def test_gate_forwards_api_key_and_actor(monkeypatch) -> None:
    """Read surfaces send CORTEX_API_KEY. F5 must too, or mutations look anonymous."""
    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, path: str, json: dict[str, Any] | None = None, headers: dict | None = None):
            captured["headers"] = headers or {}
            captured["json"] = json or {}
            return _FakeResp(200, {"status": "ok", "executable": True})

    monkeypatch.setattr("cortex_client.gate.httpx.Client", _FakeClient)

    class _Client:
        base_url = "http://cortex.test"
        api_key = "k-from-config"

    actor = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    decision = compliance_gate(
        action="studio.ingest",
        actor=actor,
        client=_Client(),
    )
    assert decision.allowed is True
    assert captured["headers"]["X-API-Key"] == "k-from-config"
    assert captured["json"]["actor"] == actor


def test_studio_ingest_passes_the_seeded_actor(monkeypatch) -> None:
    """DR-0004 Option A: Studio ingest names the configured steward, never a request field."""
    from cortex_client.gate import ComplianceDecision

    seen: dict[str, Any] = {}

    def _allow(*, action: str, actor: str | None = None, **kwargs: Any) -> ComplianceDecision:
        seen["action"] = action
        seen["actor"] = actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.studio.compliance_gate", _allow)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        files={"file": ("sales.csv", b"sku,qty\nA,1\n", "text/csv")},
    )
    assert r.status_code == 200, r.text
    settings = get_settings()
    assert seen["action"] == "studio.ingest"
    assert seen["actor"] == settings.dms_actor_user_id
    get_settings.cache_clear()


def test_studio_ingest_without_cortex_is_403_gate_unavailable(monkeypatch) -> None:
    """S1 root cause when Cortex is down: fail-closed, not a silent 200.

    This is the honest 403. It is not sellable. The fix for a local write is
    to start Cortex, not to skip compliance (gatekeeping.py).
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        files={"file": ("sales.csv", b"sku,qty\nA,1\n", "text/csv")},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "gate_unavailable"
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Adversary round 0 (workflow-api, P1): every answer the gate cannot read is a refusal.
# The gate is shared by every FastAPI mutation route; any of these escaping as an exception
# was a bare 500 on each of them instead of a 403 gate_unavailable.


class _Client:
    base_url = "http://cortex.test"
    api_key = None


def test_gate_200_with_a_json_list_is_gate_unavailable_not_an_exception(monkeypatch) -> None:
    class _ListClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def __enter__(self) -> _ListClient:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, path: str, json: Any = None, headers: Any = None) -> Any:
            return _ListResp()

    class _ListResp:
        status_code = 200
        content = b"[1, 2]"

        def json(self) -> Any:
            return [1, 2]

    monkeypatch.setattr("cortex_client.gate.httpx.Client", _ListClient)
    decision = compliance_gate(action="studio.ingest", client=_Client())
    assert decision.allowed is False
    assert decision.reason == "gate_unavailable"


def test_gate_200_that_is_not_json_is_gate_unavailable_not_an_exception(monkeypatch) -> None:
    class _TextClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def __enter__(self) -> _TextClient:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, path: str, json: Any = None, headers: Any = None) -> Any:
            return _TextResp()

    class _TextResp:
        status_code = 200
        content = b"<html>proxy</html>"

        def json(self) -> Any:
            raise ValueError("not json")

    monkeypatch.setattr("cortex_client.gate.httpx.Client", _TextClient)
    decision = compliance_gate(action="studio.ingest", client=_Client())
    assert decision.allowed is False
    assert decision.reason == "gate_unavailable"


def test_gate_base_url_httpx_rejects_is_gate_unavailable_not_an_exception() -> None:
    """``http://[::1`` is an unparseable host: httpx raises InvalidURL, which is not an
    httpx.HTTPError, so an ``except httpx.HTTPError`` lets it out as a bare 500."""

    class _BadUrlClient:
        base_url = "http://[::1"
        api_key = None

    decision = compliance_gate(action="studio.ingest", client=_BadUrlClient())
    assert decision.allowed is False
    assert decision.reason == "gate_unavailable"


def test_gate_object_answer_still_decides(monkeypatch) -> None:
    """Control: a JSON object still gets its decision (the new checks only refuse non-answers)."""

    class _OkClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def __enter__(self) -> _OkClient:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, path: str, json: Any = None, headers: Any = None) -> _FakeResp:
            return _FakeResp(200, {"status": "ok", "executable": True})

    monkeypatch.setattr("cortex_client.gate.httpx.Client", _OkClient)
    decision = compliance_gate(action="studio.ingest", client=_Client())
    assert decision.allowed is True
