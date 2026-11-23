"""DMS_GRAIN_AI. Default off.

On, grain candidates are approved ontology items (``Ontology.from_store``).
The question is mapped by two OpenVault readings. The request carries the
role alias ``grain`` and does not name a model. A low confidence or a
disagreement returns the reconfirm envelope (#464). It does not abstain
with no Yes.

``load_understand_question`` calls ``dms_executor.ai_first.understand_question``
when that module is on the base. Until then it returns
``thin_understand_question``, which has the same arguments and the same
keys. ``reading`` stays None until a model sentence arrives. This module
does not build that sentence from words in the question.

Off, this module is not on the ask path. The existing detector stays.

Credential: an OpenVault API key (``ov_...``) from ``OPENVAULT_API_KEY``,
else ``CORTEX_API_KEY``. ``POST /v1/chat/completions`` verifies the bearer
against OpenVault's issued-key store (``vault/auth.py resolve_caller``). The
DMS signing-service token (``DMS_OV_SERVICE_TOKEN``) is not an issued key, so
OpenVault answers 401 to it (#481). With no ``ov_`` key the ask is refused by
name before any call. A call that cannot be made or is refused (401/403,
unreachable, another HTTP error) is a named refusal, never a reconfirm: an
auth failure is not model doubt.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

GRAIN_AI_ENV = "DMS_GRAIN_AI"
ROLE_ALIAS = "grain"
SCHEMA_NAME = "GrainReading"
# Both readings must reach this. Below it, the ask reconfirms.
CONFIDENCE_MIN = 0.8
WHY_SPLIT = "the two grain readings did not name the same grains"
WHY_LOW = "the grain reading is not confident enough to choose"

_PROMPT_FIRST = (
    "Name the approved grains this question groups or filters by. "
    "Use only names from the candidate list in the user message. "
    "For each name, cite one character span of the question text you were given. "
    "Reply with a JSON object and nothing else. "
    "Keys are grains, evidence, confidence, reading. "
    "evidence items have grain, start, end, text. "
    "confidence is a number from 0 to 1. "
    "reading is one sentence about what the question is asking. "
    "Do not name a model."
)
_PROMPT_SECOND = (
    "Look at the question on your own, separate from any first pass. "
    "Keep a candidate only when the question text uses it. "
    "Drop a candidate that the list names and the question does not. "
    "Each citation is a slice of that question text, with start inclusive and end exclusive. "
    "Reply with a JSON object and nothing else. "
    "Keys are grains, evidence, confidence, reading. "
    "Set confidence below 1 when more than one grouping is plausible."
)

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
# Same relay as ``cortex_client.strict_pin`` and ``ao_vendor.llm.ov_client``
# (#493). Duplicated, not imported: that module is held by an open PR.
API_KEY_ENVS = ("OPENVAULT_API_KEY", "CORTEX_API_KEY")
API_KEY_PREFIX = "ov_"
URL_ENV = "OPENVAULT_URL"
CHAT_PATH = "/v1/chat/completions"

REFUSE_KEY_MISSING = "ov_api_key_missing"
REFUSE_UNAUTHORIZED = "ov_unauthorized"
REFUSE_UNREACHABLE = "ov_unreachable"
_REFUSAL_TEXT = {
    REFUSE_KEY_MISSING: (
        "I could not read this question's grains: no OpenVault API key (ov_...) "
        "is set for DMS, so no model was called. Nothing was run."
    ),
    REFUSE_UNAUTHORIZED: (
        "I could not read this question's grains: OpenVault refused the DMS "
        "API key. Nothing was run."
    ),
    REFUSE_UNREACHABLE: (
        "I could not read this question's grains: OpenVault could not be reached. Nothing was run."
    ),
}

Transport = Callable[[dict[str, Any]], Any]
_TRANSPORT: ContextVar[Transport | None] = ContextVar("dms_grain_ai_transport", default=None)


class GrainCallError(RuntimeError):
    """The reading could not be sent. The message is a code, never a token."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def grain_ai_enabled() -> bool:
    """True only when ``DMS_GRAIN_AI`` is the digit 1. Unset is off."""
    return os.environ.get(GRAIN_AI_ENV, "0").strip() == "1"


