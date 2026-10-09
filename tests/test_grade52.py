"""GRADER-VALUES-01. Self-test plants and the flag-off 52 reproduction.

ORDER BY is wrong only for a top-level ORDER BY. An extra answer column
passes. The same row duplicated on the answer side is wrong.

Flag-off replay on this checkout, values re-executed from ``sql_used``:
42 correct, 2 wrong, 1 abstain, 7 empty-gold, graded 43. The 20 generative
answers match gold after column projection, so they count as correct.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

from grade52 import grade_main, self_test  # noqa: E402


def test_grade52_self_test() -> None:
    plants = self_test()
    assert plants["renamed_column"] == "CORRECT"
    assert plants["dropped_row"] == "WRONG"
    assert plants["swapped_order"] == "WRONG"
    assert plants["ordered_shuffle"] == "WRONG"
    assert plants["unordered_shuffle"] == "CORRECT"
    assert plants["extra_column"] == "CORRECT"
    assert plants["duplicated_row"] == "WRONG"


def test_grade52_main_reproduction() -> None:
    report = grade_main()
    assert report["n"] == 52
    assert report["graded"] == 43
    assert report["correct"] == 42
    assert report["wrong"] == 2
    assert report["abstain"] == 1
    assert report["empty_gold"] == 7
