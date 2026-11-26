"""ARMED-KEEP-FAILED-01: a refused armed run keeps its capture, labelled not a score."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "grade52_failed_t", ROOT / "scripts" / "verify" / "grade52.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["grade52_failed_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def _rec(qid: str) -> dict:
    return {
        "id": qid,
        "env": {"abstained": True, "badge": "ABSTAIN", "rows": []},
        "served": {"insights_fail": "insights_timeout", "route": "abstain"},
    }


def test_failed_capture_header_is_not_a_score(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    g = _load()
    path = g.keep_failed_capture([_rec("a"), _rec("b")], "ai_lane_not_reached", cortex_sha="c" * 40)
    assert path.name == g.FAILED_CAPTURE_NAME
    lines = path.read_text().splitlines()
    head = json.loads(lines[0])
    assert head["mode"] == "failed" and head["not_a_score"] is True
    assert head["n_captured"] == 2 and head["reason"] == "ai_lane_not_reached"
    assert [json.loads(x)["served"]["insights_fail"] for x in lines[1:]] == ["insights_timeout"] * 2


def test_failed_capture_never_grades(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    g = _load()
    path = g.keep_failed_capture([_rec("a")], "x")
    with pytest.raises(SystemExit):
        g.grade_envelopes_path(path)


def test_armed_check_failure_keeps_capture(tmp_path, monkeypatch):
    """armed_replay writes the failed capture, then still refuses."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    g = _load()
    records = [_rec(f"q{i}") for i in range(52)]
    monkeypatch.setattr(g, "armed_preflight", lambda cfg: {})
    monkeypatch.setattr(g, "_armed_minter", lambda cfg: type("M", (), {"close": lambda s: None})())
    monkeypatch.setattr(g, "_armed_ask", lambda *a, **k: records)
    monkeypatch.setattr(g, "_onto_from_data_on", lambda: False)

    def refuse(_records):
        raise SystemExit("grade52 armed: FAIL ai_lane_not_reached")

    monkeypatch.setattr(g, "armed_check", refuse)
    cfg = type("C", (), {"cortex_url": "http://127.0.0.1:1", "cortex_sha": "d" * 40})()
    with pytest.raises(SystemExit, match="ai_lane_not_reached"):
        g.armed_replay("full-tree", cfg=cfg)
    kept = tmp_path / "grade52" / g.FAILED_CAPTURE_NAME
    head = json.loads(kept.read_text().splitlines()[0])
    assert head["n_captured"] == 52 and head["not_a_score"] is True
