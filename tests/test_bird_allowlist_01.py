"""BIRD-ALLOWLIST-01 / dms#304. New tests only. No skip, no xfail.

An unlisted table stays in n as excluded_table. A preflight column stays in n
as excluded_column. Both at once: one label, the table wins, the column is
still counted. A fully allowed question is scored. n stays 500.
A flagged grant blocks live with zero ask calls.
A FAIL cell whose column is in the deny file is EXCLUDE citing dms#304.
The same values on a column that is not in that file stay FAIL.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "core"))
sys.path.insert(0, str(ROOT / "packages" / "executor"))

from bird_minidev import score_cases, summarize  # noqa: E402
from score_bird import BIRD_SPACE, live  # noqa: E402

ALLOWLIST = ROOT / "tests" / "fixtures" / "bird_minidev" / "table_allowlist.yaml"
PREFLIGHT = ROOT / "tests" / "fixtures" / "bird_minidev" / "preflight.yaml"
DENY_COLUMNS = (
    "drivers.nationality",
    "member.position",
    "patient.diagnosis",
    "users.location",
    "schools.city",
    "schools.county",
    "schools.district",
    "schools.doctype",
    "schools.edopsname",
    "schools.eilname",
    "schools.mailcity",
    "schools.school",
    "schools.soctype",
)


def _ok(question: str) -> dict:
    return {
        "badge": "L0_CERTIFIED",
        "abstained": False,
        "rows": [{"name": "a"}],
        "text": question,
    }


def _gold(_sql: str) -> tuple[list[dict], None]:
    return [{"name": "a"}], None


def _q(qid: int, sql: str, question: str) -> dict:
    return {
        "question_id": qid,
        "db_id": "toy",
        "difficulty": "simple",
        "question": question,
        "SQL": sql,
    }


def test_unlisted_table_is_excluded_table() -> None:
    asks: list[str] = []
    golds: list[str] = []

    def ask(question: str) -> dict:
        asks.append(question)
        return _ok(question)

    def gold(sql: str) -> tuple[list[dict], None]:
        golds.append(sql)
        return [{"name": "a"}], None

    rows = score_cases(
        [_q(1, "SELECT 1 AS n FROM schools", "blocked-table")],
        ask_fn=ask,
        gold_fn=gold,
    )
    assert len(rows) == 1
    assert asks == []
    assert golds == []
    assert rows[0]["verdict"] == "ABSTAIN"
    assert rows[0]["label"] == "excluded_table:schools"
    assert rows[0]["reason"] == "excluded_table:schools"
    assert rows[0]["got_rows"] == 0
    assert rows[0]["label"] != "OK"


def test_listed_column_is_excluded_column() -> None:
    asks: list[str] = []

    def ask(question: str) -> dict:
        asks.append(question)
        return _ok(question)

    column_only = _q(2, "SELECT drivers.nationality FROM account", "listed-column")
    both = _q(3, "SELECT nationality FROM drivers", "table-and-column")
    rows = score_cases([column_only, both], ask_fn=ask, gold_fn=_gold)
    assert asks == []
    assert rows[0]["verdict"] == "ABSTAIN"
    assert rows[0]["label"] == "excluded_column:drivers.nationality"
    assert rows[0]["got_rows"] == 0
    assert "dms#304" in rows[0]["citation"]
    assert "preflight.yaml" in rows[0]["citation"]
    assert rows[1]["label"] == "excluded_table:drivers"
    assert "drivers.nationality" in rows[1]["excluded_columns"]
    assert "excluded_column:" not in rows[1]["label"]


def test_allowed_question_is_scored() -> None:
    asks: list[str] = []

    def ask(question: str) -> dict:
        asks.append(question)
        return _ok(question)

    allowed = _q(4, "SELECT name FROM account", "allowed-question")
    blocked = _q(5, "SELECT 1 AS n FROM schools", "blocked-table")
    rows = score_cases([allowed, blocked], ask_fn=ask, gold_fn=_gold)
    assert asks == ["allowed-question"]
    assert rows[0]["verdict"] == "OK"
    assert rows[0]["label"] == "OK"
    assert rows[0]["got_rows"] == 1
    assert not str(rows[0]["reason"]).startswith("excluded_")
    assert rows[1]["label"] == "excluded_table:schools"


def test_n_stays_500() -> None:
    asks: list[str] = []

    def ask(question: str) -> dict:
        asks.append(question)
        return _ok(question)

    questions = [_q(i, "SELECT name FROM account", f"allowed-{i}") for i in range(498)]
    questions.append(_q(498, "SELECT 1 AS n FROM schools", "blocked-table"))
    questions.append(_q(499, "SELECT drivers.nationality FROM account", "listed-column"))
    rows = score_cases(questions, ask_fn=ask, gold_fn=_gold)
    summary = summarize(rows)
    assert len(rows) == 500
    assert summary["n"] == 500
    assert summary["n_without_excluded"] == 498
    assert summary["right"] == 498
    assert summary["answered"] == 498
    assert summary["excluded_by_table"] == {"schools": 1}
    assert summary["excluded_by_column"] == {"drivers.nationality": 1}
    assert len(asks) == 498
    assert rows[498]["label"] == "excluded_table:schools"
    assert rows[499]["label"] == "excluded_column:drivers.nationality"
    assert all(isinstance(row["label"], str) and row["label"] for row in rows)


def test_parse_error_stays_in_n_as_excluded_table() -> None:
    asks: list[str] = []

    def ask(question: str) -> dict:
        asks.append(question)
        return _ok(question)

    rows = score_cases(
        [_q(6, "SELECT (((", "unparsed")],
        ask_fn=ask,
        gold_fn=_gold,
    )
    assert len(rows) == 1
    assert asks == []
    assert rows[0]["label"] == "excluded_table:unparsed"
    assert rows[0]["got_rows"] == 0


def test_flagged_table_in_grants_blocks_with_zero_live_calls(monkeypatch, capsys) -> None:
    asks: list[tuple] = []

    def fetch(*_a, **_k):
        return "ok", ["bronze.public_schools"]

    def ask(*a, **_k):
        asks.append(a)
        return _ok("live")

    monkeypatch.setattr("score_bird.fetch_bronze", fetch)
    monkeypatch.setattr("score_bird._ask_live", ask)
    code = live("http://127.0.0.1:9", 1.0, BIRD_SPACE)
    out = capsys.readouterr().out
    assert code != 0
    assert asks == []
    assert "BLOCKED" in out


def test_listed_column_exclude_cites_dms304(tmp_path: Path) -> None:
    from pii_mask_check import FlaggedCell, check_cell

    lake = tmp_path / "listed.duckdb"
    cell = FlaggedCell(
        "dms_v5_duckdb",
        "bronze.public_drivers",
        "nationality",
        "person_name_shape_low",
        1,
    )
    got = check_cell(cell, lake)
    assert got.path_a == "EXCLUDE"
    assert got.path_b == "EXCLUDE"
    assert got.path_c == "EXCLUDE"
    assert "dms#304" in got.note
    assert "drivers.nationality" in got.note


def test_unlisted_column_same_values_stay_fail(tmp_path: Path) -> None:
    from pii_mask_check import FlaggedCell, check_cell

    assert PREFLIGHT.is_file()
    assert ALLOWLIST.is_file()
    deny_text = PREFLIGHT.read_text(encoding="utf-8")
    allow_text = ALLOWLIST.read_text(encoding="utf-8")
    assert "schools.street" not in deny_text
    assert "schools.street" not in allow_text
    for col in DENY_COLUMNS:
        assert col in deny_text
        table = col.split(".", 1)[0]
        assert f"public_{table}" not in allow_text
    assert "public_yearmonth" not in allow_text
    assert "public_trans" not in allow_text
    lake = tmp_path / "unlisted.duckdb"
    cell = FlaggedCell(
        "dms_v5_duckdb",
        "bronze.public_schools",
        "street",
        "person_name_shape_low",
        1,
    )
    got = check_cell(cell, lake)
    assert got.path_a == "FAIL"
    assert got.path_b == "FAIL"
    assert got.path_c == "FAIL"
    assert "dms#304" not in got.note
