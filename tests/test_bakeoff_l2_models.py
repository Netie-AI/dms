"""F46 / EPIC-018 — bakeoff must not promote on badge or latency."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bakeoff_l2_models.py"


def _mod():
    spec = importlib.util.spec_from_file_location("bakeoff_l2_models", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fastest_l2_validated_without_oracle_is_not_promoted() -> None:
    pick = _mod().select_promote_winner
    assert (
        pick(
            [
                {
                    "model": "fast",
                    "badge": "L2_VALIDATED",
                    "abstain": False,
                    "ms": 10,
                },
                {
                    "model": "slow",
                    "badge": "L2_VALIDATED",
                    "abstain": False,
                    "ms": 9999,
                },
            ]
        )
        is None
    )


def test_oracle_precision_beats_faster_wrong_model() -> None:
    pick = _mod().select_promote_winner
    assert (
        pick(
            [
                {
                    "model": "fast_wrong",
                    "badge": "L2_VALIDATED",
                    "abstain": False,
                    "ms": 1,
                    "wrong": 1,
                    "precision_on_answered": 0.5,
                    "coverage": 1.0,
                },
                {
                    "model": "slow_right",
                    "badge": "ABSTAIN",
                    "abstain": True,
                    "ms": 9000,
                    "wrong": 0,
                    "precision_on_answered": 1.0,
                    "coverage": 0.7,
                },
            ]
        )
        == "slow_right"
    )


def test_coverage_breaks_precision_tie_not_latency() -> None:
    pick = _mod().select_promote_winner
    assert (
        pick(
            [
                {
                    "model": "narrow",
                    "ms": 1,
                    "wrong": 0,
                    "precision_on_answered": 1.0,
                    "coverage": 0.4,
                },
                {
                    "model": "wider",
                    "ms": 8000,
                    "wrong": 0,
                    "precision_on_answered": 1.0,
                    "coverage": 0.9,
                },
            ]
        )
        == "wider"
    )
