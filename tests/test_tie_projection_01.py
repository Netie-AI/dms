"""BIRD-TIES-PROJECTION-01. Ties at a LIMIT cutoff, asked columns, two readings.

Flag default off. Off, the prompt, the envelope, and the route are unchanged.
On, a tie at the cutoff returns the full tied set, a top-n answer keeps only
the asked column, and two executed readings become a clarify.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import duckdb
import pytest
from cortex_client.compute import _insights_body, question_for_sql_writer
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.sql_loop import run_readonly
from dms_executor.sql_vote import reading_prompt
from dms_executor.tie_projection import (
    ModelReport,
    apply_on_warehouse,
    apply_served,
    begin_turn,
    note_model_payload,
)

_DIRECT = (
    "Write one SELECT that answers the question. "
    "Use only tables and columns that appear in the ontology. "
    "Return the SQL. Do not write a plan.\nQuestion:\n"
)
_TIE_SQL = (
    "SELECT league AS name FROM game GROUP BY league "
    "ORDER BY COUNT(*) DESC, league LIMIT 1"
)
_TOP_SQL = (
    "SELECT name, games AS game_count FROM league ORDER BY games DESC LIMIT 4"
)
_ONE_SQL = "SELECT name FROM league ORDER BY games DESC, name LIMIT 1"
_READ_A = "SELECT symbol FROM atom WHERE id NOT IN (SELECT left_id FROM bond)"
_READ_B = (
    "SELECT symbol FROM atom AS a WHERE NOT EXISTS ("
    "SELECT 1 FROM bond AS b WHERE b.left_id = a.id OR b.right_id = a.id)"
)


def _warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "tie.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE game (league VARCHAR)")
        leagues = ["East"] * 3 + ["North"] * 3 + ["South"] * 3 + ["West"] * 3 + ["Cup"]
        con.executemany("INSERT INTO game VALUES (?)", [(name,) for name in leagues])
        con.execute("CREATE TABLE league (name VARCHAR, games INTEGER)")
        con.executemany(
            "INSERT INTO league VALUES (?, ?)",
            [("A", 5), ("B", 4), ("C", 3), ("D", 2), ("E", 1)],
        )
        con.execute("CREATE TABLE atom (id INTEGER, symbol VARCHAR)")
        con.execute("CREATE TABLE bond (left_id INTEGER, right_id INTEGER)")
        con.executemany(
            "INSERT INTO atom VALUES (?, ?)",
            [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "E")],
        )
        con.executemany("INSERT INTO bond VALUES (?, ?)", [(1, 2), (2, 3)])
    finally:
        con.close()
    return path


def _env(sql: str, rows: list[dict]) -> dict:
    env = build_answer_envelope(
        answer_id="ans_tie",
        text="Found 1 row.",
        badge="L2_VALIDATED",
        rows=rows,
        sql_used=sql,
        assumptions=["test"],
        route="generated",
        ask_mode="live",
    )
    assert_envelope_valid(env)
    return env


def _run(path: Path):
    def execute(sql: str):
        return run_readonly(sql, path)

    return execute


def test_flag_off_prompt_envelope_and_route_are_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_TIE_PROJECTION", raising=False)
    question = "Which league had the most games?"
    assert question_for_sql_writer(question) is question
    body = _insights_body(question, session_id=None, space_id=None, ontology=None)
    assert body["question"] == question
    assert body["intent"] == question
    assert reading_prompt(question, reading=1) == _DIRECT + question
    path = _warehouse(tmp_path)
    rows, err = run_readonly(_TIE_SQL, path)
    assert err is None and rows
    env = _env(_TIE_SQL, rows)
    out = apply_served(env, execute=_run(path), question=question)
    assert out is env
    assert out["route"] == "generated"
    assert out["rows"] == rows
    monkeypatch.setenv("DMS_TIE_PROJECTION", "0")
    assert question_for_sql_writer(question) is question
    assert apply_on_warehouse(
        env,
        warehouse=path,
        dialect="duckdb",
        question=question,
        space_id=None,
        session_id=None,
        grantable={"game"},
    ) is env


def test_limit_1_tie_returns_the_full_tied_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_TIE_PROJECTION", "1")
    begin_turn()
    path = _warehouse(tmp_path)
    limited, err = run_readonly(_TIE_SQL, path)
    assert err is None
    assert len(limited) == 1
    env = _env(_TIE_SQL, limited)
    out = apply_served(env, execute=_run(path), dialect="duckdb", question="league")
    assert out["abstained"] is False
    assert out["route"] == "generated"
    names = [row["name"] for row in out["rows"]]
    assert names == ["East", "North", "South", "West"]
    assert "Cup" not in names
    assert "tie at the limit cutoff" in out["text"]
    assert "tie_at_cutoff" in out["assumptions"]
    again, again_err = run_readonly(out["sql_used"], path)
    assert again_err is None
    assert sorted(row["name"] for row in again) == names
    assert_envelope_valid(out)


def test_unique_limit_1_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_TIE_PROJECTION", "1")
    begin_turn()
    path = _warehouse(tmp_path)
    rows, err = run_readonly(_ONE_SQL, path)
    assert err is None and rows == [{"name": "A"}]
    env = _env(_ONE_SQL, rows)
    assert apply_served(env, execute=_run(path), dialect="duckdb") is env


def test_top4_keeps_the_asked_column_and_moves_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_TIE_PROJECTION", "1")
    begin_turn()
    path = _warehouse(tmp_path)
    rows, err = run_readonly(_TOP_SQL, path)
    assert err is None and len(rows) == 4
    env = _env(_TOP_SQL, rows)
    out = apply_served(
        env,
        execute=_run(path),
        dialect="duckdb",
        question="leagues",
        report=ModelReport(asked_columns=["name"]),
    )
    assert [row.keys() for row in out["rows"]] == [{"name"}] * 4
    assert [row["name"] for row in out["rows"]] == ["A", "B", "C", "D"]
    again, again_err = run_readonly(out["sql_used"], path)
    assert again_err is None
    assert [list(row.keys()) for row in again] == [["name"]] * 4
    assert [row["name"] for row in again] == ["A", "B", "C", "D"]
    assert out["context_columns"] == [
        {"column": "game_count", "values": [5, 4, 3, 2]}
    ]
    assert "context_columns" in out["text"]
    assert out["route"] == "generated"
    assert_envelope_valid(out)


def test_two_readings_return_a_clarify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_TIE_PROJECTION", "1")
    begin_turn()
    path = _warehouse(tmp_path)
    rows_a, err_a = run_readonly(_READ_A, path)
    rows_b, err_b = run_readonly(_READ_B, path)
    assert err_a is None and err_b is None
    assert len(rows_a) != len(rows_b)
    env = _env(_READ_A, rows_a)
    out = apply_served(
        env,
        execute=_run(path),
        dialect="duckdb",
        question="Which atoms have no bond?",
        report=ModelReport(
            readings=[
                {"id": "not_in_left", "text": "not listed as the left atom", "sql": _READ_A},
                {"id": "no_bond", "text": "no bond on either side", "sql": _READ_B},
            ]
        ),
    )
    assert out["route"] == "needs_clarification"
    assert out["abstained"] is True
    clarify = out["clarify"]
    assert clarify["blocks"] is True
    assert len(clarify["readings"]) == 2
    texts = [item["text"] for item in clarify["readings"]]
    assert f"({len(rows_a)} rows)" in texts[0]
    assert f"({len(rows_b)} rows)" in texts[1]
    assert out["rows"] == []
    assert_envelope_valid(out)


def test_large_tie_pages_and_does_not_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_TIE_PROJECTION", "1")
    begin_turn()
    path = _warehouse(tmp_path)
    limited, err = run_readonly(_TIE_SQL, path)
    assert err is None
    env = _env(_TIE_SQL, limited)
    out = apply_served(
        env,
        execute=_run(path),
        dialect="duckdb",
        question="league",
        page_size=2,
    )
    assert len(out["rows"]) == 2
    assert out["tie_row_count"] == 4
    assert out["complete"] is False
    assert out["next_offset"] == 2
    assert out["truncated"] is False
    assert out["bounded"] is False
    assert "Nothing was cut" in out["text"]
    assert_envelope_valid(out)


def test_flag_on_json_sql_unwraps_and_flag_off_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = json.dumps(
        {
            "sql": "SELECT name FROM league",
            "asked_columns": ["name"],
            "readings": [],
        }
    )
    monkeypatch.delenv("DMS_TIE_PROJECTION", raising=False)
    assert note_model_payload({"query_sql": raw}, raw) == raw
    monkeypatch.setenv("DMS_TIE_PROJECTION", "1")
    begin_turn()
    got = note_model_payload({"query_sql": raw}, raw)
    assert got == "SELECT name FROM league"
    from dms_executor.tie_projection import current_report

    assert current_report().asked_columns == ["name"]


def test_writer_prompt_grows_only_when_the_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_TIE_PROJECTION", "1")
    question = "Which league had the most games?"
    prompted = question_for_sql_writer(question)
    assert prompted.startswith(question)
    assert "asked_columns" in prompted
    assert "readings" in prompted
    assert "model" not in prompted
    body = _insights_body(question, session_id="s", space_id="sp", ontology=None)
    assert body["question"].startswith(question)
    assert body["model_preference"]
    assert "gpt" not in json.dumps(body["model_preference"])


def test_module_does_not_test_membership_in_the_question() -> None:
    source = Path(__file__).resolve().parents[1] / (
        "packages/executor/dms_executor/tie_projection.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for op, comp in zip(node.ops, node.comparators, strict=True):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if isinstance(comp, ast.Name) and comp.id == "question":
                raise AssertionError("question membership")
            if isinstance(node.left, ast.Name) and node.left.id == "question":
                raise AssertionError("question membership")


def test_live_ask_applies_only_when_the_flag_is_on() -> None:
    from dms_executor import Executor

    source = ast.get_source_segment(
        Path(Executor.live_ask.__code__.co_filename).read_text(encoding="utf-8"),
        ast.parse(
            Path(Executor.live_ask.__code__.co_filename).read_text(encoding="utf-8")
        ),
    )
    text = Path(Executor.live_ask.__code__.co_filename).read_text(encoding="utf-8")
    assert "tie_projection_enabled()" in text
    assert "apply_on_warehouse" in text
    assert source is None or "apply_on_warehouse" in text
