"""FDE-REASON-LEAK-01. Flag DMS_REPLY_HYGIENE defaults off.

Synthetic schema only. Flag off returns the same reconfirm bytes.
Flag on: a generate 429 is a typed code and a fixed sentence, and a
reconfirm with nothing to run has no Yes token.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from cortex_client.compute import _insights_generate_post, ai_call_error_payload

_RAW = (
    "FreeRoute token budget exceeded (HTTP 429): "
    "FreeRoute token budget exceeded; retry later"
)
_DEAD = (
    "Closest question: none. No question close to this one can be confirmed "
    "as answerable. Yes runs that question, which has nothing to run"
)
_SCHEMA = "fills\nstores"
_QUESTION = "How are fills doing?"
_BANNED = ("HTTP 4", "HTTP 5", "token budget", "serving_")
_EXCEPTIONS = (
    "AttributeError",
    "Exception",
    "HTTPError",
    "InsightsError",
    "KeyError",
    "RuntimeError",
    "ServingLeaseCap",
    "TimeoutException",
    "Traceback",
    "TypeError",
    "ValueError",
)


def _gap(text: str = _DEAD) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain

    return build_abstain(
        reason="unknown_measure",
        question=_QUESTION,
        stage="test",
        answer_id="ans_reply_test",
        text=text,
        abstain_reason="unknown_measure",
        assumptions=["GEN-01: unknown_measure"],
        route="abstain",
        ask_mode="live",
    )


def _offer(
    env: dict[str, Any],
    *,
    complete: Any,
    reason_complete: Any = None,
) -> dict[str, Any]:
    from dms_executor.reconfirm import apply_reconfirm

    return apply_reconfirm(
        env,
        question=_QUESTION,
        space_id="sp",
        session_id="ses",
        tables=["fills", "stores"],
        ask_path=None,
        ontology="measure fill_count on fills",
        schema=_SCHEMA,
        complete=complete,
        reason_complete=reason_complete,
    )


def user_visible(env: dict[str, Any]) -> list[str]:
    """Prose a person can read. Machine codes are not in this list."""
    found: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            found.append(value)

    add(env.get("text"))
    add(env.get("abstain_reason"))
    for item in env.get("assumptions") or []:
        add(str(item))
    for item in env.get("suggestions") or []:
        add(item if isinstance(item, str) else "")
    block = env.get("reconfirm")
    if isinstance(block, dict):
        for key in ("reason_text", "suggested_question", "closest_question", "why", "definition"):
            add(block.get(key))
    clarify = env.get("clarify")
    if isinstance(clarify, dict):
        add(clarify.get("question"))
        for reading in clarify.get("readings") or []:
            if isinstance(reading, dict):
                add(reading.get("text"))
                add(reading.get("label"))
    return found


def assert_user_visible_clean(env: dict[str, Any]) -> None:
    """Test-side output check. Not a question rule."""
    blob = "\n".join(user_visible(env))
    for needle in _BANNED:
        if needle in blob:
            raise AssertionError(needle)
    for name in _EXCEPTIONS:
        if name in blob:
            raise AssertionError(name)


class _Resp:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {}
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _Http:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body

    def post(self, _url: str, **_kwargs: Any) -> _Resp:
        return _Resp(self.status, self.body)


def _generate(status: int) -> dict[str, Any] | None:
    return _insights_generate_post(
        _Http(status, {"error": _RAW, "answer": _RAW}),
        "http://127.0.0.1:9",
        {"question": _QUESTION, "generate": True},
        None,
    )


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.reply_hygiene import finish_reconfirm, reply_hygiene_enabled

    monkeypatch.delenv("DMS_REPLY_HYGIENE", raising=False)
    assert reply_hygiene_enabled() is False
    env = {"text": _RAW, "reconfirm": {"token": "rcf_x", "suggested_question": "none"}}
    assert finish_reconfirm(env, question=_QUESTION, schema=_SCHEMA, complete=lambda _p: "x") is env
    monkeypatch.setenv("DMS_REPLY_HYGIENE", "0")
    assert reply_hygiene_enabled() is False
    assert finish_reconfirm(env, question=_QUESTION, schema=_SCHEMA) is env


def test_flag_off_reconfirm_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.reconfirm import _reset_reconfirm

    monkeypatch.setattr("dms_core.ids.mint_id", lambda prefix: f"{prefix}_fixed")
    base = _gap()
    base["reconfirm"] = {
        "suggested_question": "none",
        "closest_question": "",
        "reason_code": "unspecified",
        "reason_text": _DEAD,
    }

    def run() -> dict[str, Any]:
        _reset_reconfirm()
        return _offer(copy.deepcopy(base), complete=lambda _prompt: "unused")

    monkeypatch.delenv("DMS_REPLY_HYGIENE", raising=False)
    unset = run()
    monkeypatch.setenv("DMS_REPLY_HYGIENE", "0")
    zero = run()
    assert unset == zero
    assert unset["reconfirm"]["token"] == "rcf_fixed"
    assert unset["reconfirm"]["suggested_question"] == "none"
    assert unset["text"] == _DEAD


def test_generate_429_flag_off_keeps_the_machine_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.reply_hygiene import generate_reply

    monkeypatch.delenv("DMS_REPLY_HYGIENE", raising=False)
    monkeypatch.delenv("DMS_STRICT_MODEL", raising=False)
    monkeypatch.delenv("DMS_STRICT_PROVIDER", raising=False)
    payload = _generate(429)
    assert payload == ai_call_error_payload(429)
    assert generate_reply(payload, question=_QUESTION, space_id=None, session_id=None) is None
    assert _RAW not in json.dumps(payload)


def test_generate_429_is_a_typed_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.reply_hygiene import USER_SENTENCE, generate_reply

    monkeypatch.setenv("DMS_REPLY_HYGIENE", "1")
    monkeypatch.delenv("DMS_STRICT_MODEL", raising=False)
    monkeypatch.delenv("DMS_STRICT_PROVIDER", raising=False)
    payload = _generate(429)
    assert payload is not None
    assert payload.get("ai_call_status") == 429
    env = generate_reply(payload, question=_QUESTION, space_id="sp", session_id="ses")
    assert env is not None
    assert env["reason_code"] == "model_unreachable"
    assert env["text"] == USER_SENTENCE
    assert env["abstain_reason"] == USER_SENTENCE
    assert env["retry"] == {"retryable": True, "after_s": 60}
    assert "reconfirm" not in env
    assert _RAW not in json.dumps(user_visible(env))
    assert_user_visible_clean(env)


def test_capacity_reason_is_not_the_answer_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.reply_hygiene import USER_SENTENCE, stamp_capacity

    monkeypatch.delenv("DMS_REPLY_HYGIENE", raising=False)
    leaked = (
        "I can't answer this right now: serving connections are at the cap "
        "(serving_lease_cap)."
    )
    env = _gap(leaked)
    env["assumptions"] = ["serving_lease_cap"]
    stamp_capacity(env, "serving_lease_cap")
    assert "serving_lease_cap" in env["text"]
    monkeypatch.setenv("DMS_REPLY_HYGIENE", "1")
    stamp_capacity(env, "serving_lease_cap")
    assert env["text"] == USER_SENTENCE
    assert env["reason_code"] == "serving_lease_cap"
    assert env["retry"]["retryable"] is True
    assert_envelope_valid(env)
    assert_user_visible_clean(env)


def test_empty_closest_question_has_no_yes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.clarify import resolve_pick

    monkeypatch.setenv("DMS_REPLY_HYGIENE", "1")
    prompts: list[str] = []

    def reason(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(
            {
                "readings": [
                    {"id": "r1", "text": "How many fills were sold?"},
                    {"id": "r2", "text": "Which stores sold fills?"},
                ]
            }
        )

    def boom(_prompt: str) -> str:
        raise AssertionError("the closest-question call must not run")

    base = _gap()
    base["reconfirm"] = {
        "suggested_question": "none",
        "closest_question": "",
        "reason_code": "unspecified",
        "reason_text": _DEAD,
    }
    out = _offer(base, complete=boom, reason_complete=reason)
    assert "token" not in out.get("reconfirm", {})
    assert "reconfirm" not in out
    block = out["clarify"]
    assert block["blocks"] is True
    assert 1 <= len(block["readings"]) <= 3
    assert block["readings"][0]["reading_id"] == "r1"
    assert "fills" in block["readings"][0]["label"]
    assert prompts and "HTTP" not in prompts[0] and "token budget" not in prompts[0]
    assert _DEAD not in out["text"]
    assert_user_visible_clean(out)
    picked = resolve_pick(
        block["clarify_id"],
        reading_id="r1",
        text=None,
        grantable={"fills", "stores"},
        dialect="duckdb",
    )
    assert picked.question == "How many fills were sold?"


def test_no_reading_is_a_plain_abstain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_REPLY_HYGIENE", "1")
    base = _gap()
    base["reconfirm"] = {
        "suggested_question": "",
        "closest_question": "",
        "reason_code": "unspecified",
    }
    out = _offer(base, complete=lambda _prompt: "", reason_complete=lambda _prompt: "")
    assert "reconfirm" not in out
    assert "clarify" not in out
    assert out["abstained"] is True
    assert_user_visible_clean(out)


def test_schema_question_keeps_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_REPLY_HYGIENE", "1")
    monkeypatch.setattr("dms_core.ids.mint_id", lambda prefix: f"{prefix}_fixed")
    base = _gap("Which fills should I total?")
    base["reconfirm"] = {
        "suggested_question": "How many fills were sold?",
        "closest_question": "How many fills were sold?",
        "reason_code": "unknown_measure",
        "reason_text": "The measure is not declared.",
    }

    def boom(_prompt: str) -> str:
        raise AssertionError("a schema question must not ask back")

    out = _offer(base, complete=boom, reason_complete=boom)
    assert out["reconfirm"]["token"] == "rcf_fixed"
    assert out["reconfirm"]["suggested_question"] == "How many fills were sold?"
    assert "clarify" not in out


def test_reason_role_sends_no_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.reply_hygiene import reason_completion

    monkeypatch.setenv("DMS_REPLY_HYGIENE", "1")
    seen: list[dict[str, Any]] = []

    class Cortex:
        def compute_insights(self, prompt: str, **kwargs: Any) -> dict[str, str]:
            seen.append({"prompt": prompt, **kwargs})
            return {"text": "{\"readings\": []}"}

    assert reason_completion(Cortex(), "prompt") == "{\"readings\": []}"
    assert seen[0]["route_hint"] == "reason"
    assert "model" not in seen[0]
    assert "provider" not in seen[0]
    monkeypatch.delenv("DMS_REPLY_HYGIENE", raising=False)
    assert reason_completion(Cortex(), "prompt") == ""
    assert len(seen) == 1


def test_validator_rejects_a_dirty_reply() -> None:
    dirty = {"text": _RAW, "assumptions": ["ServingLeaseCap: cap"]}
    with pytest.raises(AssertionError):
        assert_user_visible_clean(dirty)
    with pytest.raises(AssertionError):
        assert_user_visible_clean({"text": "serving_deadline_reserve"})
