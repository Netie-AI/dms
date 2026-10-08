"""Replay run mr-20261008-1931-38afd6bd from repo fixtures only.

Missing fixture files fail this module. Nothing is skipped, and nothing is
read from outside the repo. The stub is offline: Cortex ignores DMS retry
fields, so binder questions are invalid SQL and every other question is an
empty generate. It is not a guided leg-2.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from dms_executor.envelope import assert_envelope_valid
from test_gen_empty_ranked_01 import _blob, _executor

_DIR = Path(__file__).resolve().parent / "fixtures" / "gen_empty_ranked_01"
_ORACLES = _DIR / "oracles.json"
_CASES = _DIR / "replay_cases.json"
_ORACLES_SHA256 = "008f5bb1b8ac775a677501937a9cd8c444682986cf8008a04beb9fd27a162a12"
_SOURCE_RUN = "mr-20261008-1931-38afd6bd"


def _read() -> tuple[bytes, dict[str, Any]]:
    assert _ORACLES.is_file(), f"missing fixture {_ORACLES}"
    assert _CASES.is_file(), f"missing fixture {_CASES}"
    doc = json.loads(_CASES.read_text(encoding="utf-8"))
    assert isinstance(doc, dict) and isinstance(doc.get("cases"), list)
    return _ORACLES.read_bytes(), doc


def _num(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _row_sig(rows: list[dict[str, Any]]) -> list[tuple[tuple[str, Any], ...]]:
    return [
        tuple(sorted((str(k), _num(v)) for k, v in row.items()))
        for row in rows
    ]


def _value_sig(rows: list[dict[str, Any]]) -> list[tuple[tuple[str, ...], tuple[float, ...]]]:
    out = []
    for row in rows:
        labels: list[str] = []
        nums: list[float] = []
        for val in row.values():
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                labels.append(str(val))
            else:
                nums.append(float(val))
        out.append((tuple(sorted(labels)), tuple(sorted(nums))))
    return sorted(out)


def test_oracles_sha256_matches_recorded_run() -> None:
    raw, doc = _read()
    assert hashlib.sha256(raw).hexdigest() == _ORACLES_SHA256
    assert doc["oracles_sha256"] == _ORACLES_SHA256
    assert doc["source_run_id"] == _SOURCE_RUN
    assert doc["served_at"] == "38afd6bd"
    assert doc["set_compare_case_ids"] == ["cq_sku_count_by_category_per"]


def test_replay_sixteen_from_repo_fixtures(tmp_path: Path) -> None:
    _raw, doc = _read()
    cases = doc["cases"]
    assert len(cases) == 16
    ontology = [c for c in cases if c["ontology_ranking_correct"]]
    binder = [c for c in cases if c["group"] == "binder"]
    empty = [c for c in cases if c["group"] == "empty"]
    assert len(ontology) == 8
    assert len(binder) == 3
    assert len(empty) == 5
    assert {c["case_id"] for c in ontology} == {c["case_id"] for c in binder + empty}

    exe = _executor(tmp_path)
    seen: list[str] = []
    for case in cases:
        env = exe.live_ask(
            case["question"],
            session_id=f"replay-{case['case_id']}",
            space_id=case["space_id"],
            ask_path="generative",
        )
        assert_envelope_valid(env)
        blob = _blob(env)
        got = list(env["rows"])
        expect = list(case["rows"])
        if case["compare"] == "set":
            # cq_sku_count_by_category_per: sku_count ties at 2.
            assert case["case_id"] in doc["set_compare_case_ids"]
            assert sorted(_row_sig(got)) == sorted(_row_sig(expect))
        elif case["compare"] == "values":
            assert _value_sig(got) == _value_sig(expect)
        else:
            assert _row_sig(got) == _row_sig(expect)
        if case["ontology_ranking_correct"]:
            assert env["abstained"] is False
            assert env["badge"] == "L2_VALIDATED"
            assert env.get("plan_origin") == "ontology_ranking"
            if case["compare"] != "set":
                assert _row_sig(got) == _row_sig(expect)
        if case["group"] == "binder":
            assert "fallback:validate:" in blob
        if case["group"] == "empty":
            assert "fallback:generate_empty" in blob
            if case["compare"] == "set":
                assert sorted(_row_sig(got)) == sorted(_row_sig(expect))
            else:
                assert _row_sig(got) == _row_sig(expect)
        seen.append(case["case_id"])
    assert seen == [c["case_id"] for c in cases]
