"""ASK-CLARIFY-01 pick hook. The pick model is not the writer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from clarify_pick import SameModelRefused, pick_clarify  # noqa: E402


def _envelope() -> dict:
    return {
        "status": "clarify",
        "original_question": "show qxalpha771 and qxbeta771",
        "question": "Which measure?",
        "sql_used": "SELECT 1",
        "rows": [{"gold": 1}],
        "options": [
            {
                "id": "opt_a",
                "label": "use-qxalpha771",
                "binding": {"kind": "measure", "name": "qxalpha771", "table": "qxalpha_fact"},
            },
            {
                "id": "opt_b",
                "label": "use-qxbeta771",
                "binding": {"kind": "measure", "name": "qxbeta771"},
            },
        ],
    }


def test_pick_model_sees_only_question_and_labels() -> None:
    seen: dict = {}

    def pick(question: str, options: list[dict[str, str]]) -> str:
        seen["question"] = question
        seen["options"] = options
        return "opt_a"

    got = pick_clarify(
        _envelope(),
        pick,
        writer_model_id="writer-a",
        pick_model_id="picker-b",
    )
    assert got == {"outcome": "pick", "option_id": "opt_a"}
    assert seen["question"] == "show qxalpha771 and qxbeta771"
    blob = str(seen)
    assert "SELECT" not in blob
    assert "gold" not in blob
    assert "binding" not in blob
    assert "qxalpha_fact" not in blob
    assert seen["options"] == [
        {"id": "opt_a", "label": "use-qxalpha771"},
        {"id": "opt_b", "label": "use-qxbeta771"},
    ]


def test_pick_same_model_is_refused() -> None:
    def pick(_question: str, _options: list[dict[str, str]]) -> str:
        raise AssertionError("pick model must not be called")

    with pytest.raises(SameModelRefused):
        pick_clarify(
            _envelope(),
            pick,
            writer_model_id="same-model",
            pick_model_id="same-model",
        )


def test_pick_none_fits_is_a_miss() -> None:
    def pick(_question: str, _options: list[dict[str, str]]) -> str:
        return "none_fits"

    got = pick_clarify(
        _envelope(),
        pick,
        writer_model_id="writer-a",
        pick_model_id="picker-b",
    )
    assert got == {"outcome": "miss", "option_id": "none_fits"}
