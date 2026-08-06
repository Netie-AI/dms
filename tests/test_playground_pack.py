"""Playground pack loads and sample data regenerates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_questions_yaml_has_ladder_and_aspiration():
    """The bank must span the whole ladder, including the two aspirational rungs.

    Asserting on ``expect_layer`` coverage rather than on three hand-picked ids:
    the ids are the tweakable half of this file (users rename them while editing
    prompts), the ladder is the half that must not quietly lose a rung. A pack
    that drops L3 or L4/L5 stops testing the thing it exists to test.
    """
    pack = yaml.safe_load((ROOT / "playground" / "questions.yaml").read_text(encoding="utf-8"))
    qs = pack["questions"]
    assert len(qs) >= 10
    layers = {str(q.get("expect_layer") or "").upper() for q in qs}
    missing = {"L0", "L2", "L3", "L4", "L5"} - layers
    assert not missing, f"playground ladder is missing rung(s): {sorted(missing)}"

    ids = {str(q["id"]).lower() for q in qs}
    assert "l3_rag_sum_trap" in ids, "F26 doc-RAG sum trap must stay in the bank"

    seen_ids: set[str] = set()
    for q in qs:
        qid = str(q.get("id") or "")
        assert qid, "every question needs a stable id"
        assert qid not in seen_ids, f"duplicate question id {qid}"
        seen_ids.add(qid)
        assert str(q.get("expect_layer") or "").strip(), f"{qid} missing expect_layer"
        assert str(q.get("note") or "").strip(), f"{qid} missing note"
        assert str(q.get("prompt") or "").strip(), f"{qid} missing prompt"


def test_gen_playground_data_writes_files(tmp_path, monkeypatch):
    # Run generator; it writes under repo playground/data — assert names exist after.
    script = ROOT / "scripts" / "gen_playground_data.py"
    subprocess.check_call([sys.executable, str(script)])
    data = ROOT / "playground" / "data"
    assert (data / "pg_sales.xlsx").is_file()
    assert (data / "pg_shipments.xlsx").is_file()
    assert (data / "pg_returns_policy.md").is_file()
