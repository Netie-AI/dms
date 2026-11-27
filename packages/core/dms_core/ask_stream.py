"""Live ask stages for Studio. Flag ``DMS_ASK_STREAM``, default off.

Off, every call returns immediately and nothing is stored. On, one in-process
slot per ask id holds ``{stage, t_ms, status}`` only. The publisher accepts
ladder stage names and a ``rung1``..``rung9`` token. SQL, row values, and
relation names are not fields on the event, so they cannot be copied in.

ponytail: the slots live in this process. A second API worker would not see
them. Move the slot to a queue the ask process and the SSE process share
before DMS runs more than one worker.
"""

from __future__ import annotations

import os
import threading
import time
from contextvars import ContextVar, Token
from typing import Any

FLAG = "DMS_ASK_STREAM"
_TRUE = frozenset({"1", "true", "yes", "on"})
_MAX_EVENTS = 64
_MAX_SLOTS = 64
_MAX_WAIT_S = 300.0

#: Ladder names already recorded by the ask, and the public token we emit.
#: Keys are those names, not question text.
_LADDER = {
    "retrieve_context": "retrieve",
    "retrieve": "retrieve",
    "schema_context": "plan",
    "ground_defs": "plan",
    "plan": "plan",
    "insights_generate": "generate",
    "generate": "generate",
    "validate_explain": "validate",
    "validate": "validate",
    "sql_vote": "vote",
    "second_reading": "vote",
    "vote": "vote",
    "reconfirm_build": "reconfirm",
    "reconfirm": "reconfirm",
}
_PUBLIC = frozenset(_LADDER.values()) | {"retry"}
_STATUS = frozenset({"started", "done", "failed", "cancelled"})

_current: ContextVar[str | None] = ContextVar("dms_ask_stream_id", default=None)
_lock = threading.Lock()
_slots: dict[str, _Slot] = {}


def stream_enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in _TRUE


def valid_ask_id(value: object) -> bool:
    if not isinstance(value, str) or len(value) < 8 or len(value) > 64:
        return False
    return all(ch.isalnum() or ch == "-" for ch in value)


def _rung_token(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 5 or not value.startswith("rung"):
        return None
    digit = value[4]
    if digit < "1" or digit > "9":
        return None
    return value


class _Slot:
    def __init__(self, t0: float) -> None:
        self.t0 = t0
        self.events: list[dict[str, str | int]] = []
        self.cond = threading.Condition()
        self.done = False

    def add(self, stage: str, status: str) -> None:
        event: dict[str, str | int] = {
            "stage": stage,
            "t_ms": max(0, int((time.perf_counter() - self.t0) * 1000)),
            "status": status,
        }
        with self.cond:
            if self.done or len(self.events) >= _MAX_EVENTS:
                return
            self.events.append(event)
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()

    def cancel(self) -> None:
        with self.cond:
            if self.done or not self.events:
                return
            stage = self.events[-1]["stage"]
        if isinstance(stage, str) and stage in _PUBLIC:
            self.add(stage, "cancelled")


def _prune(now: float) -> None:
    dead = [key for key, slot in _slots.items() if slot.done and now - slot.t0 > 30]
    for key in dead:
        _slots.pop(key, None)
    if len(_slots) <= _MAX_SLOTS:
        return
    oldest = sorted(_slots.items(), key=lambda item: item[1].t0)
    for key, _slot in oldest[: len(_slots) - _MAX_SLOTS]:
        _slots.pop(key, None)


def _open(ask_id: str) -> _Slot:
    with _lock:
        _prune(time.perf_counter())
        slot = _slots.get(ask_id)
        if slot is None:
            slot = _Slot(time.perf_counter())
            _slots[ask_id] = slot
        return slot


def bind_ask(ask_id: object) -> Token[str | None] | None:
    """Bind the ask id on this thread. None when the flag is off or the id is not a token."""
    if not stream_enabled() or not valid_ask_id(ask_id):
        return None
    assert isinstance(ask_id, str)
    _open(ask_id)
    return _current.set(ask_id)


def finish_ask(token: Token[str | None] | None) -> None:
    ask_id = _current.get()
    if isinstance(ask_id, str):
        slot = _slots.get(ask_id)
        if slot is not None:
            slot.finish()
    if token is not None:
        _current.reset(token)


def mark_cancelled() -> None:
    """Status ``cancelled`` on the last public stage. No-op when nothing was emitted."""
    try:
        if not stream_enabled():
            return
        ask_id = _current.get()
        if not isinstance(ask_id, str):
            return
        slot = _slots.get(ask_id)
        if slot is not None:
            slot.cancel()
    except Exception:
        return


def _emit(stage: str, status: str) -> None:
    if stage not in _PUBLIC:
        return
    if status not in _STATUS and _rung_token(status) is None:
        return
    ask_id = _current.get()
    if not isinstance(ask_id, str):
        return
    slot = _slots.get(ask_id)
    if slot is None:
        return
    slot.add(stage, status)


def note_stage(stage: str, status: str = "done") -> None:
    """Record one ladder stage. Unknown names are dropped. A bad status becomes ``done``."""
    try:
        if not stream_enabled():
            return
        public = _LADDER.get(stage)
        if public is None:
            return
        if status not in _STATUS:
            status = "done"
        _emit(public, status)
    except Exception:
        return


def note_rungs(attempts: object) -> None:
    """Emit ``retry`` / ``rungN`` from attempt records. Reads the rung token only."""
    try:
        if not stream_enabled() or not isinstance(attempts, list):
            return
        for item in attempts:
            if not isinstance(item, dict):
                continue
            token = _rung_token(item.get("rung"))
            if token is None:
                continue
            _emit("retry", token)
    except Exception:
        return


def wait_events(ask_id: str, index: int, timeout: float) -> tuple[list[dict[str, Any]], bool]:
    """Events after ``index``, and whether the ask has finished."""
    if not valid_ask_id(ask_id):
        return [], True
    slot = _open(ask_id)
    if time.perf_counter() - slot.t0 > _MAX_WAIT_S:
        slot.finish()
    with slot.cond:
        if index >= len(slot.events) and not slot.done:
            slot.cond.wait(timeout=timeout)
        batch = [dict(event) for event in slot.events[index:]]
        return batch, slot.done


def clear_asks() -> None:
    """Test helper. Drops every slot."""
    with _lock:
        _slots.clear()
    _current.set(None)