def thin_understand_question(
    question: str,
    examples: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Same shape as ``ai_first.understand_question``. ``reading`` stays None.

    Does not invent a sentence from the question. Does not prepend examples
    onto the question text. The real function does that once it is on the base.
    """
    rows: list[dict[str, Any]] = []
    if examples is not None:
        rows = [dict(item) for item in examples if isinstance(item, Mapping)]
    active: list[dict[str, Any]] = []
    sources: list[str] = []
    for row in rows:
        if row.get("revoked") is True:
            continue
        sql = str(row.get("sql") or "").strip()
        if not sql:
            continue
        active.append(dict(row))
        src = str(row.get("source") or "")
        if src and src not in sources:
            sources.append(src)
    return {
        "question": question,
        "examples": active,
        "sources": sources,
        "reading": None,
    }


def load_understand_question() -> Callable[..., dict[str, Any]]:
    """The base question step, or the thin stand-in when it is not imported yet.

    Import is by module name so this file still typechecks before
    ``dms_executor.ai_first`` is on the base. A rebase that adds
    ``understand_question`` is picked up with no edit here.
    """
    import importlib

    try:
        module = importlib.import_module("dms_executor.ai_first")
    except ImportError:
        return thin_understand_question
    fn = getattr(module, "understand_question", None)
    if not callable(fn):
        return thin_understand_question
    return fn


def bind_grain_transport(transport: Transport) -> Token[Transport | None]:
    return _TRANSPORT.set(transport)


def reset_grain_transport(token: Token[Transport | None]) -> None:
    _TRANSPORT.reset(token)


def approved_grain_names() -> tuple[str, ...]:
    """Names from approved items only. Proposed and revoked are absent."""
    from dms_executor.ontology import Ontology

    onto = Ontology.from_store()
    names: list[str] = []
    for name in (*onto.grain_aliases, *onto.missing_grains, *onto.objects):
        text = str(name or "").strip()
        if text and text not in names:
            names.append(text)
    return tuple(names)


def map_question_to_grains(
    question: str,
    *,
    examples: Sequence[Mapping[str, Any]] | None = None,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Grains plus evidence, or the reconfirm envelope.

    Candidates come from ``Ontology.from_store``. Two readings must name the
    same candidates and both clear ``CONFIDENCE_MIN``. Otherwise this returns
    ``reconfirm_envelope`` (Yes runs the closest question).

    A reading that could not be sent or was refused (:class:`GrainCallError`:
    no ``ov_`` key, 401/403, unreachable) returns a named ABSTAIN instead, with
    no rows and no reconfirm offer of its own. The second reading is not sent.
    """
    understand = load_understand_question()
    heard = understand(question, examples)
    if not isinstance(heard, dict):
        heard = thin_understand_question(question, examples)
    heard = dict(heard)
    question_text = str(heard.get("question") or "")
    candidates = approved_grain_names()
    transport = _TRANSPORT.get() or default_transport
    try:
        first = _one_reading(
            transport, reading=1, question_text=question_text, candidates=candidates
        )
        second = _one_reading(
            transport, reading=2, question_text=question_text, candidates=candidates
        )
    except GrainCallError as exc:
        # A refused or failed call stops the readings. It is not model doubt.
        return _refused(question, exc.code, space_id=space_id, session_id=session_id)
    if first is None or second is None:
        return _reconfirm(question, WHY_LOW, space_id=space_id, session_id=session_id)
    if set(first["grains"]) != set(second["grains"]):
        return _reconfirm(question, WHY_SPLIT, space_id=space_id, session_id=session_id)
    if first["confidence"] < CONFIDENCE_MIN or second["confidence"] < CONFIDENCE_MIN:
        return _reconfirm(question, WHY_LOW, space_id=space_id, session_id=session_id)
    chosen = tuple(name for name in candidates if name in set(first["grains"]))
    evidence = tuple(first["evidence"][name] for name in chosen)
    sentence = first["reading"] or second["reading"]
    return {
        "grains": chosen,
        "evidence": evidence,
        "confidence": min(float(first["confidence"]), float(second["confidence"])),
        "reading": sentence,
    }


def _reconfirm(
    question: str,
    why: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.sql_vote import reconfirm_envelope

    return reconfirm_envelope(
        question=question,
        why=why,
        closest_question="",
        space_id=space_id,
        session_id=session_id,
    )


def _refused(
    question: str,
    code: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Named ABSTAIN for a call that was not made or was refused. No rows.

    ``reconfirm`` is present and None. ``try_compile_multi_grain`` returns a
    mapped dict as the envelope only when it carries that key; any other dict
    is read as grains. The key holds no offer: this is not a reconfirm.
    """
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    text = _REFUSAL_TEXT.get(code) or (
        f"I could not read this question's grains: the model call failed ({code}). Nothing was run."
    )
    env = build_abstain(
        reason=code,
        question=question,
        stage="grain_ai",
        answer_id="ans_grain_ai_refused",
        text=text,
        abstain_reason=code,
        rows=[],
        sql_used=None,
        values=[],
        assumptions=[f"grain_ai: {code}"],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    assert_envelope_valid(env)
    env["reconfirm"] = None
    return env


def _one_reading(
    transport: Transport,
    *,
    reading: int,
    question_text: str,
    candidates: Sequence[str],
) -> dict[str, Any] | None:
    system = _PROMPT_FIRST if reading == 1 else _PROMPT_SECOND
    user = json.dumps(
        {"question": question_text, "candidates": list(candidates)},
        ensure_ascii=True,
    )
    body: dict[str, Any] = {
        "role": ROLE_ALIAS,
        "reading": reading,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "schema": SCHEMA_NAME,
    }
    _reject_model_keys(body)
    try:
        payload = transport(body)
    except GrainCallError:
        # Auth, reachability, or a refused body. The caller names it.
        raise
    except Exception:  # noqa: BLE001 - an unreadable reading reconfirms
        return None
    return _parse_reading(payload, question=question_text, candidates=candidates)


def _sentence(payload: Mapping[str, Any]) -> str | None:
    raw = payload.get("reading")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def _parse_reading(
    payload: Any,
    *,
    question: str,
    candidates: Sequence[str],
) -> dict[str, Any] | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    raw_grains = payload.get("grains")
    raw_evidence = payload.get("evidence")
    raw_confidence = payload.get("confidence")
    if not isinstance(raw_grains, list) or not all(isinstance(item, str) for item in raw_grains):
        return None
    if not isinstance(raw_evidence, list):
        return None
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
        return None
    confidence = float(raw_confidence)
    if confidence < 0.0 or confidence > 1.0:
        return None
    allowed = set(candidates)
    grains = [item.strip() for item in raw_grains if item.strip()]
    if any(name not in allowed for name in grains):
        return None
    spans: dict[str, dict[str, Any]] = {}
    for item in raw_evidence:
        span = _span(item, question=question, grains=grains)
        if span is None:
            return None
        spans[str(span["grain"])] = span
    if set(spans) != set(grains):
        return None
    return {
        "grains": grains,
        "evidence": spans,
        "confidence": confidence,
        "reading": _sentence(payload),
    }


def _span(item: Any, *, question: str, grains: Sequence[str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    grain = item.get("grain")
    start = item.get("start")
    end = item.get("end")
    text = item.get("text")
    if not isinstance(grain, str):
        return None
    grain = grain.strip()
    if grain not in grains:
        return None
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if not isinstance(text, str) or text == "":
        return None
    if start < 0 or end > len(question) or start >= end:
        return None
    if question[start:end] != text:
        return None
    return {"grain": grain, "start": start, "end": end, "text": text}


def _reject_model_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _MODEL_KEYS:
                raise GrainCallError("model_key_refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


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
        raise GrainCallError("openvault_url_missing")
    return base + CHAT_PATH


def default_transport(body: dict[str, Any]) -> Any:
    """POST the body. Chat-completion envelopes unwrap to the JSON object.

    No ``ov_`` key raises before anything is sent. 401/403 is
    ``ov_unauthorized``; a host that cannot be reached is ``ov_unreachable``.
    The code never carries the key.
    """
    import httpx

    url = endpoint_url()
    key = load_api_key()
    if not key:
        raise GrainCallError(REFUSE_KEY_MISSING)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=60.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise GrainCallError(REFUSE_UNAUTHORIZED) from None
        raise GrainCallError(f"ov_http_{status}") from None
    except httpx.HTTPError:
        raise GrainCallError(REFUSE_UNREACHABLE) from None
    data = response.json()
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return json.loads(content)
        if isinstance(content, dict):
            return content
    return data
