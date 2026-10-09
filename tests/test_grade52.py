"""GRADER-VALUES-01. Served rows from envelopes, not the submit stub.

The seven buckets are disjoint and add up to 52. Gold-broken sits inside
the non-trap 44. A submit-stub pack is not a score. The exec-SQL stub is
labelled stub-exec.
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
    self_test,
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
_CHECK_LINE = (
    "52: correct=23 wrong=0 abstain=20 refusal_ok=6 refusal_wrong=2 "
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


def test_grade52_check_shaped_envelopes(tmp_path: Path) -> None:
    """23 correct, 20 abstain, 1 gold-broken, 6 ok refusals, 2 wrong refusals."""
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
            if grade52._is_trap(question):
                traps.append(qid)
                continue
            sql = grade52._certified_sql(oracles.get(qid))
            if sql is None:
                broken.append(qid)
                continue
            certified.append((qid, grade52._execute(con, sql, as_of)))
    finally:
        con.close()
    assert len(certified) == 43
    assert len(traps) == 8
    assert len(broken) == 1
    certified.sort(key=lambda item: item[0])
    traps.sort()
    records: list[dict[str, Any]] = []
    for qid, gold in certified[:23]:
        records.append(
            {
                "id": qid,
                "env": {
                    "badge": "L1_GOVERNED_METRIC",
                    "route": "governed_metric",
                    "abstained": False,
                    "rows": _jsonable(gold),
                },
            }
        )
    for qid, _gold in certified[23:]:
        records.append(
            {
                "id": qid,
                "env": {
                    "badge": "ABSTAIN",
                    "route": "abstain",
                    "abstained": True,
                    "rows": [],
                },
            }
        )
    records.append(
        {
            "id": broken[0],
            "env": {
                "badge": "ABSTAIN",
                "route": "abstain",
                "abstained": True,
                "rows": [],
            },
        }
    )
    for qid in traps[:6]:
        records.append(
            {
                "id": qid,
                "env": {
                    "badge": "ABSTAIN",
                    "route": "abstain",
                    "abstained": True,
                    "rows": [],
                },
            }
        )
    for qid in traps[6:]:
        records.append(
            {
                "id": qid,
                "env": {
                    "badge": "L0_CERTIFIED",
                    "route": "sql",
                    "abstained": False,
                    "rows": [{"answered": 1}],
                },
            }
        )
    path = tmp_path / "envelopes.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    report = grade_envelopes_path(path)
    assert summary_line(report).endswith(_CHECK_LINE)
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
    assert report["refusal_ok"] + report["refusal_wrong"] == 8
    without = report["without_pack_gold_served"]
    assert without["pack_gold_served"] == "excluded"
    assert without["n"] == 45
    assert _buckets(without) == 45
    assert report["pack_gold_served_ids"] == _GOLD_SERVED
    folder_out = tmp_path / "dir"
    folder_out.mkdir()
    for row in records:
        (folder_out / f"{row['id']}.json").write_text(
            json.dumps(row["env"]),
            encoding="utf-8",
        )
    loaded = load_envelopes(folder_out)
    assert {item["id"] for item in loaded} == {row["id"] for row in records}


def test_grade52_submit_stub_is_not_a_score(capsys: pytest.CaptureFixture[str]) -> None:
    path = ROOT / "tests" / "fixtures" / "ask_guide" / "flag_off_52_f9ffc3e1.json"
    with pytest.raises(SystemExit, match="stub"):
        grade_envelopes_path(path)
    out = capsys.readouterr().out
    assert "mode=stub not a score" in out
    assert "correct=" not in out


def test_grade52_stub_exec_main() -> None:
    """Exec-SQL stub, flags off. Real served rows, not the one-cell submit stub."""
    report = grade_main()
    assert report["mode"] == "stub-exec"
    assert len(report["dms_sha"]) == 40
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
