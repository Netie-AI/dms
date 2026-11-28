# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenVault FreeRoute client for the ontology role.

Replaces ``auto_ontology.utils.llm_invoke``. DMS does not name a model.
The request body carries the role alias ``ontology`` plus the messages and
the structured-output schema name. Provider keys stay in OpenVault.
``DMS_AO_RESPONSE_FORMAT=1`` also sends ``response_format`` built from the
pydantic class, and allows one fenced-JSON parse retry. Any other value,
including unset, leaves the body and the ``json.loads`` path unchanged.

Credential: an OpenVault API key (``ov_...``) from ``OPENVAULT_API_KEY``, else
``CORTEX_API_KEY``. This is the same relay ``cortex_client.strict_pin`` uses.
``POST /v1/chat/completions`` verifies the bearer against the issued-key store
(``vault/auth.py resolve_caller``). A DMS signing-service token from
``POST /keys/services`` is not an issued key, so OpenVault answers 401. The
key is minted by OpenVault's human Grant flow. DMS never mints one.
Base URL: ``OPENVAULT_URL``. Nothing here is read at import time.

A failed call is a named refusal, never a quiet ``None``. With
``DMS_AO_COMPILE_RESILIENT`` off (the default), ``compile_catalog`` turns any
recorded failure into :class:`AoCompileRefused` so nothing is stored. On, a
429 or other transient error is retried, a validation error is sent back
once, and a caller deadline replaces the fixed client timeout.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from email.utils import parsedate_to_datetime
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
REFUSE_TIMEOUT = "ao_ov_timeout"
REFUSE_OUTPUT_INVALID = "ao_model_output_invalid"
REFUSE_NO_CALLS = "ao_no_model_calls"
#: ``1`` adds ``response_format`` and one fenced-JSON retry. Anything else is off.
RESPONSE_FORMAT_FLAG = "DMS_AO_RESPONSE_FORMAT"
#: ``1`` retries transient calls, repairs one validation miss, and uses the
#: caller deadline instead of a fixed timeout. Anything else is off.
RESILIENT_FLAG = "DMS_AO_COMPILE_RESILIENT"
# ponytail: three sends, then the table is a gap. Upgrade: a caller budget.
_TRANSPORT_TRIES = 3
_RETRYABLE = frozenset(
    {
        "ao_ov_http_408",
        "ao_ov_http_429",
        "ao_ov_http_502",
        "ao_ov_http_503",
        "ao_ov_http_504",
        REFUSE_TIMEOUT,
        REFUSE_UNREACHABLE,
    }
)
_FENCE = "```"
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
#: Monotonic deadline the caller passed. None means the caller set no cap.
_DEADLINE: ContextVar[float | None] = ContextVar("ao_compile_deadline", default=None)


