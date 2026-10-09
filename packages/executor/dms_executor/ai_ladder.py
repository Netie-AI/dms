"""Plan, then SQL, then one higher-tier retry. OpenVault picks the model.

DMS sends the prompt and, on the last retry, a tier hint. It does not send
a provider, a model id, or a key. Provider and model on the trace are copied
from the response metadata only.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from cortex_client.insights import SCHEMA_CONTEXT_FIELD
from cortex_client.strict_pin import SERVED_MODEL_HEADER, SERVED_PROVIDER_HEADER

from dms_executor.abstain import build_abstain

_CHAT_PATH = "/v1/chat/completions"
_TIER_HIGHER = "higher"
_DIRECT_HEADS = frozenset({"ungranted", "hostile_sql"})
_CONTEXT_KEYS = (
    "measures",
    "objects",
    "columns",
    "encodings",
    "bound_values",
    "relationships",
)
_LADDER_KEYS = frozenset(
    {
        "ladder_step",
        "sql_prompt",
        "sql_plan",
        "ov_tier",
        "closest_question",
    }
)
_FALLBACK_QUESTION = "Which measure on a granted table should I calculate?"
_VISIBLE = (
    "I could not answer that from the data this Space can read. "
    "Confirm the closest question I can answer."
)


def ladder_active() -> bool:
    from cortex_client.compute import cloop_b_enabled, ontology_ranked_lane_enabled

    return cloop_b_enabled() and not ontology_ranked_lane_enabled()


def reported_call(step: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Provider and model from response metadata. Absent stays null."""
    provider = _reported(payload, "served_provider")
    model = _reported(payload, "served_model")
    out: dict[str, Any] = {"step": step, "provider": provider, "model": model}
    route = _reported(payload, "ov_route")
    if route:
        out["ov_route"] = route
    return out


def _reported(payload: Mapping[str, Any] | None, key: str) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def context_block(ctx: Mapping[str, Any]) -> str:
    """Ontology and schema text already on the context. No new value sample."""
    parts: list[str] = []
    schema = ctx.get(SCHEMA_CONTEXT_FIELD)
    if isinstance(schema, str) and schema.strip():
        parts.append(schema.strip())
    for key in _CONTEXT_KEYS:
        raw = ctx.get(key)
        if not raw:
            continue
        parts.append(f"{key}:\n{json.dumps(raw, default=str, sort_keys=True)}")
    return "\n".join(parts)


def plan_prompt(question: str, context: str) -> str:
    return (
        "Write a step-by-step plan for the question. "
        "Use only the ontology and schema context below, including the column "
        "values it already lists.\n\n"
        f"question:\n{question}\n\ncontext:\n{context}"
    )


def sql_prompt(question: str, plan: str, context: str) -> str:
    return (
        f"{question}\n\nplan:\n{plan}\n\ncontext:\n{context}\n\n"
        "Write one read-only SQL statement that follows the plan."
    )


