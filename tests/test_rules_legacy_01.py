"""DMS_RULES_LEGACY. Default on. Digit 0 is the pure-AI switch.

Off (unset) the question-word router and the measure locker stay. On the
switch (0) they do not decide. L1 and compile serves are held by
``hold_rule_serve`` in the ask path. This file checks the switch, not the 52.
"""

from __future__ import annotations

import pytest
from dms_executor.demo_ask import answer_demo_question
from dms_executor.keyword_routes import locked_measure
from dms_executor.rules_legacy import hold_rule_serve, rules_legacy_enabled


def test_default_and_one_keep_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_RULES_LEGACY", raising=False)
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    assert rules_legacy_enabled() is True
    assert hold_rule_serve() is False
    assert locked_measure("total revenue") == "outbound_value_myr"
    served = answer_demo_question("What was total revenue?")
    assert isinstance(served, dict)
    assert "Total outbound revenue" in str(served.get("text") or "")
    monkeypatch.setenv("DMS_RULES_LEGACY", "1")
    assert rules_legacy_enabled() is True
    assert hold_rule_serve() is False
    monkeypatch.setenv("DMS_RULES_LEGACY", "true")
    assert rules_legacy_enabled() is True


def test_zero_holds_the_router_and_the_locker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_RULES_LEGACY", "0")
    monkeypatch.delenv("DMS_RULES_OFF", raising=False)
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    assert rules_legacy_enabled() is False
    assert hold_rule_serve() is True
    assert locked_measure("total revenue") is None
    held = answer_demo_question("What was total revenue?")
    assert isinstance(held, dict)
    assert held.get("reason_code") == "rules_off"
    assert "Total outbound revenue" not in str(held.get("text") or "")
    assert held.get("rows") == []


def test_ai_first_still_holds_when_legacy_stays_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_RULES_LEGACY", raising=False)
    monkeypatch.setenv("DMS_AI_FIRST", "1")
    assert rules_legacy_enabled() is True
    assert hold_rule_serve() is True
