"""OpenVault FreeRoute as the ModelProviderPort (swap: OpenVault -> Azure OpenAI).

Loopback chat carries no Bearer. A dummy Authorization 401s on :5000.
Sealed vault is a typed failure, never a chat POST: decrypt-fail hops 500'd
the old worktree. Canonical finding: 2026-09-05_openvault-chat-500-decrypt.

Does not import CortexOS. Does not read /api/keys/{id}/secret. Does not
wire CCA or flip DMS_CCA_CASCADE.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from dms_core.ports import ModelProviderPort

from dms_executor.openvault_discovery import probe_openvault

_DUMMY_KEYS = frozenset({"", "empty", "none", "null", "changeme", "openvault-loopback"})
_SEALED_TYPE = "openvault_vault_sealed"
_DEFAULT_TIMEOUT = 45.0
_DEFAULT_MAX_TOKENS = 256
_DEFAULT_MODEL = "auto"


class VaultSealed(RuntimeError):
    """Master key is not in memory. Founder unseals; agents do not POST chat."""


class OpenVaultUnreachable(RuntimeError):
    """healthz did not answer. Start command is on the exception message."""


class OpenVaultCompleteFailed(RuntimeError):
    """FreeRoute answered, but not with usable text."""


def _token() -> str:
    raw = (os.environ.get("OPENVAULT_TOKEN") or "").strip()
    if not raw or raw.lower() in _DUMMY_KEYS:
        return ""
    return raw


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _content_from_body(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        text = message.get("content")
        if isinstance(text, str):
            return text
    text = first.get("text")
    return text if isinstance(text, str) else ""


def _error_type(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    err = body.get("error")
    if isinstance(err, dict):
        typed = err.get("type")
        if isinstance(typed, str):
            return typed
    typed = body.get("type")
    return typed if isinstance(typed, str) else ""


class OpenVaultModelProvider:
    """POST {base}/v1/chat/completions. base is the OpenVault origin, not .../v1."""

    def __init__(
        self,
        *,
        preferred_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self._preferred_url = preferred_url
        self._timeout = timeout
        self._client = client

    def complete(self, prompt: str, **kwargs: Any) -> str:
        base, hint = probe_openvault(preferred_url=self._preferred_url)
        if not base:
            raise OpenVaultUnreachable(hint)
        http = self._client or httpx.Client(timeout=self._timeout)
        owns = self._client is None
        try:
            status = http.get(f"{base}/api/vault/status")
            try:
                payload = status.json()
            except ValueError:
                payload = {}
            if isinstance(payload, dict) and payload.get("sealed") is True:
                raise VaultSealed("openvault_vault_sealed")
            messages: list[dict[str, str]] = []
            system = kwargs.get("system")
            if isinstance(system, str) and system.strip():
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            model = kwargs.get("model")
            if not isinstance(model, str) or not model.strip():
                model = _DEFAULT_MODEL
            max_tokens = kwargs.get("max_tokens", _DEFAULT_MAX_TOKENS)
            try:
                max_tokens = int(max_tokens)
            except (TypeError, ValueError):
                max_tokens = _DEFAULT_MAX_TOKENS
            resp = http.post(
                f"{base}/v1/chat/completions",
                headers=_headers(),
                json={
                    "model": model.strip(),
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
            )
            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = {"error": {"message": resp.text[:300]}}
            if _error_type(body) == _SEALED_TYPE or resp.status_code == 403:
                raise VaultSealed(_SEALED_TYPE)
            if resp.status_code >= 400:
                raise OpenVaultCompleteFailed(f"http {resp.status_code}")
            text = _content_from_body(body).strip()
            if not text:
                raise OpenVaultCompleteFailed("empty completion")
            return text
        finally:
            if owns:
                http.close()


def get_model_provider() -> ModelProviderPort:
    return OpenVaultModelProvider()
