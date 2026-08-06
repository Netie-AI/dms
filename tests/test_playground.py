"""Playground dry-run loads the founder question bank."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_playground_dry_lists_questions() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "playground_ask.py"), "--dry"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "L2_empty_filter_beta" in proc.stdout or "l2_encoding_beta" in proc.stdout
    assert "L3_rag_sum_trap" in proc.stdout or "l3_rag_sum_trap" in proc.stdout


def test_playground_data_generator() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_playground_data.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = ROOT / "playground" / "data"
    assert (data / "pg_sales.xlsx").is_file()
    assert (data / "pg_shipments.xlsx").is_file()
