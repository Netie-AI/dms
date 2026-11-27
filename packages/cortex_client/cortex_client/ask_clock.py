"""Per-ask monotonic timings and the DMS_ASK_LEAN switches.

The clock always records spans. The envelope gains a ``timings`` key
only when ``DMS_ASK_TIMINGS`` is on (default off), so a flag-off ask is
byte-identical. The same snapshot is published for the armed capture.
The four cuts stay off unless ``DMS_ASK_LEAN`` is on, or that cut's own
variable is on. A cut variable set to 0 wins over the master, so each
cut can be measured alone. DEFER does not skip the second reading.

Cortex's vendored contract has no cancel route. When the cancel cut is
on, a give-up closes the HTTP client and POSTs ``/v1/insights/cancel``.
A 404 is ignored. The Cortex companion owns that route.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

_TRUE = frozenset({"1", "true", "yes", "on"})
LEAN_ENV = "DMS_ASK_LEAN"
LEAN_CANCEL = "DMS_ASK_LEAN_CANCEL"
LEAN_DEFER = "DMS_ASK_LEAN_DEFER"
LEAN_TRIM = "DMS_ASK_LEAN_TRIM"
LEAN_SINGLE = "DMS_ASK_LEAN_SINGLE"
TIMINGS_ENV = "DMS_ASK_TIMINGS"
TRIM_TOKENS_ENV = "DMS_ASK_LEAN_TRIM_TOKENS"
DEFAULT_TRIM_TOKENS = 1200
CANCEL_PATH = "/v1/insights/cancel"

STAGE_NAMES = (
    "retrieve_context",
    "schema_context",
    "ground_defs",
    "tasks_choose",
    "insights_generate",
    "validate_explain",
    "submit_execute",
    "second_reading",
    "reconfirm_build",
    "total",
)


def _on(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in _TRUE
    return os.environ.get(LEAN_ENV, "").strip().lower() in _TRUE


def ask_lean_cancel() -> bool:
    return _on(LEAN_CANCEL)


def ask_lean_defer() -> bool:
    return _on(LEAN_DEFER)


def ask_lean_trim() -> bool:
    return _on(LEAN_TRIM)


def ask_lean_single() -> bool:
    return _on(LEAN_SINGLE)


def ask_timings_enabled() -> bool:
    """Envelope key only. Does not follow ``DMS_ASK_LEAN``."""
    return os.environ.get(TIMINGS_ENV, "").strip().lower() in _TRUE


def trim_token_budget() -> int:
    raw = os.environ.get(TRIM_TOKENS_ENV, "").strip()
    if not raw:
        return DEFAULT_TRIM_TOKENS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TRIM_TOKENS
    if value < 1:
        return DEFAULT_TRIM_TOKENS
    return value


def est_tokens(text: str) -> int:
    """Chars/4. A length estimate, not a tokenizer and not a word list."""
    return (len(text) + 3) // 4


@dataclass
class _Inflight:
    client: Any
    base_url: str
    headers: dict[str, str] | None


class AskClock:
    """One ask. Spans use ``time.perf_counter`` and never store prompt text."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self._sums: dict[str, float] = {}
        self._open: dict[str, list[float]] = defaultdict(list)
        self._intervals: list[tuple[str, float, float]] = []
        self.legs_ms: list[float] = []
        self.abandoned: list[dict[str, str]] = []
        self.prompts: list[dict[str, Any]] = []
        self.skipped: list[str] = []
        self.inflight: list[_Inflight] = []
        self._lock = threading.Lock()
        self._model_inflight = 0
        self._model_holder: int | None = None
        self.deadline: float | None = None

    def begin(self, name: str) -> None:
        self._open[name].append(time.perf_counter())

    def end(self, name: str) -> None:
        stack = self._open.get(name)
        if not stack:
            return
        start = stack.pop()
        end = time.perf_counter()
        ms = max(0.0, (end - start) * 1000.0)
        self._sums[name] = self._sums.get(name, 0.0) + ms
        origin = self.t0
        self._intervals.append((name, (start - origin) * 1000.0, (end - origin) * 1000.0))

    def note_leg(self, ms: float) -> None:
        self.legs_ms.append(max(0.0, ms))

    def note_abandoned(self, leg: str, reason: str) -> None:
        self.abandoned.append({"leg": leg, "reason": reason})

    def note_prompt(self, leg: str, chars: int) -> None:
        n = max(0, int(chars))
        self.prompts.append({"leg": leg, "chars": n, "est_tokens": (n + 3) // 4})

    def note_skipped(self, stage: str) -> None:
        self.skipped.append(stage)

    def acquire_model(self) -> None:
        me = threading.get_ident()
        while True:
            with self._lock:
                if self._model_inflight == 0 or self._model_holder == me:
                    self._model_inflight += 1
                    self._model_holder = me
                    return
            time.sleep(0.005)

    def release_model(self) -> None:
        with self._lock:
            self._model_inflight = max(0, self._model_inflight - 1)
            if self._model_inflight == 0:
                self._model_holder = None

    def snapshot(self) -> dict[str, Any]:
        from cortex_client.compute import recorded_model_calls

        spans = {name: 0.0 for name in STAGE_NAMES if name != "total"}
        for name, ms in self._sums.items():
            spans[name] = round(ms, 3)
        total = max(0.0, (time.perf_counter() - self.t0) * 1000.0)
        spans["total"] = round(total, 3)
        recorded = recorded_model_calls()
        calls = recorded if recorded else len(self.prompts)
        return {
            "spans_ms": spans,
            "intervals_ms": [
                {"stage": name, "start_ms": round(start, 3), "end_ms": round(end, 3)}
                for name, start, end in self._intervals
            ],
            "insights_generate_legs_ms": [round(ms, 3) for ms in self.legs_ms],
            "model_calls": calls,
            "abandoned": list(self.abandoned),
            "prompts": list(self.prompts),
            "skipped": list(self.skipped),
        }


_clock: ContextVar[AskClock | None] = ContextVar("dms_ask_clock", default=None)


def current_clock() -> AskClock | None:
    return _clock.get()


def note_deadline(deadline: float) -> None:
    """Remember the ask's monotonic deadline. Does not change a request by itself."""
    clock = _clock.get()
    if clock is not None:
        clock.deadline = deadline


def deadline_already_passed() -> bool:
    """True only when the cancel cut is on and this ask is past its deadline."""
    if not ask_lean_cancel():
        return False
    clock = _clock.get()
    if clock is None or clock.deadline is None:
        return False
    return time.monotonic() >= clock.deadline


def http_timeout(timeout: float) -> float:
    """HTTP timeout. The cancel cut caps it at the time left on the ask."""
    if not ask_lean_cancel():
        return timeout
    clock = _clock.get()
    if clock is None or clock.deadline is None:
        return timeout
    left = clock.deadline - time.monotonic()
    if left <= 0:
        return 0.05
    return min(timeout, left)


# STUDIO-FIX-C. Off unless the digit 1. The reserve is the existing suggestion
# call (CortexClient.complete uses min(2s, client timeout)), not a row cap.
ASKBACK_ENV = "DMS_DEADLINE_ASKBACK"
ASKBACK_RESERVE_S = 2.0


def deadline_askback_enabled() -> bool:
    """True only when ``DMS_DEADLINE_ASKBACK`` is the digit 1. Unset is off."""
    return os.environ.get(ASKBACK_ENV, "0").strip() == "1"


def generate_budget_left_s() -> float | None:
    """Seconds left for a generate or retry leg, or None when the flag is off.

    None means the caller keeps today's timeout. Zero means do not start a
    leg: the remaining time is the ask-back reserve. A lake query does not
    read this. It still interrupts at the full deadline minus the deliver margin.
    """
    if not deadline_askback_enabled():
        return None
    clock = _clock.get()
    if clock is None or clock.deadline is None:
        return None
    return max(0.0, clock.deadline - time.monotonic() - ASKBACK_RESERVE_S)


def askback_window_open() -> bool:
    """True when the flag is on and only the reserved ask-back time remains.

    Past the full deadline this is false: the named deadline abstain stands.
    """
    if not deadline_askback_enabled():
        return False
    clock = _clock.get()
    if clock is None or clock.deadline is None:
        return False
    now = time.monotonic()
    if now >= clock.deadline:
        return False
    return (clock.deadline - now) <= ASKBACK_RESERVE_S


def ensure_clock() -> Token[AskClock | None] | None:
    """Bind a clock when this turn does not already have one.

    The owner is the caller that receives a token. A nested ask keeps the
    outer clock and gets ``None``.
    """
    if _clock.get() is not None:
        return None
    return _clock.set(AskClock())


def release_clock(token: Token[AskClock | None] | None) -> None:
    if token is None:
        return
    give_up("ask_returned")
    _clock.reset(token)


_published_lock = threading.Lock()
_published: dict[str, Any] | None = None


def attach_timings(env: dict[str, Any] | None) -> dict[str, Any] | None:
    """Publish the snapshot. Write it on the envelope only when asked.

    Flag off leaves the envelope untouched. ``grade52 --armed`` reads
    ``take_published_timings`` from this process.
    """
    clock = _clock.get()
    if clock is None:
        return env
    snap = clock.snapshot()
    global _published
    with _published_lock:
        _published = snap
    if ask_timings_enabled() and isinstance(env, dict):
        env["timings"] = snap
    return env


def take_published_timings() -> dict[str, Any] | None:
    """The latest snapshot, then clear it. None when this ask published nothing."""
    global _published
    with _published_lock:
        snap = _published
        _published = None
        return snap


@contextmanager
def span(name: str) -> Iterator[None]:
    clock = _clock.get()
    if clock is None:
        yield
        return
    clock.begin(name)
    try:
        yield
    finally:
        clock.end(name)


def note_skipped(stage: str) -> None:
    clock = _clock.get()
    if clock is not None:
        clock.note_skipped(stage)


def note_generate_leg(ms: float) -> None:
    clock = _clock.get()
    if clock is not None:
        clock.note_leg(ms)


def note_prompt_text(leg: str, text: str) -> None:
    clock = _clock.get()
    if clock is None:
        return
    clock.note_prompt(leg, len(text))


def note_prompt_body(leg: str, body: Mapping[str, Any]) -> None:
    if _clock.get() is None:
        return
    try:
        raw = json.dumps(body, default=str, sort_keys=True)
    except TypeError:
        raw = ""
    note_prompt_text(leg, raw)


def note_prompt_ctx(ctx: Mapping[str, Any] | None) -> None:
    """Prompt size for a compute callable that never reached HTTP.

    Counts the catalog text. Does not keep the text.
    """
    if not isinstance(ctx, Mapping):
        note_prompt_text("insights_generate", "")
        return
    parts: list[str] = []
    for key in ("schema_context", "governed_definitions", "vq_examples", "sql_vote_prompt"):
        raw = ctx.get(key)
        if isinstance(raw, str):
            parts.append(raw)
    rest = {key: value for key, value in ctx.items() if key not in {
        "schema_context", "governed_definitions", "vq_examples", "sql_vote_prompt"
    }}
    try:
        parts.append(json.dumps(rest, default=str, sort_keys=True))
    except TypeError:
        pass
    note_prompt_text("insights_generate", "\n".join(parts))


def register_inflight(
    client: Any, base_url: str, headers: Mapping[str, str] | None
) -> None:
    clock = _clock.get()
    if clock is None:
        return
    copied = dict(headers) if headers else None
    clock.inflight.append(_Inflight(client=client, base_url=base_url, headers=copied))


def unregister_inflight(client: Any) -> None:
    clock = _clock.get()
    if clock is None:
        return
    clock.inflight = [item for item in clock.inflight if item.client is not client]


def give_up(reason: str) -> None:
    """Close clients still open for this ask. Cancel POST only when that cut is on.

    An empty in-flight list on ``ask_returned`` records nothing: the ask
    ended cleanly. A timeout always records an abandoned call.
    """
    clock = _clock.get()
    items = list(clock.inflight) if clock is not None else []
    if clock is not None:
        clock.inflight = []
    if not items and reason == "ask_returned":
        return
    if clock is not None:
        clock.note_abandoned("insights_generate", reason)
    if not ask_lean_cancel():
        return
    for item in items:
        try:
            item.client.close()
        except Exception:  # noqa: BLE001 - close is best-effort
            pass
        _post_cancel(item.base_url, item.headers, reason)


def _post_cancel(base_url: str, headers: dict[str, str] | None, reason: str) -> None:
    """Best-effort. The contract has no cancel route; 404 is success-shaped."""
    import httpx

    root = str(base_url or "").rstrip("/")
    if not root:
        return
    body = {"reason": reason}
    for _ in range(3):
        try:
            with httpx.Client(timeout=1.0) as http:
                http.post(f"{root}{CANCEL_PATH}", json=body, headers=headers)
            return
        except Exception:  # noqa: BLE001 - companion route may be absent
            time.sleep(0.05)


@contextmanager
def model_slot(*, voting: bool = False) -> Iterator[None]:
    """One model POST at a time for this ask, unless voting is on.

    Flag off yields immediately, including when no clock is bound.
    """
    if voting or not ask_lean_single():
        yield
        return
    clock = _clock.get()
    if clock is None:
        yield
        return
    clock.acquire_model()
    try:
        yield
    finally:
        clock.release_model()