class AoLlmError(RuntimeError):
    """FreeRoute call could not be sent. The message never includes a token."""

    def __init__(self, code: str, *, retry_after: float | None = None) -> None:
        self.code = code
        self.retry_after = retry_after
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
    401/403, and, when the resilient flag is off, unreachable) is sticky:
    later calls raise it without sending.
    """

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport
        self.bodies: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.failure_steps: list[str] = []
        self.failure_fields: list[tuple[str, ...]] = []
        self.invalid_fields: list[str] = []
        self.calls_ok = 0
        self._fatal: str | None = None

    def complete_structured(
        self,
        messages: list[SystemMessage | HumanMessage],
        schema: type[T],
    ) -> T:
        if resilient_enabled():
            return self._complete_resilient(messages, schema)
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
                payload = _loads_model_json(payload)
            parsed = schema.model_validate(payload)
        except AoLlmError as exc:
            self._record(exc.code)
            raise
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                _log_validation(schema.__name__, _field_paths(exc))
            self._record(REFUSE_OUTPUT_INVALID)
            raise
        self.calls_ok += 1
        return parsed

    def _complete_resilient(
        self,
        messages: list[SystemMessage | HumanMessage],
        schema: type[T],
    ) -> T:
        """Retry transport errors, then send a validation miss back once."""
        if self._fatal is not None:
            raise AoLlmError(self._fatal)
        payload = self._send_with_retry(messages, schema)
        try:
            parsed = _coerce(schema, payload)
        except ValidationError as exc:
            fields = _field_paths(exc)
            _log_validation(schema.__name__, fields)
            self.invalid_fields = fields
            follow = [*messages, HumanMessage(content=_repair_text(fields))]
            payload2 = self._send_with_retry(follow, schema)
            try:
                parsed = _coerce(schema, payload2)
            except ValidationError as exc2:
                fields2 = _field_paths(exc2)
                _log_validation(schema.__name__, fields2)
                self.invalid_fields = fields2
                self._record(
                    REFUSE_OUTPUT_INVALID,
                    step=schema.__name__,
                    fields=tuple(fields2),
                )
                raise
            except ValueError:
                self._record(REFUSE_OUTPUT_INVALID, step=schema.__name__)
                raise
        except ValueError:
            self._record(REFUSE_OUTPUT_INVALID, step=schema.__name__)
            raise
        self.calls_ok += 1
        return parsed

    def _send_with_retry(
        self,
        messages: list[SystemMessage | HumanMessage],
        schema: type[BaseModel],
    ) -> Any:
        """Send once, and again on 429 or a transient error, up to the cap."""
        step = schema.__name__
        last: AoLlmError | None = None
        for attempt in range(_TRANSPORT_TRIES):
            try:
                _ensure_deadline()
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
                _ensure_deadline()
                return payload
            except AoLlmError as exc:
                last = exc
                retry = exc.code in _RETRYABLE and attempt + 1 < _TRANSPORT_TRIES
                if retry and exc.code == REFUSE_TIMEOUT and not _deadline_open():
                    retry = False
                if not retry:
                    self._record(exc.code, step=step)
                    raise
                _pause(_wait_seconds(attempt, exc.retry_after))
        if last is not None:
            self._record(last.code, step=step)
            raise last
        raise AoLlmError(REFUSE_UNREACHABLE)

    def _record(
        self,
        code: str,
        *,
        step: str = "",
        fields: tuple[str, ...] = (),
    ) -> None:
        self.failures.append(code)
        self.failure_steps.append(step)
        self.failure_fields.append(fields)
        if fields:
            self.invalid_fields = list(fields)
        sticky = code in _FATAL
        if resilient_enabled() and code in _RETRYABLE:
            sticky = False
        if sticky:
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


def response_format_enabled() -> bool:
    """True only when the flag is the digit 1. Unset and every other value are off."""
    return os.environ.get(RESPONSE_FORMAT_FLAG, "0").strip() == "1"


def resilient_enabled() -> bool:
    """True only when the flag is the digit 1. Unset and every other value are off."""
    return os.environ.get(RESILIENT_FLAG, "0").strip() == "1"


def bind_deadline(deadline: float | None) -> Token[float | None]:
    """Install the caller deadline. ``None`` means the caller set no cap."""
    return _DEADLINE.set(deadline)


def reset_deadline(token: Token[float | None]) -> None:
    _DEADLINE.reset(token)


def build_request_body(
    messages: list[SystemMessage | HumanMessage],
    schema: type[BaseModel],
) -> dict[str, Any]:
    """JSON body. Role alias ``ontology``. No model id, no provider, no strict.

    Flag off: ``role``, ``messages``, and the schema class name. Flag on: the
    same object plus ``response_format`` from ``schema.model_json_schema()``.
    """
    body: dict[str, Any] = {
        "role": ROLE_ALIAS,
        "messages": [{"role": message.role, "content": message.content} for message in messages],
        "schema": schema.__name__,
    }
    if response_format_enabled():
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
            },
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
    """POST the body. Chat-completion envelopes are unwrapped to the JSON object.

    Flag off keeps the fixed 60 second cap, and a timeout stays
    ``ao_ov_unreachable``. Flag on uses the deadline the caller passed
    (no fixed cap) and a timeout is ``ao_ov_timeout``.
    """
    import httpx

    timeout: float | None = 60.0
    if resilient_enabled():
        timeout = _httpx_timeout()
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise AoLlmError(REFUSE_UNAUTHORIZED) from None
        if resilient_enabled():
            raise AoLlmError(
                f"ao_ov_http_{status}",
                retry_after=_retry_after_seconds(exc.response),
            ) from None
        raise AoLlmError(f"ao_ov_http_{status}") from None
    except httpx.TimeoutException:
        if resilient_enabled():
            raise AoLlmError(REFUSE_TIMEOUT) from None
        raise AoLlmError(REFUSE_UNREACHABLE) from None
    except httpx.HTTPError:
        raise AoLlmError(REFUSE_UNREACHABLE) from None
    data = response.json()
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return _loads_model_json(content)
        if isinstance(content, dict):
            return content
    return data


def _loads_model_json(content: str) -> Any:
    """Parse a model string. Flag on: one retry after a single ``` fence.

    Flag off re-raises the first ``json.loads`` error, so the refusal is the
    one this client has always raised. The retry uses string splits only.
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        if not response_format_enabled():
            raise
        stripped = _strip_one_fence(content)
        if stripped == content:
            raise
        return json.loads(stripped)


def _httpx_timeout() -> float | None:
    """Seconds left on the caller deadline. None when the caller set no cap."""
    deadline = _DEADLINE.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AoLlmError(REFUSE_TIMEOUT)
    return remaining


def _deadline_open() -> bool:
    deadline = _DEADLINE.get()
    if deadline is None:
        return True
    return time.monotonic() <= deadline


def _ensure_deadline() -> None:
    if not _deadline_open():
        raise AoLlmError(REFUSE_TIMEOUT)


def _wait_seconds(attempt: int, retry_after: float | None) -> float:
    """Honour Retry-After. Otherwise a short backoff that stops at the deadline."""
    if retry_after is not None:
        wait = max(0.0, float(retry_after))
    else:
        wait = 0.05 * (2**attempt)
    deadline = _DEADLINE.get()
    if deadline is None:
        return wait
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(wait, remaining)


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _retry_after_seconds(response: Any) -> float | None:
    """Seconds from a Retry-After header. A delay or an HTTP date. No value is None."""
    headers = getattr(response, "headers", None)
    raw = ""
    if headers is not None:
        raw = str(headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, when.timestamp() - time.time())


def _field_paths(exc: ValidationError) -> list[str]:
    """Location names from a validation error. The input value is not included."""
    paths: list[str] = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        path = ".".join(str(part) for part in loc)
        if path and path not in paths:
            paths.append(path)
    return paths


def _log_validation(schema_name: str, fields: list[str]) -> None:
    logger.error("ao validation %s fields=%s", schema_name, ",".join(fields))


def _repair_text(fields: list[str]) -> str:
    listed = ", ".join(fields) if fields else "unknown"
    return (
        "The previous JSON failed schema validation at these field paths: "
        f"{listed}. Return one JSON object that matches the schema."
    )


def _coerce(schema: type[T], payload: Any) -> T:
    if isinstance(payload, str):
        payload = _loads_model_json(payload)
    return schema.model_validate(payload)


def _strip_one_fence(text: str) -> str:
    """Drop one opening ``` line and one trailing ``` fence. No regex.

    Returns ``text`` unchanged when there is no leading fence line, so the
    caller can tell the retry would repeat the same parse.
    """
    body = text.strip()
    if not body.startswith(_FENCE):
        return text
    newline = body.find("\n")
    if newline < 0:
        return text
    rest = body[newline + 1 :].rstrip()
    if rest.endswith(_FENCE):
        rest = rest[: -len(_FENCE)].rstrip()
    return rest
