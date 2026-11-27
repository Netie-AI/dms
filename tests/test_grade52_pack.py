"""GRADE-PACK-01: grade52 --pack grades an external (held-out) pack."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "grade52_pack_t", ROOT / "scripts" / "verify" / "grade52.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["grade52_pack_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def _pack(tmp: Path) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "questions.yaml").write_text(
        "spaces: {finance: cccccccc-cccc-cccc-cccc-cccccccccccc}\n"
        "questions:\n"
        "  - {id: h1, space: finance, expect: l0, min_rows: 1, question: q one}\n"
        "  - {id: h2, space: finance, expect: refuse, trap: true, why: w, question: q two}\n",
        encoding="utf-8",
    )
    (tmp / "oracles.yaml").write_text(
        "oracles:\n  h1: {sql: 'SELECT COUNT(*) AS n FROM inventory'}\n"
        "  h2: {expect: refuse, trap: true, why: w}\n",
        encoding="utf-8",
    )
    return tmp


def test_default_is_curated_52():
    g = _load()
    assert g.EXTERNAL_PACK is None
    assert g.expected_pack_n(10) == 52
    assert g.pack_label() == "curated_ceo"
    with pytest.raises(SystemExit):
        g._check_pack_size(51)


def test_external_pack_size_is_its_own(tmp_path):
    g = _load()
    g.use_pack(_pack(tmp_path / "pack_x"))
    assert g.pack_label() == "pack_x"
    assert g.expected_pack_n(2) == 2
    g._check_pack_size(2)
    assert g.PACK_PATH.parent == (tmp_path / "pack_x").resolve()
    # copy-only still hides only the curated pack, never the external one.
    assert g.CURATED_PACK_DIR == ROOT / "tests" / "fixtures" / "curated_ceo"


def test_pack_inside_tree_refused():
    g = _load()
    with pytest.raises(SystemExit, match="outside the dms tree"):
        g.use_pack(ROOT / "tests" / "fixtures" / "curated_ceo")


def test_pack_missing_files_refused(tmp_path):
    g = _load()
    with pytest.raises(SystemExit, match="questions.yaml"):
        g.use_pack(tmp_path)


def test_pack_flag_needs_armed_or_envelopes(tmp_path):
    g = _load()
    with pytest.raises(SystemExit, match="--pack is for"):
        g.main(["--pack", str(_pack(tmp_path / "p")), "--main"])


def test_external_pack_grades_envelopes(tmp_path, monkeypatch):
    """A 2-question external pack grades end to end and is labelled."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "run"))
    g = _load()
    g.use_pack(_pack(tmp_path / "pack_y"))
    report = g.grade_loaded(
        [
            {
                "id": "h1",
                "env": {
                    "abstained": False,
                    "badge": "L1_GOVERNED_METRIC",
                    "rows": [{"n": 7}],
                    "sql_used": "SELECT 7 AS n",
                    "values": [{"id": "v0", "value": 7.0, "label": "n"}],
                    "audit_id": "11111111-1111-1111-1111-111111111111",
                    "text": "n = {v0}",
                    "ask_mode": "live",
                    "as_of": "2026-11-27T00:00:00Z",
                },
            },
            {"id": "h2", "env": {"abstained": True, "badge": "REFUSED", "rows": []}},
        ],
        mode=g.MODE_STUB_EXEC,
        dms_sha="0" * 40,
    )
    assert report["n"] == 2
    buckets = {c["id"]: c["bucket"] for c in report["cases"]}
    # The served h1 here is not a full envelope, so the strict judge marks it
    # WRONG; what this test pins is that a 2-question pack grades at all.
    assert buckets == {"h1": "WRONG", "h2": "REFUSAL_OK"}
    import json

    artifact = json.loads((tmp_path / "run" / "grade52" / "grade52.json").read_text())
    assert artifact["pack"] == "pack_y"
