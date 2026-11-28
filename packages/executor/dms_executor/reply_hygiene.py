"""User-reply hygiene. Default off.

``DMS_REPLY_HYGIENE=1`` keeps transport, budget, and capacity failures out of
the words a person reads. A suggested question is kept only when it names a
granted schema identifier. A reconfirm with nothing to run is an ask-back
or a plain abstain, not a Yes button.

Flag off returns the same object and does not call the model.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

USER_SENTENCE = "I could not reach the model just now; ask again in a minute"
RETRY_AFTER_S = 60
ROLE_REASON = "reason"
_ENV = "DMS_REPLY_HYGIENE"
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")
_CAPACITY = frozenset(
    {
        "serving_lease_cap",
        "serving_deadline_reserve",
        "serving_deadline_exceeded",
        "serving_lease_queue_full",
    }
)
_BUDGET = frozenset({"insights_timeout", "insights_call_cap"})
_TRANSPORT_HEADS = frozenset(
    {
        "insights_http_error",
        "cortex_unavailable",
        "model_unreachable",
    }
)


def reply_hygiene_enabled() -> bool:
    """True only when ``DMS_REPLY_HYGIENE`` is the digit 1. Unset is off."""
    return os.environ.get(_ENV, "0").strip() == "1"


def schema_names(schema: str) -> list[str]:
    """Identifiers in a granted-schema prompt, first-seen order."""
    seen: list[str] = []
    folded: set[str] = set()
    for match in _IDENT.finditer(schema or ""):
        token = match.group(0)
        key = token.casefold()
        if key in folded:
            continue
        folded.add(key)
        seen.append(token)
    return seen


def matches_schema(text: str, names: list[str] | set[str]) -> bool:
    """True when ``text`` names one granted identifier. Not a phrase list."""
    if not isinstance(text, str) or not text.strip() or not names:
        return False
    allowed = {name.casefold() for name in names if name}
    if not allowed:
        return False
    tokens = {match.group(0).casefold() for match in _IDENT.finditer(text)}
    return bool(tokens & allowed)


def _head(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("GEN-01:"):
        text = text[len("GEN-01:") :].strip()
    return text.split(":", 1)[0].strip()


def _http_status(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    for key in ("_insights_http_status", "ai_call_status"):
        raw = payload.get(key)
        try:
            status = int(raw or 0)
        except (TypeError, ValueError):
            status = 0
        if status:
            return status
    return 0


def _candidates(env: dict[str, Any] | None, reason: str | None) -> list[str]:
    found: list[str] = []
    if reason:
        found.append(reason)
    if not isinstance(env, dict):
        return found
    for key in ("reason_code", "abstain_reason"):
        raw = env.get(key)
        if isinstance(raw, str) and raw.strip():
            found.append(raw.strip())
    block = env.get("reconfirm")
    if isinstance(block, dict):
        raw = block.get("reason_code")
        if isinstance(raw, str) and raw.strip():
            found.append(raw.strip())
    for item in env.get("assumptions") or []:
        found.append(str(item))
    return found


def transport_code(
    env: dict[str, Any] | None = None,
    *,
    reason: str | None = None,
) -> str | None:
    """Typed code for a transport, budget, or capacity failure. Else None.

    HTTP status and known reason heads only. The question text is not read.
    """
    if not reply_hygiene_enabled():
        return None
    status = _http_status(env)
    if status == 429 or status >= 500:
        return "model_unreachable"
    for item in _candidates(env, reason):
        head = _head(item)
        if head in _CAPACITY or head in _BUDGET:
            return head
        if head in _TRANSPORT_HEADS:
            return "model_unreachable"
    return None


def _code_token(raw: str) -> str:
    head = _head(raw).casefold()
    if _TOKEN.fullmatch(head):
        return head
    return "unspecified"


def transport_envelope(
    source: dict[str, Any] | None,
    code: str,
    *,
    question: str,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Abstain a person can read. The machine code stays on ``reason_code``."""
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    sid = space_id
    sess = session_id
    if isinstance(source, dict):
        if sid is None and isinstance(source.get("space_id"), str):
            sid = source["space_id"]
        if sess is None and isinstance(source.get("session_id"), str):
            sess = source["session_id"]
    env = build_abstain(
        reason=code,
        question=question,
        stage="reply_hygiene",
        answer_id="ans_reply_hygiene",
        text=USER_SENTENCE,
        abstain_reason=USER_SENTENCE,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=["retry"],
        space_id=sid,
        session_id=sess,
        ask_mode="live",
        route="abstain",
    )
    env["reason_code"] = code
    env["retry"] = {"retryable": True, "after_s": RETRY_AFTER_S}
    assert_envelope_valid(env)
    return env


