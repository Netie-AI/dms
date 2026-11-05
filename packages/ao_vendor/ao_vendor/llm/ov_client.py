# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenVault FreeRoute client for the ontology role.

Replaces ``auto_ontology.utils.llm_invoke``. DMS does not name a model.
The request body carries the role alias ``ontology`` plus the messages and
the structured-output schema name. Provider keys stay in OpenVault.

Credential: an OpenVault API key (``ov_...``) from ``OPENVAULT_API_KEY``, else
``CORTEX_API_KEY``. This is the same relay ``cortex_client.strict_pin`` uses.
``POST /v1/chat/completions`` verifies the bearer against the issued-key store
(``vault/auth.py resolve_caller``). A DMS signing-service token from
``POST /keys/services`` is not an issued key, so OpenVault answers 401. The
key is minted by OpenVault's human Grant flow. DMS never mints one.
Base URL: ``OPENVAULT_URL``. Nothing here is read at import time.

A failed call is a named refusal, never a quiet ``None``. ``compile_catalog``
turns any recorded failure into :class:`AoCompileRefused` so nothing is stored.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ao_vendor.llm.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

ROLE_ALIAS = "ontology"
CHAT_PATH = "/v1/chat/completions"
API_KEY_ENVS = ("OPENVAULT_API_KEY", "CORTEX_API_KEY")
API_KEY_PREFIX = "ov_"
URL_ENV = "OPENVAULT_URL"

REFUSE_KEY_MISSING = "ao_ov_api_key_missing"
REFUSE_UNAUTHORIZED = "ao_ov_unauthorized"
REFUSE_UNREACHABLE = "ao_ov_unreachable"
REFUSE_OUTPUT_INVALID = "ao_model_output_invalid"
REFUSE_NO_CALLS = "ao_no_model_calls"
#: A failure that no later call can cure. The client stops sending after one.
_FATAL = frozenset(
    {
        REFUSE_KEY_MISSING,
        REFUSE_UNAUTHORIZED,
        REFUSE_UNREACHABLE,
        "openvault_url_missing",
        "model_key_refused",
    }
)

# Keys that would name or pin a model. The body builder rejects them.
_MODEL_KEYS = frozenset(
    {
        "model",
        "model_id",
        "model_name",
        "served_model",
        "served_provider",
        "provider",
        "strict",
    }
)

T = TypeVar("T", bound=BaseModel)
Transport = Callable[[str, dict[str, str], dict[str, Any]], Any]
_BOUND: ContextVar[OpenVaultOntologyClient | None] = ContextVar("ao_ov_client", default=None)


class AoLlmError(RuntimeError):
    """FreeRoute call could not be sent. The message never includes a token."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AoCompileRefused(RuntimeError):
    """The compile cannot be called AI-compiled. Nothing may be stored."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OpenVaultOntologyClient:
    """One FreeRoute caller. ``bodies`` keeps every JSON object that was sent.

    ``failures`` holds the code of every call that did not return a valid
    object, and ``calls_ok`` counts the ones that did. A fatal failure (no key,
    401/403, unreachable) is sticky: later calls raise it without sending.
    """

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport
        self.bodies: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.calls_ok = 0
        self._fatal: str | None = None

    def complete_structured(
        self,
        messages: list[SystemMessage | HumanMessage],
        schema: type[T],
    ) -> T:
        if self._fatal is not None:
            raise AoLlmError(self._fatal)
        try:
            body = build_request_body(messages, schema)
            url = endpoint_url()
            key = load_api_key()
            if not key:
                raise AoLlmError(REFUSE_KEY_MISSING)
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            self.bodies.append(body)
            transport = self._transport or default_transport
            payload = transport(url, headers, body)
            if isinstance(payload, str):
                payload = json.loads(payload)
            parsed = schema.model_validate(payload)
        except AoLlmError as exc:
            self._record(exc.code)
            raise
        except (ValidationError, ValueError):
            self._record(REFUSE_OUTPUT_INVALID)
            raise
        self.calls_ok += 1
        return parsed

    def _record(self, code: str) -> None:
        self.failures.append(code)
        if code in _FATAL:
            self._fatal = code


def bind_client(client: OpenVaultOntologyClient) -> Token[OpenVaultOntologyClient | None]:
    return _BOUND.set(client)


def reset_client(token: Token[OpenVaultOntologyClient | None]) -> None:
    _BOUND.reset(token)


def get_llm_client(**_kwargs: Any) -> OpenVaultOntologyClient:
    """The bound client, or a new one. Keyword args are ignored on purpose.

    Upstream passed ``temperature`` and ``max_tokens`` through to a named
    model. Those are not forwarded. OpenVault picks the hop for ``ontology``.
    """
    current = _BOUND.get()
    if current is not None:
        return current
    return OpenVaultOntologyClient()


def invoke_with_structured_output(
    llm: OpenVaultOntologyClient,
    messages: list[SystemMessage | HumanMessage],
    schema: type[T],
) -> T | None:
    """Return a validated object, or None when the model's output does not parse.

    A call that could not be made or was refused (:class:`AoLlmError`) is
    raised. The client has already recorded it, so a caller that swallows the
    error still cannot hide the failure from ``compile_catalog``.
    """
    schema_name = getattr(schema, "__name__", str(schema))
    try:
        return llm.complete_structured(messages, schema)
    except AoLlmError as exc:
        logger.error("invoke_with_structured_output refused for %s: %s", schema_name, exc.code)
        raise
    except (ValidationError, ValueError) as exc:
        logger.error(
            "invoke_with_structured_output failed for %s: %s",
            schema_name,
            type(exc).__name__,
        )
        return None


def load_api_key() -> str:
    """The ``ov_`` key OpenVault accepts for chat. Empty when none is set.

    A value that is not an ``ov_`` key (the demo viewer key, a Cortex steward
    key, a signing-service token) is skipped: OpenVault would answer 401.
    """
    for name in API_KEY_ENVS:
        raw = os.environ.get(name, "").strip()
        if raw.startswith(API_KEY_PREFIX):
            return raw
    return ""


def endpoint_url() -> str:
    base = os.environ.get(URL_ENV, "").strip().rstrip("/")
    if not base:
        raise AoLlmError("openvault_url_missing")
    return base + CHAT_PATH


def build_request_body(
    messages: list[SystemMessage | HumanMessage],
    schema: type[BaseModel],
) -> dict[str, Any]:
    """JSON body. Role alias ``ontology``. No model id, no provider, no strict."""
    body: dict[str, Any] = {
        "role": ROLE_ALIAS,
        "messages": [{"role": message.role, "content": message.content} for message in messages],
        "schema": schema.__name__,
    }
    _reject_model_keys(body)
    return body


def _reject_model_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _MODEL_KEYS:
                raise AoLlmError("model_key_refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


def default_transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
    """POST the body. Chat-completion envelopes are unwrapped to the JSON object."""
    import httpx

    try:
        response = httpx.post(url, headers=headers, json=body, timeout=60.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise AoLlmError(REFUSE_UNAUTHORIZED) from None
        raise AoLlmError(f"ao_ov_http_{status}") from None
    except httpx.HTTPError:
        raise AoLlmError(REFUSE_UNREACHABLE) from None
    data = response.json()
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return json.loads(content)
        if isinstance(content, dict):
            return content
    return data
