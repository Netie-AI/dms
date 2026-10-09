"""GRADER-VALUES-01. Self-test plants and the labelled flag-off lines.

ORDER BY is wrong only for a top-level ORDER BY. An extra answer column
passes. The same row duplicated on the answer side is wrong. A served
answer on empty gold stays in that bucket. A trap is a refusal, never
empty gold. Served is the default score. Replay-only is not a score.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

from grade52 import (  # noqa: E402
    _resolve_mode,
    grade_main,
    self_test,
    summary_line,
)

_GOLD_SERVED = [
    "cq_sales_top3_volume",
    "cq_sku_count",
    "cq_sku_count_by_category",
    "cq_supplier_ranking",
    "ops_sku_count",
    "ops_sku_count_by_category",
    "trap_categoty",
]


def test_grade52_self_test() -> None:
    plants = self_test()
    assert plants["renamed_column"] == "CORRECT"
    assert plants["dropped_row"] == "WRONG"
    assert plants["swapped_order"] == "WRONG"
    assert plants["ordered_shuffle"] == "WRONG"
    assert plants["unordered_shuffle"] == "CORRECT"
    assert plants["extra_column"] == "CORRECT"
    assert plants["duplicated_row"] == "WRONG"


def _buckets(report: dict) -> int:
    return (
        report["correct"]
        + report["wrong"]
        + report["abstain"]
        + report["refusal_ok"]
        + report["refusal_wrong"]
        + report["empty_gold"]
        + report["gold_broken"]
    )


def test_grade52_served_is_default() -> None:
    assert _resolve_mode(None) == "served"
    with pytest.raises(SystemExit, match="mode"):
        _resolve_mode("replay-of-captured-envelopes")
    with pytest.raises(SystemExit, match="add up"):
        summary_line(
            {
                "mode": "served",
                "dms_sha": "a" * 40,
                "pack_gold_served": "included",
                "n": 52,
                "correct": 23,
                "wrong": 0,
                "abstain": 21,
                "refusal_ok": 6,
                "refusal_wrong": 2,
                "empty_gold": 0,
                "gold_broken": 1,
            }
        )


def test_grade52_served_main_flag_off() -> None:
    """Envelope rows, no sql_used re-exec. Stub cells do not match gold."""
    report = grade_main(None)
    assert report["mode"] == "served"
    assert len(report["dms_sha"]) == 40
    assert report["pack_gold_served"] == "included"
    assert report["pack_gold_served_ids"] == _GOLD_SERVED
    assert report["n"] == 52
    assert _buckets(report) == 52
    assert report["correct"] == 0
    assert report["wrong"] == 42
    assert report["abstain"] == 1
    assert report["refusal_ok"] == 6
    assert report["refusal_wrong"] == 2
    assert report["empty_gold"] == 0
    assert report["gold_broken"] == 1
    line = summary_line(report)
    assert line.endswith("mode=served")
    assert "not a score" not in line
    without = report["without_pack_gold_served"]
    assert without["pack_gold_served"] == "excluded"
    assert without["mode"] == "served"
    assert without["n"] == 45
    assert _buckets(without) == 45
    assert without["correct"] == 0
    assert without["wrong"] == 36
    assert without["abstain"] == 0
    assert without["refusal_ok"] == 6
    assert without["refusal_wrong"] == 2
    assert without["empty_gold"] == 0
    assert without["gold_broken"] == 1


def test_grade52_replay_only_is_not_a_score() -> None:
    report = grade_main("replay-only")
    assert report["mode"] == "replay-only"
    assert report["n"] == 52
    assert _buckets(report) == 52
    assert report["correct"] == 42
    assert report["wrong"] == 0
    assert report["abstain"] == 1
    assert report["refusal_ok"] == 6
    assert report["refusal_wrong"] == 2
    assert report["empty_gold"] == 0
    assert report["gold_broken"] == 1
    assert report["pack_gold_served_ids"] == _GOLD_SERVED
    line = summary_line(report)
    assert "mode=replay-only" in line
    assert line.endswith("not a score")
    without = report["without_pack_gold_served"]
    assert without["n"] == 45
    assert _buckets(without) == 45
    assert without["correct"] == 36
    assert without["wrong"] == 0
    assert without["abstain"] == 0
    assert without["pack_gold_served"] == "excluded"


def test_grade52_source_has_no_provider_names() -> None:
    text = (ROOT / "scripts" / "verify" / "grade52.py").read_text(encoding="utf-8")
    for banned in ("OPENAI", "ANTHROPIC", "AZURE_OPENAI"):
        assert banned not in text
