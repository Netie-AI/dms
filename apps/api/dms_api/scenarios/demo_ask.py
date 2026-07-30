"""Deprecated path — chat uses AskServicePort.demo_ask via wiring."""

from __future__ import annotations

from typing import Any


def demo_answer_envelope(*, question: str, space_id: str | None) -> dict[str, Any]:
    raise RuntimeError("use AskServicePort.demo_ask — not this module")
