"""GET /v1/features is always mounted. Each flag is on only for the digit 1."""

from dms_api.app import create_app
from dms_api.settings import get_settings
from fastapi.testclient import TestClient

_FLAGS = ("DMS_READY_GUIDE", "DMS_SIGN", "DMS_INSIGHT_PROOF")


def _body(monkeypatch, **env: str) -> dict[str, bool]:
    for name in _FLAGS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        res = TestClient(create_app()).get("/v1/features")
    finally:
        get_settings.cache_clear()
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, dict)
    return body


def test_features_default_off(monkeypatch) -> None:
    assert _body(monkeypatch) == {
        "ready_guide": False,
        "sign": False,
        "insight_proof": False,
    }


def test_features_reject_words(monkeypatch) -> None:
    assert _body(
        monkeypatch,
        DMS_READY_GUIDE="true",
        DMS_SIGN="yes",
        DMS_INSIGHT_PROOF="on",
    ) == {
        "ready_guide": False,
        "sign": False,
        "insight_proof": False,
    }


def test_features_digit_one(monkeypatch) -> None:
    assert _body(
        monkeypatch,
        DMS_READY_GUIDE="1",
        DMS_SIGN="1",
        DMS_INSIGHT_PROOF="1",
    ) == {
        "ready_guide": True,
        "sign": True,
        "insight_proof": True,
    }
