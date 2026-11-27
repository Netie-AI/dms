"""STUDIO-FIX-C. Reserve ask-back time when DMS_DEADLINE_ASKBACK=1.

Flag off keeps today's serving-deadline abstain. The flag does not read
the question text and does not move the lake-query interrupt.
"""

from __future__ import annotations

import time

from cortex_client.ask_clock import (
    ASKBACK_RESERVE_S,
    AskClock,
    askback_window_open,
    deadline_askback_enabled,
    ensure_clock,
    generate_budget_left_s,
    note_deadline,
    release_clock,
)


def test_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("DMS_DEADLINE_ASKBACK", raising=False)
    assert deadline_askback_enabled() is False
    assert generate_budget_left_s() is None
    assert askback_window_open() is False
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + 1.0)
        assert generate_budget_left_s() is None
        assert askback_window_open() is False
    finally:
        release_clock(token)


def test_flag_zero_matches_unset(monkeypatch) -> None:
    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "0")
    assert deadline_askback_enabled() is False
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + ASKBACK_RESERVE_S)
        assert generate_budget_left_s() is None
    finally:
        release_clock(token)


def test_reserve_stops_a_new_generate_leg_and_opens_the_window(monkeypatch) -> None:
    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "1")
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + ASKBACK_RESERVE_S + 5.0)
        left = generate_budget_left_s()
        assert left is not None and left > 4.0
        assert askback_window_open() is False
        note_deadline(time.monotonic() + (ASKBACK_RESERVE_S / 2))
        assert generate_budget_left_s() == 0.0
        assert askback_window_open() is True
        note_deadline(time.monotonic() - 1.0)
        assert generate_budget_left_s() == 0.0
        assert askback_window_open() is False
    finally:
        release_clock(token)


def test_lake_interrupt_stays_on_the_full_deadline(monkeypatch) -> None:
    """A query that already holds a connection is not cut at the reserve."""
    from dms_executor.lake_registry import AskControl

    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "1")
    monkeypatch.delenv("DMS_LAKE_SERVING_DEADLINE_DELIVER_S", raising=False)
    ctrl = AskControl()
    # 2.5s left. Deliver margin is 0.90s, so the lake interrupt is not due.
    # Subtracting the 2s reserve as well would make it due.
    ctrl.deadline = time.monotonic() + 2.5
    assert ctrl._interrupt_due() is False
    ctrl.deadline = time.monotonic() + 0.2
    assert ctrl._interrupt_due() is True


def test_helpers_do_not_read_question_text() -> None:
    import inspect

    from cortex_client import ask_clock

    src = inspect.getsource(ask_clock.deadline_askback_enabled)
    src += inspect.getsource(ask_clock.generate_budget_left_s)
    src += inspect.getsource(ask_clock.askback_window_open)
    assert "question" not in src
    assert "re.compile" not in src
    assert isinstance(AskClock, type)
