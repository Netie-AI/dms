"""Ask OpenVault for a document. The provider picks the model.

The prompt is the block schema. This module does not rewrite the reply.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any

from dms_core.create_style.errors import CreateError
from dms_core.create_style.flag import enabled
from dms_core.create_style.spec import normalize

ROLE = "create"
SCHEMA = "CreateDocument"
_SYSTEM = (
    "Return one JSON object and nothing else. "
    "Keys are title and blocks. "
    "blocks is an array. "
    "Each block type is one of image_side, image_below, two_column, "
    "kpi_strip, chart_commentary, table, chart_page. "
    "image_side and image_below need image_b64 and caption. "
    "two_column needs left and right. "
    "kpi_strip needs items, and each item needs headline, value, and source. "
    "chart_commentary needs chart and commentary. "
    "chart and each chart_page chart need kind, categories, and series. "
    "kind is bar or line. "
    "series items need name and values, and values line up with categories. "
    "chart_page needs charts with exactly four charts. "
    "table needs columns and rows, and every row has one cell per column. "
    "Copy sentences and numbers from the facts. "
    "Do not add a number that is not in the facts. "
    "Keep each string whole."
)
_MODEL_KEYS = frozenset(
    {"model", "model_id", "model_name", "served_model", "served_provider", "provider", "strict"}
)
_FENCE = "```"
Transport = Callable[[dict[str, Any]], Any]
_TRANSPORT: ContextVar[Transport | None] = ContextVar("dms_create_style_transport", default=None)


def bind_transport(transport: Transport) -> Token[Transport | None]:
    return _TRANSPORT.set(transport)


def reset_transport(token: Token[Transport | None]) -> None:
    _TRANSPORT.reset(token)


def request_body(facts: str) -> dict[str, Any]:
    if not isinstance(facts, str) or not facts.strip():
        raise CreateError("facts_required")
    if len(facts) > 20_000:
        raise CreateError("text_too_long")
    user = json.dumps({"facts": facts}, ensure_ascii=True, separators=(",", ":"))
    body: dict[str, Any] = {
        "role": ROLE,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "schema": SCHEMA,
    }
    if _MODEL_KEYS.intersection(body):
        raise CreateError("create_route_field")
    return body


def propose(facts: str, transport: Transport | None = None) -> dict[str, Any]:
    """One OpenVault call. Flag off refuses. A bad reply is not replaced."""
    if not enabled():
        raise CreateError("create_style_disabled")
    body = request_body(facts)
    fn = transport if transport is not None else (_TRANSPORT.get() or default_transport)
    try:
        payload = fn(body)
    except CreateError:
        raise
    except Exception:
        raise CreateError("ov_unreachable") from None
    return normalize(_parse_payload(payload))


def _parse_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        text = _strip_fence(payload)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise CreateError("model_output_invalid") from None
    if not isinstance(payload, dict):
        raise CreateError("model_output_invalid")
    return payload


def _strip_fence(text: str) -> str:
    raw = text.strip()
    if not raw.startswith(_FENCE):
        return raw
    lines = raw.splitlines()
    if lines and lines[0].startswith(_FENCE):
        lines = lines[1:]
    if lines and lines[-1].strip() == _FENCE:
        lines = lines[:-1]
    return "\n".join(lines).strip()


def default_transport(body: dict[str, Any]) -> Any:
    import httpx

    if _MODEL_KEYS.intersection(body):
        raise CreateError("create_route_field")
    base = os.environ.get("OPENVAULT_URL", "").strip().rstrip("/")
    if not base:
        raise CreateError("openvault_url_missing")
    key = ""
    for name in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
        raw = os.environ.get(name, "").strip()
        if raw.startswith("ov_"):
            key = raw
            break
    if not key:
        raise CreateError("ov_api_key_missing")
    try:
        response = httpx.post(
            base + "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise CreateError("ov_unauthorized") from None
        raise CreateError("ov_unreachable") from None
    except httpx.HTTPError:
        raise CreateError("ov_unreachable") from None
    try:
        data = response.json()
    except ValueError:
        raise CreateError("model_output_invalid") from None
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) or isinstance(content, dict):
            return content
    return data
