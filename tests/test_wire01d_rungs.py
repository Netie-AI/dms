"""WIRE-01d: grade52 counts a model-SQL ladder rung as AI, Cortex's or DMS's own.

Cortex 1.5.0 stamps ``plan_sql_rung`` (``plan``, ``error-fed-retry-<N>``,
``stronger-model``, ``reconfirm``); WIRE-01c copies it into ``ladder_rung``.
A rung that served counts as AI only beside a served model, and never beside
a rule stamp. ``reconfirm`` is that ladder's refusal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import grade52  # noqa: E402

_COPY_ONLY = ROOT / "tests" / "fixtures" / "ask_guide" / "copy_only_57d85c52.jsonl"
_RUNGS = (
    "plan",
    "error-fed-retry-1",
    "error-fed-retry-2",
    "stronger-model",
    "self_correct",
    "richer_context",
    "stronger_tier",
)


@pytest.fixture(autouse=True)
def _artifacts_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner"))


def _restamped(path: Path, **stamps: Any) -> Path:
    """The copy-only capture with every served answer restamped as a Cortex rung."""
    lines = _COPY_ONLY.read_text(encoding="utf-8").splitlines()
    out = [lines[0]]
    for n, line in enumerate(row for row in lines[1:] if row.strip()):
        record = json.loads(line)
        env = record["env"]
        if not env.get("abstained"):
            for key in ("plan_origin", "plan_source", "ladder_rung", "served_model"):
                env.pop(key, None)
            env["badge"] = "L2_VALIDATED"
            env["ladder_rung"] = _RUNGS[n % len(_RUNGS)]
            env["served_model"] = "cortex-model"
            env.update(stamps)
        out.append(json.dumps(record))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def test_cortex_rungs_with_a_served_model_are_ai(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _restamped(tmp_path / "cortex.jsonl")
    assert grade52.main(["--envelopes", str(run)]) == 0
    printed = capsys.readouterr().out
    assert "paths correct ai=16 rule=0" in printed, printed
    assert "ai served=16 wrong=0" in printed, printed
    assert "unattributed served=0" in printed, printed


def test_a_cortex_rung_without_a_served_model_is_unattributed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _restamped(tmp_path / "nomodel.jsonl", served_model="")
    grade52.main(["--envelopes", str(run)])
    printed = capsys.readouterr().out
    assert "ai served=0" in printed, printed
    assert "unattributed served=16" in printed, printed


def test_a_rule_badge_beside_a_cortex_rung_is_unattributed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _restamped(tmp_path / "mixed.jsonl", badge="L1_GOVERNED_METRIC")
    grade52.main(["--envelopes", str(run)])
    printed = capsys.readouterr().out
    assert "ai served=0" in printed, printed
    assert "unattributed served=16" in printed, printed


@pytest.mark.parametrize("rung", ["reconfirm", "error-fed-retry-0", "error-fed-retry-x", "planned"])
def test_other_rung_text_is_never_ai(rung: str) -> None:
    env = {"badge": "L2_VALIDATED", "ladder_rung": rung, "served_model": "cortex-model"}
    assert grade52.serve_path(env) == "unattributed"


def test_generate_sql_origin_beside_a_cortex_rung_is_ai() -> None:
    env = {
        "badge": "L2_VALIDATED",
        "plan_origin": "generate_sql",
        "ladder_rung": "stronger-model",
        "served_model": "cortex-model",
    }
    assert grade52.serve_path(env) == "ai"
    assert grade52.path_group(grade52.serve_path(env)) == "ai"
