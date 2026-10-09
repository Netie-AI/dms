"""GRADER-VALUES-01. Served rows from envelopes, not the submit stub.

The seven buckets are disjoint and add up to 52. Gold-broken sits inside
the non-trap 44. A submit-stub pack is not a score. The exec-SQL stub is
labelled stub-exec. The 23/0/20 fixture below is synthetic gold-as-served,
not the captured flag-off file.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

from grade52 import (  # noqa: E402
    _resolve_mode,
    grade_envelopes_path,
    grade_main,
    load_envelopes,
    path_counts,
    path_group,
    paths_line,
    self_test,
    serve_path,
    summary_line,
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
_MAIN_SHA = "57d85c529aa363825aaa566f12b8822fd76a4215"
_CHECK_INCLUDED = (
    "sha=57d85c529aa363825aaa566f12b8822fd76a4215 pack_gold_served=included "
    "52: correct=23 wrong=0 abstain=20 refusal_ok=8 refusal_wrong=0 "
    "empty_gold=0 gold_broken=1 mode=served"
)
_CHECK_EXCLUDED = (
    "sha=57d85c529aa363825aaa566f12b8822fd76a4215 pack_gold_served=excluded "
    "45: correct=16 wrong=0 abstain=20 refusal_ok=8 refusal_wrong=0 "
    "empty_gold=0 gold_broken=1 mode=served"
)


def _buckets(report: dict) -> int:
    return (
        report["correct"]
        + report["wrong"]
        + report["abstain"]
        + report["refusal_ok"]
        + report["refusal_wrong"]
        + report["empty_gold"]
        + report["gold_broken"]
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def test_grade52_self_test() -> None:
    plants = self_test()
    assert plants["renamed_column"] == "CORRECT"
    assert plants["dropped_row"] == "WRONG"
    assert plants["swapped_order"] == "WRONG"
    assert plants["ordered_shuffle"] == "WRONG"
    assert plants["unordered_shuffle"] == "CORRECT"
    assert plants["extra_column"] == "CORRECT"
    assert plants["duplicated_row"] == "WRONG"
    assert plants["tie_swap"] == "CORRECT"
    assert plants["tie_break"] == "WRONG"
    assert plants["limit_tie"] == "CORRECT"


def test_grade52_served_is_default() -> None:
    assert _resolve_mode(None) == "served"
    with pytest.raises(SystemExit, match="mode"):
        _resolve_mode("replay-only")
    with pytest.raises(SystemExit, match="add up"):
        summary_line(
            {
                "mode": "served",
                "dms_sha": "a" * 40,
                "pack_gold_served": "included",
                "n": 52,
                "correct": 23,
                "wrong": 0,
                "abstain": 21,
                "refusal_ok": 6,
                "refusal_wrong": 2,
                "empty_gold": 0,
                "gold_broken": 1,
            }
        )


def _abstain() -> dict[str, Any]:
    return {
        "badge": "ABSTAIN",
        "route": "abstain",
        "abstained": True,
        "rows": [],
    }


def _served(rows: Any) -> dict[str, Any]:
    return {
        "badge": "L1_GOVERNED_METRIC",
        "route": "governed_metric",
        "abstained": False,
        "rows": rows,
    }


def _write_jsonl(path: Path, records: list[dict[str, Any]], sha: str) -> None:
    header = json.dumps({"dms_sha": sha})
    body = "".join(json.dumps(row) + "\n" for row in records)
    path.write_text(header + "\n" + body, encoding="utf-8")


def test_grade52_serve_path_stamps() -> None:
    """Contradictory stamps are unattributed. Compile with an empty model is rule."""
    contradictory = {
        "badge": "L1_GOVERNED_METRIC",
        "plan_origin": "generate_sql",
        "ladder_rung": "compile",
        "served_model": "some-model",
    }
    assert serve_path(contradictory) == "unattributed"
    assert serve_path({"served_model": "some-model"}) == "unattributed"
    compile_empty = {"ladder_rung": "compile", "served_model": ""}
    assert serve_path(compile_empty) == "compile"
    assert path_group(serve_path(compile_empty)) == "rule"


def test_grade52_check_shaped_envelopes(tmp_path: Path) -> None:
    """Synthetic gold-as-served: 23/0/20 plus 1 gold-broken, traps 8/0."""
    import grade52

    grade52._bootstrap()
    pack = grade52._load_yaml(grade52.PACK_PATH)
    oracles = grade52._load_yaml(grade52.ORACLE_PATH)["oracles"]
    folder = tmp_path / "wh"
    folder.mkdir()
    con, as_of, _path = grade52._open_warehouse(folder)
    certified: list[tuple[str, list]] = []
    traps: list[str] = []
    broken: list[str] = []
    try:
        for question in pack["questions"]:
            qid = str(question["id"])
            oracle = oracles.get(qid)
            oracle_map = oracle if isinstance(oracle, dict) else None
            if grade52._is_trap(question, oracle_map):
                traps.append(qid)
                continue
            sql = grade52._certified_sql(oracle_map)
            if sql is None:
                broken.append(qid)
                continue
            certified.append((qid, grade52._execute(con, sql, as_of)))
    finally:
        con.close()
    assert len(certified) == 43
    assert len(traps) == 8
    assert broken == ["ops_spend_boundary"]
    assert "trap_categoty" not in traps
    by_gold = {qid: rows for qid, rows in certified}
    assert set(_GOLD_SERVED) <= set(by_gold)
    rest = sorted(qid for qid in by_gold if qid not in set(_GOLD_SERVED))
    correct_ids = list(_GOLD_SERVED) + rest[:16]
    abstain_ids = rest[16:]
    assert len(correct_ids) == 23
    assert len(abstain_ids) == 20
    records: list[dict[str, Any]] = []
    for qid in correct_ids:
        records.append({"id": qid, "env": _served(_jsonable(by_gold[qid]))})
    for qid in abstain_ids:
        records.append({"id": qid, "env": _abstain()})
    records.append({"id": broken[0], "env": _abstain()})
    for qid in traps:
        records.append({"id": qid, "env": _abstain()})
    path = tmp_path / "envelopes.jsonl"
    _write_jsonl(path, records, _MAIN_SHA)
    report = grade_envelopes_path(path)
    assert summary_line(report) == _CHECK_INCLUDED
    assert paths_line(path_counts(report["cases"])) == (
        "paths correct ai=0 rule=23 unattributed=0 served ai=0 rule=23 unattributed=0"
    )
    assert report["dms_sha"] == _MAIN_SHA
    assert report["mode"] == "served"
    assert _buckets(report) == 52
    non_trap = (
        report["correct"]
        + report["wrong"]
        + report["abstain"]
        + report["empty_gold"]
        + report["gold_broken"]
    )
    assert non_trap == 44
    assert report["gold_broken"] == 1
    assert report["refusal_ok"] == 8
    assert report["refusal_wrong"] == 0
    by_case = {item["id"]: item for item in report["cases"]}
    for qid in _GOLD_SERVED:
        assert by_case[qid]["bucket"] == "CORRECT"
    assert by_case["ops_spend_boundary"]["bucket"] == "GOLD_BROKEN"
    without = report["without_pack_gold_served"]
    assert summary_line(without) == _CHECK_EXCLUDED
    assert _buckets(without) == 45
    assert report["pack_gold_served_ids"] == _GOLD_SERVED
    served_broken: list[dict[str, Any]] = []
    for row in records:
        if row["id"] == "ops_spend_boundary":
            served_broken.append({"id": row["id"], "env": _served([{"n": 1}])})
        else:
            served_broken.append(row)
    broken_path = tmp_path / "served_broken.jsonl"
    _write_jsonl(broken_path, served_broken, _MAIN_SHA)
    failed = grade_envelopes_path(broken_path)
    assert failed["gold_broken"] == 0
    assert failed["wrong"] == 1
    failed_case = {item["id"]: item for item in failed["cases"]}
    assert failed_case["ops_spend_boundary"]["bucket"] == "WRONG"
    assert failed_case["ops_spend_boundary"]["reason"] == "served on gold-broken"
    folder_out = tmp_path / "dir"
    folder_out.mkdir()
    for row in records:
        (folder_out / f"{row['id']}.json").write_text(
            json.dumps(row["env"]),
            encoding="utf-8",
        )
    (folder_out / "dms_sha").write_text(_MAIN_SHA + "\n", encoding="utf-8")
    loaded, sha = load_envelopes(folder_out)
    assert sha == _MAIN_SHA
    assert {item["id"] for item in loaded} == {row["id"] for row in records}


def test_grade52_sha_is_the_envelopes_commit(tmp_path: Path) -> None:
    body = json.dumps({"id": "x", "env": {"badge": "ABSTAIN", "abstained": True, "rows": []}})
    path = tmp_path / "env.jsonl"
    path.write_text(json.dumps({"dms_sha": "b" * 40}) + "\n" + body + "\n", encoding="utf-8")
    rows, sha = load_envelopes(path)
    assert sha == "b" * 40
    assert rows[0]["id"] == "x"
    bare = tmp_path / "bare.jsonl"
    bare.write_text(body + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="commit missing"):
        grade_envelopes_path(bare)


def test_grade52_missing_rows_raises(tmp_path: Path) -> None:
    path = tmp_path / "env.jsonl"
    path.write_text(
        json.dumps({"id": "x", "env": {"badge": "L0_CERTIFIED"}}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="rows"):
        load_envelopes(path)
    empty = tmp_path / "empty.jsonl"
    empty.write_text(
        json.dumps({"id": "x", "env": {"badge": "ABSTAIN", "rows": []}}) + "\n",
        encoding="utf-8",
    )
    rows, _sha = load_envelopes(empty)
    assert rows[0]["env"]["rows"] == []


def test_grade52_submit_stub_is_not_a_score(capsys: pytest.CaptureFixture[str]) -> None:
    path = ROOT / "tests" / "fixtures" / "ask_guide" / "flag_off_52_f9ffc3e1.json"
    with pytest.raises(SystemExit, match="stub"):
        grade_envelopes_path(path)
    out = capsys.readouterr().out
    assert "mode=stub not a score" in out
    assert "correct=" not in out
    assert "paths served ai=0 rule=24 unattributed=20" in out


def test_grade52_stub_exec_main() -> None:
    """Exec-SQL stub, flags off. Real served rows, not the one-cell submit stub."""
    report = grade_main()
    assert report["mode"] == "stub-exec"
    assert report["dms_sha"] == _MAIN_SHA
    assert report["n"] == 52
    assert _buckets(report) == 52
    assert report["correct"] == 43
    assert report["wrong"] == 0
    assert report["abstain"] == 0
    assert report["refusal_ok"] == 6
    assert report["refusal_wrong"] == 2
    assert report["empty_gold"] == 0
    assert report["gold_broken"] == 1
    assert report["pack_gold_served_ids"] == _GOLD_SERVED
    line = summary_line(report)
    assert line.endswith(
        "52: correct=43 wrong=0 abstain=0 refusal_ok=6 refusal_wrong=2 "
        "empty_gold=0 gold_broken=1 mode=stub-exec"
    )
    assert "not a score" not in line
    assert paths_line(path_counts(report["cases"])) == (
        "paths correct ai=0 rule=23 unattributed=20 "
        "served ai=0 rule=25 unattributed=20"
    )
    without = report["without_pack_gold_served"]
    assert without["pack_gold_served"] == "excluded"
    assert without["n"] == 45
    assert _buckets(without) == 45
    assert without["correct"] == 36
    assert without["wrong"] == 0
    assert without["abstain"] == 0
    assert without["refusal_ok"] == 6
    assert without["refusal_wrong"] == 2
    assert without["gold_broken"] == 1


def test_grade52_source_has_no_provider_names() -> None:
    text = (ROOT / "scripts" / "verify" / "grade52.py").read_text(encoding="utf-8")
    for banned in ("OPENAI", "ANTHROPIC", "AZURE_OPENAI"):
        assert banned not in text
