"""Typed outcomes for the CLI. A WRONG count is not an exit code.

The five outcomes are the only map. Unsure is clarify or reconfirm when the
envelope says so. This module does not read question text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OUTCOMES = ("served", "clarify", "reconfirm", "refused", "error")

# Process status. Served is 0. The others are distinct and not a WRONG guess.
EXIT = {
    "served": 0,
    "clarify": 2,
    "reconfirm": 3,
    "refused": 4,
    "error": 1,
}


def typed_outcome(env: Mapping[str, Any]) -> str:
    """Outcome from envelope fields. Does not read the question."""
    current = env.get("outcome")
    if current in OUTCOMES:
        return str(current)
    if isinstance(env.get("clarify"), dict) or env.get("route") == "needs_clarification":
        return "clarify"
    if isinstance(env.get("reconfirm"), dict):
        return "reconfirm"
    if env.get("abstained") is True or str(env.get("badge") or "").upper() == "ABSTAIN":
        return "refused"
    if env.get("route") or env.get("served_route") or env.get("badge"):
        return "served"
    return "error"


def route_stamp(env: Mapping[str, Any]) -> str | None:
    """Served route, then route. Empty strings count as missing."""
    for key in ("served_route", "route"):
        value = env.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def exit_code(env: Mapping[str, Any]) -> int:
    """Map the typed outcome. ``wrong`` is ignored on purpose."""
    outcome = typed_outcome(env)
    return EXIT[outcome]
