"""RULES-OFF-TYPE-01. Flag off, the demo router envelope is unchanged.

Flag on, answer_demo_question returns an ask-back envelope. A model client
takes the AI path. No model client serves that card, not a bare abstain
and not a number.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dms_executor.demo_ask import answer_demo_question
from dms_executor.envelope import assert_envelope_valid

_REVENUE = "What was total revenue?"
_CLOCK = ("as_of",)


def _stable(env: dict[str, Any] | None) -> dict[str, Any] | None:
    if env is None:
        return None
    out = dict(env)
    for key in list(out):
        if key in _CLOCK or key.startswith("engine_"):
            out.pop(key)
    return out


def test_flag_off_router_envelope_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)
    unset = _stable(answer_demo_question(_REVENUE))
    assert unset is not None
    assert unset["badge"] != "ABSTAIN"
    assert unset.get("reason_code") != "rules_off"
    monkeypatch.setenv("DMS_RULES_OFF", "0")
    assert _stable(answer_demo_question(_REVENUE)) == unset
    assert json.dumps(unset, sort_keys=True, default=str) == json.dumps(
        _stable(answer_demo_question(_REVENUE)), sort_keys=True, default=str
    )


def test_flag_on_returns_an_ask_back_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_RULES_OFF", "1")
    env = answer_demo_question(_REVENUE)
    assert isinstance(env, dict)
    assert_envelope_valid(env)
    assert env["reason_code"] == "rules_off"
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env["values"] == []
    offer = env["reconfirm"]
    assert isinstance(offer, dict)
    assert offer["reason_code"] == "rules_off"
    assert offer["suggested_question"] == _REVENUE
    assert "Total outbound revenue" not in str(env.get("text") or "")


def test_flag_on_with_a_model_client_takes_the_ai_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_executor import Executor

    monkeypatch.setenv("DMS_RULES_OFF", "1")
    seen: list[str] = []
    exe = Executor(cortex=object(), warehouse_path=tmp_path / "rules.duckdb")  # type: ignore[arg-type]

    def live(question: str, **_kwargs: object) -> dict[str, str]:
        seen.append(question)
        return {"ask_mode": "live", "routed": "ai"}

    monkeypatch.setattr(exe, "live_ask", live)
    out = exe.demo_ask(_REVENUE)
    assert seen == [_REVENUE]
    assert out == {"ask_mode": "live", "routed": "ai"}


def test_flag_on_without_a_model_client_asks_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_executor import Executor

    monkeypatch.setenv("DMS_RULES_OFF", "1")
    exe = Executor(cortex=None, warehouse_path=tmp_path / "rules.duckdb")
    out = exe.demo_ask(_REVENUE, allow_live=False)
    assert isinstance(out, dict)
    assert_envelope_valid(out)
    assert out["reason_code"] == "rules_off"
    assert isinstance(out.get("reconfirm"), dict)
    assert out["rows"] == []
    assert out["values"] == []
