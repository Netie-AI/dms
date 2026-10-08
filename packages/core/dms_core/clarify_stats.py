"""Ask counters for clarify vs abstain.

A clarify reply is not a served answer. Rates share one denominator so an
evaluation run can print them side by side. Tokens are the clarify step only.

Swap: a metrics backend behind this process counter. Not a sixth port.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

_LOCK = Lock()
_SERVED = 0
_ABSTAIN = 0
_CLARIFY = 0
_PROMPT = 0
_COMPLETION = 0


def reset() -> None:
    global _SERVED, _ABSTAIN, _CLARIFY, _PROMPT, _COMPLETION
    with _LOCK:
        _SERVED = 0
        _ABSTAIN = 0
        _CLARIFY = 0
        _PROMPT = 0
        _COMPLETION = 0


def record_outcome(kind: str) -> None:
    """Count one finished ask. ``clarify`` is not served and not an abstain."""
    global _SERVED, _ABSTAIN, _CLARIFY
    with _LOCK:
        if kind == "clarify":
            _CLARIFY += 1
        elif kind == "abstain":
            _ABSTAIN += 1
        elif kind == "served":
            _SERVED += 1


def record_clarify_tokens(prompt_tokens: int, completion_tokens: int) -> None:
    global _PROMPT, _COMPLETION
    with _LOCK:
        _PROMPT += max(0, int(prompt_tokens))
        _COMPLETION += max(0, int(completion_tokens))


def rates_from_counts(
    *,
    asks: int,
    abstain: int,
    clarify: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> dict[str, Any]:
    """Shared rate shape. ``asks`` is the denominator both rates use."""
    denom = int(asks)
    def _rate(n: int) -> float | None:
        if denom <= 0:
            return None
        return round(n / denom, 4)

    per: float | None = None
    if clarify > 0:
        per = round((int(prompt_tokens) + int(completion_tokens)) / clarify, 4)
    return {
        "asks": denom,
        "abstain": int(abstain),
        "clarify": int(clarify),
        "abstain_rate": _rate(int(abstain)),
        "clarify_rate": _rate(int(clarify)),
        "clarify_prompt_tokens": int(prompt_tokens),
        "clarify_completion_tokens": int(completion_tokens),
        "tokens_per_clarify": per,
    }


def snapshot() -> dict[str, Any]:
    with _LOCK:
        asks = _SERVED + _ABSTAIN + _CLARIFY
        return rates_from_counts(
            asks=asks,
            abstain=_ABSTAIN,
            clarify=_CLARIFY,
            prompt_tokens=_PROMPT,
            completion_tokens=_COMPLETION,
        )
