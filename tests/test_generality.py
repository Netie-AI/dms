"""Generality harness. Synthetic sqlite only. No held-out pack and no provider call.

Gold numbers come from executing SQL. A stored answer and a stored row list
are not the grade.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "fde"))
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import generality as g  # noqa: E402

SLICE = ROOT / "tests" / "fixtures" / "generality" / "slice.json"


def _row(report: dict, pack: str, row: str) -> dict:
    found = [item for item in report["rows"] if item["pack"] == pack and item["row"] == row]
    assert len(found) == 1
    return found[0]


def _case(row: dict, qid: str) -> dict:
    found = [item for item in row["cases"] if item["id"] == qid]
    assert len(found) == 1
    return found[0]


def test_flag_is_only_the_digit_1() -> None:
    assert g.enabled({}) is False
    assert g.enabled({g.FLAG: ""}) is False
    assert g.enabled({g.FLAG: "0"}) is False
    assert g.enabled({g.FLAG: "true"}) is False
    assert g.enabled({g.FLAG: "yes"}) is False
    assert g.enabled({g.FLAG: " 1 "}) is True
    assert g.digit_one({"DMS_X": "true", "DMS_Y": "1", "DMS_Z": "0"}) == {"DMS_Y": "1"}
    for _name, flags in g.profiles():
        assert set(flags.values()) <= {"1"}


def test_control_body_is_the_bird_baseline() -> None:
    from bench.bird.baseline import baseline_body

    body = g.control_request("How many rows?", "CREATE TABLE item (n INTEGER)")
    assert body == baseline_body("How many rows?", "CREATE TABLE item (n INTEGER)")
    assert body["role"] == "sql"
    assert "model" not in body and "provider" not in body
    dms = g.dms_request("How many rows?")
    assert list(dms) == ["question"]


def test_offline_slice_scoreboard(tmp_path: Path) -> None:
    report = g.build_offline(SLICE, tmp_path / "work")
    assert report["mode"] == "offline-slice"
    assert report["complete"] is True
    assert report["full_packs"] is False
    assert report["not_pass"] is True
    assert report["sha"]
    assert {pack["id"] for pack in report["packs"]} == {
        "bird-slice",
        "everyday-slice",
        "pharmacy-slice",
        "harborline-slice",
    }
    assert all(pack["full"] is False and pack["status"] == "slice" for pack in report["packs"])
    bird = next(pack for pack in report["packs"] if pack["id"] == "bird-slice")
    assert bird["n_questions"] == 5
    assert bird["n_databases"] == 2

    expected = {
        "control": dict(correct=1, wrong=1, abstain=1, unsafe=1, rule_served=0, correct_alt=1),
        "dms:off": dict(correct=0, wrong=1, abstain=1, unsafe=1, rule_served=1, correct_alt=1),
        "dms:ai-first": dict(correct=2, wrong=1, abstain=1, unsafe=1, rule_served=0, correct_alt=0),
        "dms:DMS_AI_FIRST": dict(
            correct=1, wrong=0, abstain=2, unsafe=1, rule_served=1, correct_alt=0
        ),
        "dms:DMS_ASK_LADDER": dict(
            correct=0, wrong=1, abstain=2, unsafe=1, rule_served=1, correct_alt=0
        ),
        "dms:DMS_CLOOP_B": dict(
            correct=0, wrong=1, abstain=2, unsafe=1, rule_served=1, correct_alt=0
        ),
        "dms:DMS_RECONFIRM_RUN_HELD": dict(
            correct=1, wrong=1, abstain=2, unsafe=1, rule_served=0, correct_alt=0
        ),
    }
    for pack in report["packs"]:
        for name, counts in expected.items():
            row = _row(report, pack["id"], name)
            assert row["status"] == "scored"
            assert row["n"] == 5
            for key, value in counts.items():
                assert row[key] == value, (pack["id"], name, key)
            assert row["failure"] == row["wrong"] + row["unsafe"] + row["rule_served"]
            assert sum(row[key] for key in g.BUCKETS) == row["n"]
            assert row["p50_ms"] == 30
            assert row["p95_ms"] == 100
            assert row["cost_usd"] == 0.005
            assert _case(row, "q_match")["bucket"] == (
                "correct" if counts["correct"] and name != "dms:off" else row["cases"][0]["bucket"]
            )

    first = _row(report, "bird-slice", "dms:DMS_AI_FIRST")
    assert _case(first, "q_match")["bucket"] == "correct"
    ruled = _row(report, "bird-slice", "dms:off")
    assert _case(ruled, "q_match")["bucket"] == "rule_served"
    assert _case(ruled, "q_match")["bucket"] != "correct"

    code = g.main(["offline", "--out", str(tmp_path / "out"), "--slice", str(SLICE)])
    assert code == 0
    text = (tmp_path / "out" / "generality.md").read_text(encoding="utf-8")
    blob = (tmp_path / "out" / "generality.json").read_text(encoding="utf-8")
    assert "Not PASS." in text
    assert "Not a DMS measurement." in text
    assert "rule-served" in text
    assert "\u2014" not in text and "\u2013" not in text
    assert "999" not in text and "999" not in blob


def test_unsafe_sql_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "item.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE item (n INTEGER)")
    con.execute("INSERT INTO item (n) VALUES (1), (3)")
    con.commit()
    con.close()
    spec = g.eval_pack.SourceSpec(kind="sqlite", database=str(path))
    with g.eval_pack.open_source(spec) as raw:
        body = g.eval_pack.tie_aware(
            raw, "SELECT COUNT(*) FROM item", ordered=False, dialect="sqlite"
        )
        item = {"id": "q", "db_id": "main", "dialect": "sqlite", "alt": [], **body}
        bucket = g.classify_case(
            item, {"decision": "answer", "sql": "DELETE FROM item"}, raw, control=True
        )
        left = raw.execute("SELECT COUNT(*) FROM item").fetchone()
    assert bucket == "unsafe"
    assert left == (2,)


def test_harborline_ignores_the_stored_answer(tmp_path: Path) -> None:
    path = tmp_path / "gold.json"
    path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "id": "q01",
                        "buyer": "How many rows are in item?",
                        "sql": "SELECT COUNT(*) FROM item",
                        "answer": "999",
                        "rows": [{"n": 999}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = g.load_harborline_questions(json.loads(path.read_text(encoding="utf-8")))
    assert loaded == [
        {
            "id": "q01",
            "question": "How many rows are in item?",
            "gold_sql": "SELECT COUNT(*) FROM item",
        }
    ]
    assert "999" not in json.dumps(loaded)
    db = tmp_path / "item.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE item (n INTEGER)")
    con.execute("INSERT INTO item (n) VALUES (1)")
    con.commit()
    con.close()
    questions, cons, dialect, closer = g.open_harborline(path, db, None)
    try:
        assert dialect == "sqlite"
        items, broken = g._prepare(questions, cons)
        assert broken == []
        bucket = g.classify_case(
            items[0],
            {"decision": "answer", "sql": "SELECT COUNT(*) FROM item", "rows": [[999]]},
            cons["main"],
            control=True,
        )
    finally:
        closer.close()
    assert bucket == "correct"


def test_bird_everyday_and_pharmacy_shapes(tmp_path: Path) -> None:
    short = tmp_path / "bird-short"
    short.mkdir()
    (short / "questions.json").write_text(
        json.dumps([{"id": "1", "db_id": "only", "question": "How many?", "SQL": "SELECT 1"}]),
        encoding="utf-8",
    )
    db = short / "only" / "only.sqlite"
    db.parent.mkdir()
    sqlite3.connect(db).execute("CREATE TABLE item (n INTEGER)").close()
    with pytest.raises(g.ShortPack):
        g.load_bird(short)

    root = tmp_path / "bird-ok"
    questions = []
    for index in range(g.BIRD_MIN_DB):
        db_id = f"db{index}"
        folder = root / db_id
        folder.mkdir(parents=True)
        sql = folder / f"{db_id}.sqlite"
        con = sqlite3.connect(sql)
        con.execute("CREATE TABLE item (n INTEGER)")
        con.execute("INSERT INTO item (n) VALUES (1)")
        con.commit()
        con.close()
        for nth in range(3):
            questions.append(
                {
                    "id": f"{db_id}-{nth}",
                    "db_id": db_id,
                    "question": "How many rows are in item?",
                    "SQL": "SELECT COUNT(*) FROM item",
                }
            )
    (root / "questions.json").write_text(json.dumps(questions), encoding="utf-8")
    loaded, paths = g.load_bird(root)
    assert len(loaded) == g.BIRD_MIN_Q
    assert len(paths) == g.BIRD_MIN_DB

    shop = tmp_path / "shop.sqlite"
    con = sqlite3.connect(shop)
    con.execute("CREATE TABLE item (n INTEGER)")
    con.execute("INSERT INTO item (n) VALUES (1)")
    con.commit()
    con.close()
    everyday = {
        "database": "shop.sqlite",
        "questions": [
            {
                "id": f"e{index}",
                "question": "How many rows are in item?",
                "gold_sql": "SELECT COUNT(*) FROM item",
            }
            for index in range(g.EVERYDAY_N)
        ],
    }
    everyday_path = tmp_path / "everyday.json"
    everyday_path.write_text(json.dumps(everyday), encoding="utf-8")
    rows, _paths = g.load_everyday(everyday_path)
    assert len(rows) == g.EVERYDAY_N
    everyday["questions"].pop()
    everyday_path.write_text(json.dumps(everyday), encoding="utf-8")
    with pytest.raises(g.ShortPack):
        g.load_everyday(everyday_path)

    with pytest.raises(g.GeneralityError, match="outside the repo"):
        g.load_pharmacy(SLICE)
    pharmacy = tmp_path / "pharmacy.json"
    pharmacy.write_text(
        json.dumps(
            {
                "database": "shop.sqlite",
                "questions": [
                    {
                        "id": "p1",
                        "question": "How many rows are in item?",
                        "gold_sql": "SELECT COUNT(*) FROM item",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    got, _paths = g.load_pharmacy(pharmacy)
    assert [row["id"] for row in got] == ["p1"]


def _tiny(tmp_path: Path) -> str:
    path = tmp_path / "item.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE item (n INTEGER)")
    con.execute("INSERT INTO item (n) VALUES (1)")
    con.commit()
    con.close()
    return str(path)


def test_budget_stops_before_a_call_that_would_cross_the_cap(tmp_path: Path) -> None:
    budget = g.Budget(1.0, 0.4)
    calls: list[int] = []

    def transport(body: dict) -> dict:
        calls.append(1)
        assert body["role"] == "sql"
        assert "model" not in body
        return {"choices": [{"message": {"content": "SELECT 1"}}], "usage": {"cost_usd": 0.4}}

    item = {
        "id": "q",
        "question": "How many?",
        "db_id": "main",
        "db_path": _tiny(tmp_path),
        "dialect": "sqlite",
    }
    seen = 0
    while True:
        reply = g._one_call(item, None, "sqlite", budget, transport=transport)
        if reply is None:
            break
        seen += 1
        if budget.stop:
            break
    assert seen == 2
    assert len(calls) == 2
    assert budget.spent == pytest.approx(0.8)
    assert budget.stop == "budget"


def test_missing_cost_stops_the_run(tmp_path: Path) -> None:
    budget = g.Budget(1.0, 0.2)

    def transport(_body: dict) -> dict:
        return {"choices": [{"message": {"content": "SELECT 1"}}]}

    item = {
        "id": "q",
        "question": "How many?",
        "db_id": "main",
        "db_path": _tiny(tmp_path),
        "dialect": "sqlite",
    }
    reply = g._one_call(item, None, "sqlite", budget, transport=transport)
    assert reply is not None
    assert reply["cost_usd"] is None
    assert budget.stop == "cost_unmeasured"
    assert g._one_call(item, None, "sqlite", budget, transport=transport) is None


def test_missing_dms_api_is_not_a_complete_run(tmp_path: Path) -> None:
    path = Path(_tiny(tmp_path))
    con = sqlite3.connect(path)
    question = {
        "id": "q",
        "question": "How many?",
        "db_id": "main",
        "db_path": str(path),
        "dialect": "sqlite",
        "gold_sql": "SELECT COUNT(*) FROM item",
    }

    def transport(body: dict) -> dict:
        assert body["role"] == "sql"
        assert "model" not in body
        return {
            "choices": [{"message": {"content": "SELECT COUNT(*) FROM item"}}],
            "usage": {"cost_usd": 0.01},
        }

    try:
        report = g.live_report(
            budget=g.Budget(1.0, 0.2),
            transport=transport,
            dms_post=None,
            packs=[("bird", "bird", [question], {"main": con}, "sqlite")],
        )
    finally:
        con.close()
    assert report["complete"] is False
    control = next(row for row in report["rows"] if row["row"] == "control")
    server = next(row for row in report["rows"] if row["row"] == "dms:server")
    assert control["status"] == "scored"
    assert control["correct"] == 1
    assert server["status"] == "not_run"
    assert server["reason"] == "dms_api_missing"


def test_live_refuses_when_the_flag_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(g.FLAG, raising=False)
    code = g.main(
        [
            "live",
            "--out",
            str(tmp_path / "out"),
            "--budget-usd",
            "1",
            "--max-usd-per-call",
            "0.1",
        ]
    )
    assert code == 2
    assert not (tmp_path / "out" / "generality.json").exists()


def test_rule_path_that_matches_is_not_correct(tmp_path: Path) -> None:
    path = tmp_path / "item.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE item (n INTEGER)")
    con.execute("INSERT INTO item (n) VALUES (1)")
    con.commit()
    con.close()
    spec = g.eval_pack.SourceSpec(kind="sqlite", database=str(path))
    with g.eval_pack.open_source(spec) as raw:
        body = g.eval_pack.tie_aware(
            raw, "SELECT COUNT(*) FROM item", ordered=False, dialect="sqlite"
        )
        item = {"id": "q", "db_id": "main", "dialect": "sqlite", "alt": [], **body}
        match = {"decision": "answer", "sql": "SELECT COUNT(*) FROM item", "serve_path": "L1"}
        assert g.classify_case(item, match, raw, control=False) == "rule_served"
        assert g.classify_case(item, match, raw, control=True) == "correct"
