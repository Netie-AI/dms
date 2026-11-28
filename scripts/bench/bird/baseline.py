"""One OpenVault call. Role string ``sql``. No provider and no model id.

The prompt is the schema plus three sample rows, then the question. One shot.
The same scorer grades the executed SQL.
"""

from __future__ import annotations

import json
from typing import Any

ROLE = "sql"
ESCALATE_ROLE = "escalate"
_FORBIDDEN = frozenset(
    {"model", "provider", "served_model", "served_provider", "strict", "temperature"}
)
_SYSTEM = (
    "Write one SQLite SELECT that answers the question. "
    "Use only tables and columns in the schema. Return the SQL only."
)


class ModelKeyRefused(ValueError):
    """The body named a provider or a model. OpenVault routes. We do not."""


def reject_model_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN:
                raise ModelKeyRefused(str(key))
            reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            reject_model_keys(item)


def baseline_body(question: str, schema_text: str) -> dict[str, Any]:
    """Chat body. ``role`` is the string ``sql``. Sample rows are inside the schema text."""
    body: dict[str, Any] = {
        "role": ROLE,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {"schema": schema_text, "question": question},
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        ],
    }
    reject_model_keys(body)
    return body


def escalate_body(body: dict[str, Any]) -> dict[str, Any]:
    """Same prompt, role ``escalate``. OpenVault picks the route. No model id."""
    nxt = dict(body)
    nxt["role"] = ESCALATE_ROLE
    reject_model_keys(nxt)
    return nxt


def sql_from_reply(payload: Any) -> str:
    """The SQL text from one completion. A fence is unwrapped. The question is not read."""
    text = _content(payload).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner: list[str] = []
        for line in lines[1:]:
            if line.strip().startswith("```"):
                break
            inner.append(line)
        text = "\n".join(inner).strip()
    return text


def _content(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    for key in ("content", "sql", "text"):
        raw = payload.get(key)
        if isinstance(raw, str):
            return raw
    return ""


def copied_model(payload: Any) -> str:
    """A model name OpenVault already returned. Absent stays empty. We do not invent one."""
    if not isinstance(payload, dict):
        return ""
    raw = payload.get("served_model")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""
