"""ModelProviderPort is OpenVault FreeRoute. CCA stays unwired. Flag stays 0."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from dms_core.ports import ModelProviderPort
from dms_executor.openvault_model import (
    OpenVaultCompleteFailed,
    OpenVaultModelProvider,
    OpenVaultUnreachable,
    VaultSealed,
    get_model_provider,
)

SOURCE = inspect.getsource(OpenVaultModelProvider)


class _FakeResponse:
    def __init__(self, status_code: int, body: Any, content_type: str = "application/json") -> None:
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": content_type}
        self.text = "" if not isinstance(body, str) else body

    def json(self) -> Any:
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class _FakeClient:
    def __init__(self, routes: dict[str, _FakeResponse]) -> None:
        self.routes = routes
        self.posts: list[tuple[str, dict[str, str] | None, dict[str, Any]]] = []

    def get(self, url: str) -> _FakeResponse:
        return self.routes[url]

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        self.posts.append((url, headers, json or {}))
        return self.routes[url]

    def close(self) -> None:
        return None


def test_factory_satisfies_port() -> None:
    provider = get_model_provider()
    assert isinstance(provider, ModelProviderPort)


def test_sealed_status_does_not_post_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            "http://127.0.0.1:5000/api/vault/status": _FakeResponse(
                200, {"ok": True, "sealed": True}
            )
        }
    )
    monkeypatch.setattr(
        "dms_executor.openvault_model.probe_openvault",
        lambda preferred_url=None: ("http://127.0.0.1:5000", "hint"),
    )
    provider = OpenVaultModelProvider(client=client)  # type: ignore[arg-type]
    with pytest.raises(VaultSealed, match="openvault_vault_sealed"):
        provider.complete("hello")
    assert client.posts == []


def test_unreachable_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dms_executor.openvault_model.probe_openvault",
        lambda preferred_url=None: (None, "start ov"),
    )
    with pytest.raises(OpenVaultUnreachable, match="start ov"):
        OpenVaultModelProvider().complete("hello")


def test_complete_loopback_has_no_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENVAULT_TOKEN", raising=False)
    client = _FakeClient(
        {
            "http://127.0.0.1:5000/api/vault/status": _FakeResponse(
                200, {"ok": True, "sealed": False}
            ),
            "http://127.0.0.1:5000/v1/chat/completions": _FakeResponse(
                200,
                {"choices": [{"message": {"content": "4"}}]},
            ),
        }
    )
    monkeypatch.setattr(
        "dms_executor.openvault_model.probe_openvault",
        lambda preferred_url=None: ("http://127.0.0.1:5000", "hint"),
    )
    text = OpenVaultModelProvider(client=client).complete(  # type: ignore[arg-type]
        "what is 2+2?", system="reply with a number", model="auto"
    )
    assert text == "4"
    url, headers, body = client.posts[0]
    assert url.endswith("/v1/chat/completions")
    assert headers is not None
    assert "Authorization" not in headers
    assert body["model"] == "auto"
    assert body["messages"][0] == {"role": "system", "content": "reply with a number"}
    assert body["messages"][1]["content"] == "what is 2+2?"


def test_403_sealed_type_is_vault_sealed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            "http://127.0.0.1:5000/api/vault/status": _FakeResponse(
                200, {"ok": True, "sealed": False}
            ),
            "http://127.0.0.1:5000/v1/chat/completions": _FakeResponse(
                403,
                {
                    "error": {
                        "message": "vault is sealed",
                        "type": "openvault_vault_sealed",
                    }
                },
            ),
        }
    )
    monkeypatch.setattr(
        "dms_executor.openvault_model.probe_openvault",
        lambda preferred_url=None: ("http://127.0.0.1:5000", "hint"),
    )
    with pytest.raises(VaultSealed, match="openvault_vault_sealed"):
        OpenVaultModelProvider(client=client).complete("hello")  # type: ignore[arg-type]


def test_empty_completion_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            "http://127.0.0.1:5000/api/vault/status": _FakeResponse(
                200, {"ok": True, "sealed": False}
            ),
            "http://127.0.0.1:5000/v1/chat/completions": _FakeResponse(
                200, {"choices": [{"message": {"content": "  "}}]}
            ),
        }
    )
    monkeypatch.setattr(
        "dms_executor.openvault_model.probe_openvault",
        lambda preferred_url=None: ("http://127.0.0.1:5000", "hint"),
    )
    with pytest.raises(OpenVaultCompleteFailed, match="empty"):
        OpenVaultModelProvider(client=client).complete("hello")  # type: ignore[arg-type]


def test_module_does_not_import_cortexos() -> None:
    assert "CortexOS" not in SOURCE
    assert "packs." not in SOURCE
    assert "/api/keys/" not in SOURCE


def test_reuses_httpx_not_a_new_stack() -> None:
    import dms_executor.openvault_model as mod

    assert "import httpx" in inspect.getsource(mod)