def plan_text(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    raw = payload.get("plan")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _message_text(payload)


def _message_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def prepare_sql_context(
    ctx: dict[str, Any],
    *,
    question: str,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """One plan call, then a SQL prompt that contains that plan."""
    context = context_block(ctx)
    prompt = plan_prompt(question, context)
    plan_ctx = dict(ctx)
    plan_ctx["ladder_step"] = "plan"
    plan_ctx["sql_prompt"] = prompt
    try:
        payload = compute(plan_ctx)
    except Exception:
        payload = None
    body = payload if isinstance(payload, dict) else None
    calls.append(reported_call("plan", body))
    plan = plan_text(body)
    out = dict(ctx)
    out["ladder_step"] = "sql"
    out["sql_plan"] = plan
    out["sql_prompt"] = sql_prompt(question, plan, context)
    return out


def reason_head(reason: str) -> str:
    text = str(reason or "")
    while True:
        if text.startswith("loop_exhausted:"):
            text = text.split(":", 1)[1]
            continue
        if text.startswith("checker:"):
            text = text.split(":", 1)[1]
            continue
        break
    return text.split(":", 1)[0]


def direct_refusal(reason: str) -> bool:
    """Ungranted tables and destructive SQL. Everything else can confirm."""
    return reason_head(reason) in _DIRECT_HEADS


def closest_question(payload: Mapping[str, Any] | None, sql: str | None) -> str:
    raw = ""
    if isinstance(payload, Mapping):
        for key in ("closest_question", "suggestion"):
            got = payload.get(key)
            if isinstance(got, str) and got.strip():
                raw = got.strip()
                break
    if not raw or (sql and sql in raw):
        return _FALLBACK_QUESTION
    return raw


def reconfirm_context(
    ctx: Mapping[str, Any],
    *,
    question: str,
    reason: str,
) -> dict[str, Any]:
    context = context_block(ctx)
    prompt = (
        "The attempt stopped. Reply with a short reason and one closest "
        "answerable question that uses only the schema context. "
        "Do not include SQL or a database error.\n\n"
        f"stop_code:\n{reason_head(reason)}\n\n"
        f"question:\n{question}\n\ncontext:\n{context}"
    )
    out = dict(ctx)
    out["ladder_step"] = "reconfirm"
    out["sql_prompt"] = prompt
    out.pop("ov_tier", None)
    return out


def reconfirm_envelope(
    *,
    reason: str,
    question: str,
    sql: str | None,
    retries: int,
    space_id: str | None,
    session_id: str | None,
    closest: str,
    as_of: str,
) -> dict[str, Any]:
    """Abstain that asks the steward to confirm one next question."""
    from datetime import UTC, datetime

    stamp = as_of or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    suggestion = closest or _FALLBACK_QUESTION
    text = f"{_VISIBLE} {suggestion}"
    env = build_abstain(
        reason=reason,
        question=question,
        sql=sql,
        retries=retries,
        stage="extract_loop",
        abstain_reason="reconfirm",
        answer_id="ans_gen01_abstain",
        text=text,
        suggestions=[suggestion],
        rows=[],
        sql_used=None,
        assumptions=["reconfirm", reason_head(reason)],
        as_of=stamp,
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="confirm",
    )
    return scrub_ungranted(env, reason)


def scrub_ungranted(env: dict[str, Any], reason: str) -> dict[str, Any]:
    """Drop ungranted table names from the strings a person reads."""
    if "ungranted:" not in reason:
        return env
    tail = reason.split("ungranted:", 1)[1]
    names = [part.strip() for part in tail.replace(" ", ",").split(",") if part.strip()]
    if not names:
        return env
    out = dict(env)

    def _clean(value: str) -> str:
        cleaned = value
        for name in names:
            cleaned = cleaned.replace(name, "a withheld table")
        return cleaned

    if isinstance(out.get("text"), str):
        out["text"] = _clean(out["text"])
    suggestions = out.get("suggestions")
    if isinstance(suggestions, list):
        out["suggestions"] = [_clean(str(item)) for item in suggestions]
    assumptions = out.get("assumptions")
    if isinstance(assumptions, list):
        out["assumptions"] = [_clean(str(item)) for item in assumptions]
    return out


def strip_ladder_keys(ontology: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(ontology, dict):
        return None
    return {key: value for key, value in ontology.items() if key not in _LADDER_KEYS}


def ov_complete(prompt: str, *, tier: str | None) -> dict[str, Any] | None:
    """One OpenVault completion. No provider, model, or key on the request.

    ``tier`` is only the higher-tier hint. OpenVault chooses the model.
    """
    base = os.environ.get("OPENVAULT_URL", "").strip().rstrip("/")
    if not base:
        return None
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if tier:
        body["tier"] = tier
    try:
        with httpx.Client(timeout=30.0) as http:
            res = http.post(f"{base}{_CHAT_PATH}", json=body)
        payload = res.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return _from_ov(payload, res.headers)


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    want = name.lower()
    for key, value in headers.items():
        if str(key).lower() == want and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _from_ov(payload: Mapping[str, Any], headers: Any) -> dict[str, Any]:
    provider = _reported(payload, "served_provider") or _header(headers, SERVED_PROVIDER_HEADER)
    model = _reported(payload, "served_model") or _header(headers, SERVED_MODEL_HEADER)
    route = _reported(payload, "ov_route") or _reported(payload, "route")
    out: dict[str, Any] = {
        "served_provider": provider,
        "served_model": model,
        "raw_reply": dict(payload),
    }
    if route:
        out["ov_route"] = route
    content = _message_text(payload)
    parsed: dict[str, Any] | None = None
    if content.startswith("{"):
        try:
            got = json.loads(content)
        except json.JSONDecodeError:
            got = None
        if isinstance(got, dict):
            parsed = got
    if parsed is not None:
        for key in ("plan", "query_sql", "closest_question", "reason"):
            raw = parsed.get(key)
            if isinstance(raw, str) and raw.strip():
                out[key] = raw.strip()
        return out
    if content:
        head = content.split(None, 1)[0].lower()
        if head in {"select", "with"}:
            out["query_sql"] = content
        else:
            out["plan"] = content
    return out


def higher_tier() -> str:
    return _TIER_HIGHER
