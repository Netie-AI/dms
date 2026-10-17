"""Evaluation pick hook for a clarify envelope. Not on the serving path.

The pick model sees the original question text and the option labels only.
It must not receive a gold answer, SQL, expected rows, or option bindings.
``none_fits`` is a miss. The helper refuses to run when the pick model id
is the writer model id.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

NONE_FITS = "none_fits"

PickModel = Callable[[str, list[dict[str, str]]], str]


class SameModelRefused(RuntimeError):
    """Pick model id matches the writer that produced the options."""


def _labels(envelope: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    question = str(envelope.get("original_question") or "")
    options: list[dict[str, str]] = []
    raw = envelope.get("options")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            options.append(
                {
                    "id": str(item.get("id") or ""),
                    "label": str(item.get("label") or ""),
                }
            )
    return question, options


def pick_clarify(
    envelope: dict[str, Any],
    pick_model: PickModel,
    *,
    writer_model_id: str,
    pick_model_id: str,
) -> dict[str, str]:
    """Return ``{outcome, option_id}``. ``outcome`` is ``pick`` or ``miss``.

    Raises ``SameModelRefused`` before the pick model is called when the ids match.
    """
    if str(writer_model_id) == str(pick_model_id):
        raise SameModelRefused(
            "pick model id equals the writer model id; refusing to grade with the writer"
        )
    question, options = _labels(envelope)
    choice = str(pick_model(question, options) or "").strip()
    ids = {row["id"] for row in options if row["id"]}
    if choice == NONE_FITS or choice not in ids:
        return {"outcome": "miss", "option_id": choice or NONE_FITS}
    return {"outcome": "pick", "option_id": choice}
