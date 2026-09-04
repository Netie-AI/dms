"""EPIC-018 CI-ACCURACY (F42) — stack-free oracle CLI in pytest / CI.

This is not a live ask. It only proves ``score_answers.py --oracle-only``
recomputes hostile-pack oracles and can fail (R-0007).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "score_answers.py"
DOCS = ROOT / "tests" / "fixtures" / "hostile_score"


def test_score_answers_oracle_only_cli_passes() -> None:
    assert SCRIPT.is_file()
    assert DOCS.is_dir()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--docs", str(DOCS), "--oracle-only"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "F32 scope-trap oracles distinct: True" in proc.stdout


def test_score_answers_oracle_only_fails_when_workbook_missing(
    tmp_path: Path,
) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--docs", str(tmp_path), "--oracle-only"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAIL missing workbook" in proc.stdout