def stamp_capacity(env: dict[str, Any], code: str) -> None:
    """Replace a capacity sentence in place. No-op when the flag is off."""
    if not reply_hygiene_enabled() or code not in _CAPACITY:
        return
    from dms_executor.envelope import assert_envelope_valid

    env["text"] = USER_SENTENCE
    env["abstain_reason"] = USER_SENTENCE
    env["reason_code"] = code
    env["retry"] = {"retryable": True, "after_s": RETRY_AFTER_S}
    env["assumptions"] = ["retry"]
    env.pop("reconfirm", None)
    assert_envelope_valid(env)


def generate_reply(
    payload: dict[str, Any] | None,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    reason: str | None = None,
) -> dict[str, Any] | None:
    """User envelope for a generate failure. None when the flag is off or this is not one."""
    code = transport_code(payload, reason=reason)
    if code is None:
        return None
    return transport_envelope(
        payload,
        code,
        question=question,
        space_id=space_id,
        session_id=session_id,
    )


def _json_text(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_readings(raw: str) -> list[dict[str, str]]:
    """Structured readings only. Prose is not scanned for a question."""
    text = _json_text(raw)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    items = parsed.get("readings") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            out.append({"id": f"r{len(out) + 1}", "text": item.strip()})
        elif isinstance(item, dict):
            line = " ".join(str(item.get("text") or "").split())
            if not line:
                continue
            ident = str(item.get("id") or "").strip()
            if not ident or len(ident) > 16 or not ident.replace("_", "").isalnum():
                ident = f"r{len(out) + 1}"
            out.append({"id": ident, "text": line})
        if len(out) == 3:
            break
    return out


def _readings_prompt(question: str, schema: str, code: str) -> str:
    return (
        "The last answer could not be confirmed.\n"
        "Write 1 to 3 concrete questions this granted schema can answer.\n"
        "Each question must name a granted table or column.\n"
        "Return a JSON object and nothing else. "
        "Key readings is a list of objects with id and text. Do not write SQL.\n\n"
        f"Reason code:\n{code}\n\n"
        f"Granted schema:\n{schema}\n\n"
        f"Question:\n{question}\n"
    )


def reason_completion(cortex: Any, prompt: str) -> str:
    """One OpenVault call with role ``reason``. No provider and no model id."""
    if not reply_hygiene_enabled():
        return ""
    fn = getattr(cortex, "compute_insights", None)
    if not callable(fn):
        return ""
    try:
        payload = fn(prompt, route_hint=ROLE_REASON)
    except Exception:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "answer", "query_sql", "suggested_question"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _filter_suggestions(env: dict[str, Any], names: list[str]) -> None:
    raw = env.get("suggestions")
    if not isinstance(raw, list):
        return
    kept = [item.strip() for item in raw if isinstance(item, str) and matches_schema(item, names)]
    if kept != [item for item in raw if isinstance(item, str)]:
        env["suggestions"] = kept


def _runnable(block: dict[str, Any], names: list[str]) -> bool:
    definition = block.get("definition")
    if isinstance(definition, str) and definition.strip():
        return True
    sql = block.get("sql")
    if isinstance(sql, str) and sql.strip():
        return True
    for key in ("suggested_question", "closest_question"):
        if matches_schema(str(block.get(key) or ""), names):
            return True
    return False


def _blank_unmatched(block: dict[str, Any], names: list[str]) -> None:
    for key in ("suggested_question", "closest_question"):
        raw = block.get(key)
        if isinstance(raw, str) and raw.strip() and not matches_schema(raw, names):
            block[key] = ""


def _plain_abstain(
    source: dict[str, Any],
    *,
    question: str,
    code: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.gen_path_refuse import customer_abstain_text

    token = _code_token(code)
    sentence = customer_abstain_text(token)
    env = build_abstain(
        reason=token,
        question=question,
        stage="reply_hygiene",
        answer_id="ans_reply_abstain",
        text=sentence,
        abstain_reason=sentence,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[token],
        space_id=space_id if space_id is not None else source.get("space_id"),
        session_id=session_id if session_id is not None else source.get("session_id"),
        ask_mode=str(source.get("ask_mode") or "live"),
        route="abstain",
    )
    env["reason_code"] = token
    assert_envelope_valid(env)
    return env


def _ask_back(
    source: dict[str, Any],
    *,
    question: str,
    readings: list[dict[str, str]],
    space_id: str | None,
    session_id: str | None,
    tables: list[str] | None,
) -> dict[str, Any]:
    import uuid

    from dms_executor.abstain import build_abstain
    from dms_executor.clarify import save_question_readings
    from dms_executor.envelope import assert_envelope_valid, attach_clarify_block

    clarify_id = "clr_" + uuid.uuid4().hex[:16]
    lines = ["Which reading should I answer?"]
    lines.extend(f"{index}. {item['text']}" for index, item in enumerate(readings, 1))
    sid = space_id if space_id is not None else source.get("space_id")
    sess = session_id if session_id is not None else source.get("session_id")
    env = build_abstain(
        reason="clarify_ambiguous",
        question=question,
        stage="reply_hygiene",
        answer_id="ans_reply_clarify",
        text="\n".join(lines),
        abstain_reason="clarify_ambiguous",
        values=[],
        rows=[],
        sql_used=None,
        assumptions=["clarify_ambiguous"],
        space_id=sid if isinstance(sid, str) else None,
        session_id=sess if isinstance(sess, str) else None,
        ask_mode=str(source.get("ask_mode") or "live"),
        route="needs_clarification",
    )
    attach_clarify_block(
        env,
        question=question,
        readings=[{"id": item["id"], "text": item["text"], "sql_preview": ""} for item in readings],
        clarify_id=clarify_id,
    )
    shown = env["clarify"]["readings"]
    if isinstance(shown, list):
        for item in shown:
            if isinstance(item, dict):
                item["reading_id"] = item.get("id") or ""
                item["label"] = item.get("text") or ""
    save_question_readings(
        clarify_id,
        question=question,
        readings=[{"id": item["id"], "text": item["text"]} for item in readings],
        space_id=sid if isinstance(sid, str) else None,
        session_id=sess if isinstance(sess, str) else None,
        tables=list(tables or []),
    )
    assert_envelope_valid(env)
    return env


def _drop_token(block: dict[str, Any]) -> None:
    token = block.get("token")
    if not isinstance(token, str) or not token:
        return
    from dms_executor.reconfirm import take_pending

    take_pending(token)


def finish_reconfirm(
    env: dict[str, Any],
    *,
    question: str,
    schema: str,
    space_id: str | None = None,
    session_id: str | None = None,
    tables: list[str] | None = None,
    complete: Any = None,
) -> dict[str, Any]:
    """Clean one envelope. Flag off returns ``env`` unchanged."""
    if not reply_hygiene_enabled() or not isinstance(env, dict):
        return env
    code = transport_code(env)
    if code is not None:
        retry = env.get("retry")
        if (
            env.get("reason_code") == code
            and env.get("text") == USER_SENTENCE
            and "reconfirm" not in env
            and isinstance(retry, dict)
        ):
            return env
        return transport_envelope(
            env,
            code,
            question=question,
            space_id=space_id,
            session_id=session_id,
        )
    names = schema_names(schema)
    clarify = env.get("clarify")
    if isinstance(clarify, dict) and clarify.get("blocks") is True:
        return env
    block = env.get("reconfirm")
    if not isinstance(block, dict):
        _filter_suggestions(env, names)
        return env
    if _runnable(block, names):
        _blank_unmatched(block, names)
        _filter_suggestions(env, names)
        return env
    _drop_token(block)
    token = _code_token(str(block.get("reason_code") or env.get("abstain_reason") or ""))
    readings: list[dict[str, str]] = []
    if names and callable(complete):
        raw = complete(_readings_prompt(question, schema, token))
        if isinstance(raw, str):
            readings = [
                item for item in parse_readings(raw) if matches_schema(item["text"], names)
            ][:3]
    if readings:
        return _ask_back(
            env,
            question=question,
            readings=readings,
            space_id=space_id,
            session_id=session_id,
            tables=tables if tables else names,
        )
    return _plain_abstain(
        env,
        question=question,
        code=token,
        space_id=space_id,
        session_id=session_id,
    )
