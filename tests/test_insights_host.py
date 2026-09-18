"""INSIGHTS-HOST-01 — DMS consumes Cortex GET|POST /v1/insights via the configured key.

Mocks Cortex/OV. Does not invent LIVE_KEY, :5000 green, or hosted PASS.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.insights import (
    InsightsError,
    insights_get,
    insights_post,
    redact_secrets,
)
from dms_api.app import create_app
from fastapi.testclient import TestClient

_OV = "ov_test_host_01"
_CERTIFIED = {
    "ok": True,
    "status": "CERTIFIED",
    "values": [{"sku_count": 12}],
    "answer": "There are 12 skus.",
    "api": {"consumer": "dms", "stable": "POST /v1/insights", "live_5000_ci": True},
    "live_5000_ci": True,
}
_LAW = {
    "statuses": ["CERTIFIED", "ABSTAIN", "REFUSE"],
    "stable": "POST /v1/insights",
    "live_5000_ci": False,
    "cot_climb": {"complete": False, "status": "INCOMPLETE"},
}
_KEYS = {
    "ok": True,
    "custody": "openvault",
    "second_vault": False,
    "callers_hold_provider_keys": False,
    "live_key_rotate": False,
    "live_5000_ci": False,
    "token_returned": False,
    "note": "Callers present ov_ or loopback; they do not present raw provider keys.",
}


class _Resp:
    def __init__(
        self,
        status_code: int,
        body: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _CaptureClient:
    last: dict[str, Any] = {}

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def __enter__(self) -> _CaptureClient:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _Resp:
        _CaptureClient.last = {
            "method": method,
            "url": url,
            "headers": headers or {},
            "json": json,
            "params": params,
        }
        return _Resp(200, dict(_CERTIFIED) if method == "POST" else dict(_LAW))


class FakeInsights:
    base_url = "http://cortex.test"
    api_key = _OV

    def __init__(self) -> None:
        self.asks: list[dict[str, Any]] = []
        self.law_error: Exception | None = None
        self.ask_error: Exception | None = None
        self.ask_payload: dict[str, Any] = dict(_CERTIFIED)

    def insights_law(self) -> dict[str, Any]:
        if self.law_error:
            raise self.law_error
        return dict(_LAW)

    def insights_keys(self) -> dict[str, Any]:
        return dict(_KEYS)

    def insights_identity(self) -> dict[str, Any]:
        return {
            "identity": "cortex:crew",
            "token_returned": False,
            "custody": "openvault",
            "live_5000_ci": False,
        }

    def insights_ontology(self, q: str) -> dict[str, Any]:
        return {"phase": "ontology", "intent": q, "values": None, "live_5000_ci": False}

    def insights_ask(self, **kwargs: Any) -> dict[str, Any]:
        self.asks.append(kwargs)
        if self.ask_error:
            raise self.ask_error
        return dict(self.ask_payload)


def _allow(**kwargs: Any) -> ComplianceDecision:
    return ComplianceDecision(
        allowed=True, reason="ok", action=str(kwargs.get("action") or "insights.ask")
    )


def _app(fake: FakeInsights | None = None) -> TestClient:
    app = create_app()
    app.state.cortex = fake
    return TestClient(app)


def test_redact_strips_ov_and_live_key_not_docs() -> None:
    raw = f"Authorization: Bearer {_OV} LIVE_KEY pasted gsk_notareallivekeyxx"
    out = redact_secrets(raw, extra=_OV)
    assert _OV not in out
    assert "LIVE_KEY" not in out
    assert "gsk_" not in out
    assert "[redacted]" in out


def test_http_forwards_configured_ov_key_and_does_not_invent() -> None:
    _CaptureClient.last = {}
    with patch("cortex_client.insights.httpx.Client", _CaptureClient):
        body = insights_post("http://127.0.0.1:8010", intent="how many skus", api_key=_OV)
    seen = _CaptureClient.last
    assert seen["url"].endswith("/v1/insights")
    assert seen["headers"]["Authorization"] == f"Bearer {_OV}"
    assert seen["headers"]["X-API-Key"] == _OV
    assert seen["json"]["consumer"] == "dms"
    assert "api_key" not in seen["json"]
    assert body["status"] == "CERTIFIED"
    assert body["values"] == [{"sku_count": 12}]
    assert body["live_5000_ci"] is False
    assert body["api"]["live_5000_ci"] is False


def test_http_does_not_invent_a_key_when_unset() -> None:
    _CaptureClient.last = {}
    with patch("cortex_client.insights.httpx.Client", _CaptureClient):
        insights_get("http://127.0.0.1:8010", api_key=None)
    headers = _CaptureClient.last["headers"]
    assert headers == {}
    assert "Authorization" not in headers
    assert "X-API-Key" not in headers


def test_http_unreachable_fails_closed_no_values() -> None:
    import httpx as _httpx

    class _BoomClient(_CaptureClient):
        def request(self, *a: Any, **k: Any) -> _Resp:
            raise _httpx.ConnectError("down")

    with patch("cortex_client.insights.httpx.Client", _BoomClient):
        with pytest.raises(InsightsError) as caught:
            insights_post("http://127.0.0.1:8010", intent="how many skus", api_key=_OV)
    exc = caught.value
    assert exc.status_code == 503
    assert exc.payload is None
    assert _OV not in str(exc)
    assert "LIVE_KEY" not in str(exc)


def test_get_law_503_when_cortex_missing() -> None:
    res = _app(None).get("/v1/insights")
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert body["live_5000_ci"] is False
    assert body["code"] == "cortex_unavailable"


def test_post_without_cortex_fails_closed_even_with_demo_fallback(monkeypatch: Any) -> None:
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "1")
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    try:
        res = _app(None).post("/v1/insights", json={"intent": "how many skus"})
    finally:
        monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
        get_settings.cache_clear()
    assert res.status_code == 503
    body = res.json()
    assert body["values"] == []
    assert body["status"] == "REFUSE"
    assert "12" not in (body.get("answer") or body.get("message") or "")


def test_post_returns_cortex_envelope_and_clears_live_5000(monkeypatch: Any) -> None:
    monkeypatch.setattr("dms_api.routes.insights.compliance_gate", _allow)
    fake = FakeInsights()
    res = _app(fake).post("/v1/insights", json={"intent": "how many skus", "ask": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "CERTIFIED"
    assert body["values"] == [{"sku_count": 12}]
    assert "12" in body["answer"]
    assert body["live_5000_ci"] is False
    assert body["api"]["live_5000_ci"] is False
    assert fake.asks[0]["consumer"] == "dms"
    raw = res.text
    assert "LIVE_KEY" not in raw
    assert _OV not in raw
    assert "gsk_" not in raw


def test_get_law_and_keys_are_honest(monkeypatch: Any) -> None:
    fake = FakeInsights()
    law = _app(fake).get("/v1/insights")
    assert law.status_code == 200, law.text
    body = law.json()
    assert body["statuses"] == ["CERTIFIED", "ABSTAIN", "REFUSE"]
    assert body["live_5000_ci"] is False
    keys = _app(fake).get("/v1/insights/keys")
    assert keys.status_code == 200, keys.text
    pose = keys.json()
    assert pose["custody"] == "openvault"
    assert pose["second_vault"] is False
    assert pose["token_returned"] is False
    assert pose["live_key_rotate"] is False
    assert pose["live_5000_ci"] is False
    raw = keys.text
    assert "LIVE_KEY" not in raw
    assert _OV not in raw


def test_empty_intent_is_400(monkeypatch: Any) -> None:
    monkeypatch.setattr("dms_api.routes.insights.compliance_gate", _allow)
    res = _app(FakeInsights()).post("/v1/insights", json={"intent": "  "})
    assert res.status_code == 400


def test_generate_fail_closed_when_ov_down(monkeypatch: Any) -> None:
    monkeypatch.setattr("dms_api.routes.insights.compliance_gate", _allow)
    monkeypatch.setattr("dms_api.routes.insights.openvault_reachable", lambda *_a, **_k: False)
    fake = FakeInsights()
    res = _app(fake).post(
        "/v1/insights",
        json={"intent": "how many skus", "ask": False, "generate": True},
    )
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert body["code"] == "openvault_unavailable"
    assert body["live_5000_ci"] is False
    assert fake.asks == []
    assert _OV not in res.text


def test_generate_forwards_when_ov_up(monkeypatch: Any) -> None:
    monkeypatch.setattr("dms_api.routes.insights.compliance_gate", _allow)
    monkeypatch.setattr("dms_api.routes.insights.openvault_reachable", lambda *_a, **_k: True)
    fake = FakeInsights()
    fake.ask_payload = {
        "status": "REFUSE",
        "values": [],
        "answer": "FreeRoute unarmed",
        "api": {"live_5000_ci": False},
    }
    res = _app(fake).post(
        "/v1/insights",
        json={"intent": "how many skus", "ask": False, "generate": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert fake.asks[0]["generate"] is True


def test_cortex_401_passthrough_is_real_refuse(monkeypatch: Any) -> None:
    monkeypatch.setattr("dms_api.routes.insights.compliance_gate", _allow)
    fake = FakeInsights()
    fake.ask_error = InsightsError(
        "needs its own OpenVault ov_ key",
        status_code=401,
        payload={
            "ok": False,
            "status": "REFUSE",
            "values": [],
            "refused": "FreeRoute spend from a non-loopback caller needs its own OpenVault ov_ key",
            "live_5000_ci": False,
        },
        code="insights_refused",
    )
    res = _app(fake).post("/v1/insights", json={"intent": "how many skus", "generate": False})
    assert res.status_code == 401
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert _OV not in res.text


def test_post_calls_compliance_gate(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def _gate(**kwargs: Any) -> ComplianceDecision:
        seen.update(kwargs)
        return ComplianceDecision(allowed=True, reason="ok", action="insights.ask")

    monkeypatch.setattr("dms_api.routes.insights.compliance_gate", _gate)
    res = _app(FakeInsights()).post("/v1/insights", json={"question": "how many skus"})
    assert res.status_code == 200, res.text
    assert seen["action"] == "insights.ask"
