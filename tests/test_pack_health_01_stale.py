"""Editing a scoring file does not change the product metric tuple."""

from __future__ import annotations

from pathlib import Path

from dms_executor import demo_pack


def test_scoring_file_edits_are_not_metrics(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    root.mkdir()
    (root / "questions.yaml").write_text(
        "questions:\n  - id: cq_sku_count\n    question: synthetic\n    expect: l0\n",
        encoding="utf-8",
    )
    (root / "oracles.yaml").write_text(
        "oracles:\n  cq_sku_count:\n    sql: SELECT 0 AS planted\n",
        encoding="utf-8",
    )
    assert demo_pack.score_pack_exact_metrics() == ()
    assert demo_pack.curated_l0_question_norms() == frozenset()
    assert demo_pack.curated_pack_status().name == "absent"
