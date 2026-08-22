"""F5 call-through must not go out as an anonymous POST.

S1 (22 Aug): Studio ingest returned 403 on a stack that could answer chat.
``compliance_gate`` opened a bare httpx client against Cortex and posted
``/dms/tasks/gate/check`` with no ``X-API-Key`` and actor defaulting to
``"user"``. Read surfaces send ``settings.cortex_api_key``. A Cortex that
requires the viewer key on F5 therefore refused every mutation as anonymous,
which is indistinguishable from a catalog miss unless we look at the request.

These tests pin the request we send. They do not prove Cortex packed
``studio.ingest`` (P-DMS-4 / gate_task_unknown is Cortex-side).
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


def test_gate_forwards_api_key_and_actor(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, path: str, json: dict[str, Any] | None = None, headers: dict | None = None):
            captured["path"] = path
            captured["json"] = json
            captured["headers"] = headers or {}
            return _FakeResp(200, {"status": "ok", "executable": True})

    monkeypatch.setattr("cortex_client.gate.httpx.Client", _FakeClient)

    class _Client:
        base_url = "http://cortex.test"
        api_key = "dms-demo-viewer-key"

    decision = compliance_gate(
        action="studio.ingest",
        actor="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        metadata={"task_id": "studio.ingest"},
        client=_Client(),
    )

    assert decision.allowed is True
    assert captured["path"] == "/dms/tasks/gate/check"
    assert captured["headers"]["X-API-Key"] == "dms-demo-viewer-key"
    assert captured["json"]["actor"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert captured["json"]["task_id"] == "studio.ingest"


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


def test_studio_ingest_passes_the_seeded_actor(monkeypatch, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def _capture(*, action: str, actor: str | None = None, **_: Any):
        from cortex_client.gate import ComplianceDecision

        seen["action"] = action
        seen["actor"] = actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    import dms_api.routes.studio as studio_routes

    monkeypatch.setattr(studio_routes, "compliance_gate", _capture)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "gate_actor.duckdb"))
    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/ingest",
        files={"file": ("sales.csv", b"sku,qty\nA,1\n", "text/csv")},
    )
    assert r.status_code == 200
    assert seen["action"] == "studio.ingest"
    assert seen["actor"] == get_settings().dms_actor_user_id
    get_settings.cache_clear()
