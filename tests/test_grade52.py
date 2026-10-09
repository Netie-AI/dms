"""GRADER-VALUES-01. Self-test plants and the labelled flag-off line.

ORDER BY is wrong only for a top-level ORDER BY. An extra answer column
passes. The same row duplicated on the answer side is wrong. A served
answer on empty gold stays in that bucket. A score line without a mode
is refused. A 20-wide ambiguous map is unmappable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

from grade52 import (  # noqa: E402
    grade_main,
    main,
    self_test,
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


def test_grade52_mode_required() -> None:
    with pytest.raises(SystemExit, match="mode"):
        main(["--main"])


def test_grade52_main_reproduction() -> None:
    report = grade_main("replay-of-captured-envelopes")
    assert report["mode"] == "replay-of-captured-envelopes"
    assert len(report["dms_sha"]) == 40
    assert report["pack_gold_served"] == "included"
    assert report["pack_gold_served_ids"] == _GOLD_SERVED
    assert report["n"] == 52
    assert report["graded"] == 43
    assert report["correct"] == 42
    assert report["wrong"] == 0
    assert report["abstain"] == 1
    assert report["empty_gold"] == 9
    assert report["empty_gold_abstained"] == 7
    assert report["empty_gold_served"] == {"1": 2}
    without = report["without_pack_gold_served"]
    assert without["pack_gold_served"] == "excluded"
    assert without["mode"] == report["mode"]
    assert without["dms_sha"] == report["dms_sha"]
    assert without["n"] == 45
    assert without["graded"] == 36
    assert without["correct"] == 36
    assert without["wrong"] == 0
    assert without["abstain"] == 0
    assert without["empty_gold"] == 9


def test_grade52_source_has_no_provider_names() -> None:
    text = (ROOT / "scripts" / "verify" / "grade52.py").read_text(encoding="utf-8")
    for banned in ("OPENAI", "ANTHROPIC", "AZURE_OPENAI"):
        assert banned not in text
