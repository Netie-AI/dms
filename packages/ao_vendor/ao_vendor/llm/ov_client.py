# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenVault FreeRoute client for the ontology role.

Replaces ``auto_ontology.utils.llm_invoke``. DMS does not name a model.
The request body carries the role alias ``ontology`` plus the messages and
the structured-output schema name. Provider keys stay in OpenVault.

Token: ``DMS_OV_SERVICE_TOKEN``, else the file at ``DMS_OV_SERVICE_TOKEN_FILE``.
Base URL: ``OPENVAULT_URL``. Nothing here is read at import time.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ao_vendor.llm.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

ROLE_ALIAS = "ontology"
CHAT_PATH = "/v1/chat/completions"
TOKEN_ENV = "DMS_OV_SERVICE_TOKEN"
TOKEN_FILE_ENV = "DMS_OV_SERVICE_TOKEN_FILE"
URL_ENV = "OPENVAULT_URL"

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


class OpenVaultOntologyClient:
    """One FreeRoute caller. ``bodies`` keeps every JSON object that was sent."""

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport
        self.bodies: list[dict[str, Any]] = []

    def complete_structured(
        self,
        messages: list[SystemMessage | HumanMessage],
        schema: type[T],
    ) -> T:
        body = build_request_body(messages, schema)
        self.bodies.append(body)
        url = endpoint_url()
        token = load_service_token()
        if not token:
            raise AoLlmError("ov_service_token_missing")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        transport = self._transport or default_transport
        payload = transport(url, headers, body)
        if isinstance(payload, str):
            payload = json.loads(payload)
        return schema.model_validate(payload)


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
    """Return a validated object, or None when the call or the parse fails."""
    schema_name = getattr(schema, "__name__", str(schema))
    try:
        return llm.complete_structured(messages, schema)
    except (AoLlmError, ValidationError, ValueError) as exc:
        logger.error(
            "invoke_with_structured_output failed for %s: %s",
            schema_name,
            type(exc).__name__,
        )
        return None
    except Exception:
        logger.exception("invoke_with_structured_output failed for %s", schema_name)
        return None


def load_service_token() -> str:
    """Bearer from the env, else from the token file. Empty when neither is set."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    raw = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


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

    response = httpx.post(url, headers=headers, json=body, timeout=60.0)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return json.loads(content)
        if isinstance(content, dict):
            return content
    return data
