"""SCORE-BIRD-ROWS-01 (#300): score_bird judges answer rows against gold SQL.

Seeded DuckDB built here. No network, no skip, no xfail.
Figures in this file are CI fixtures, not a BIRD score. Not COMPLETE.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import score_bird  # noqa: E402
from score_bird import (  # noqa: E402
    DEFAULT_PACK,
    EXIT_BLOCKED,
    EXIT_CONFIG,
    EXIT_FAIL,
    EXIT_PASS,
    judge_case,
    live,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "score_bird.py"

COUNT_SQL = "SELECT COUNT(*) AS n FROM gender"
DISTINCT_SQL = "SELECT DISTINCT gender FROM gender"
CASE = {"id": "gender_row_count", "expect": "answered", "min_rows": 1, "gold_sql": COUNT_SQL}
GREEN = {"badge": "L2_VALIDATED", "abstained": False}


def _seed(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE gender (id INTEGER, gender VARCHAR)")
        con.executemany(
            "INSERT INTO gender VALUES (?, ?)",
            [(1, "Male"), (2, "Female"), (3, "N/A")],
        )
    finally:
        con.close()
    return path


@pytest.fixture()
def oracle_db(tmp_path: Path) -> Path:
    return _seed(tmp_path / "bird_oracle.duckdb")


def _green(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {**GREEN, "rows": rows, "text": "answer"}


def test_matching_rows_are_ok(oracle_db: Path) -> None:
    verdict, reason = judge_case(CASE, _green([{"count": 3}]), oracle_db)
    assert (verdict, reason) == ("OK", "")


def test_matching_rows_ignore_order_as_multiset(oracle_db: Path) -> None:
    case = {**CASE, "gold_sql": DISTINCT_SQL, "min_rows": 1}
    rows = [{"gender": "N/A"}, {"gender": "Male"}, {"gender": "Female"}]
    assert judge_case(case, _green(rows), oracle_db)[0] == "OK"


def test_doubled_row_is_wrong(oracle_db: Path) -> None:
    verdict, reason = judge_case(CASE, _green([{"n": 3}, {"n": 3}]), oracle_db)
    assert verdict == "WRONG"
    assert reason == "rows_mismatch:count=2/1"


def test_doubled_distinct_value_is_wrong(oracle_db: Path) -> None:
    case = {**CASE, "gold_sql": DISTINCT_SQL}
    rows = [{"gender": "Male"}, {"gender": "Male"}, {"gender": "Female"}]
    assert judge_case(case, _green(rows), oracle_db)[0] == "WRONG"


def test_short_total_is_wrong(oracle_db: Path) -> None:
    verdict, reason = judge_case(CASE, _green([{"n": 2}]), oracle_db)
    assert verdict == "WRONG"
    assert reason == "rows_mismatch:values"


def test_gold_error_is_oracle_error_never_ok(oracle_db: Path) -> None:
    case = {**CASE, "gold_sql": "SELECT COUNT(*) FROM superhero"}
    verdict, reason = judge_case(case, _green([{"n": 3}]), oracle_db)
    assert verdict == "ORACLE_ERROR"
    assert reason.startswith("oracle_error:")


def test_abstain_with_working_gold_is_abstain(oracle_db: Path) -> None:
    env = {"badge": "ABSTAIN", "abstained": True, "rows": []}
    assert judge_case(CASE, env, oracle_db)[0] == "ABSTAIN"


def test_no_oracle_db_never_claims_ok_or_wrong() -> None:
    assert judge_case(CASE, _green([{"n": 3}]), None) == ("NO_ORACLE", "no --oracle-db")
    assert judge_case(CASE, _green([{"n": 99}, {"n": 99}]), None)[0] == "NO_ORACLE"


def test_case_without_gold_sql_is_no_oracle(oracle_db: Path) -> None:
    case = {k: v for k, v in CASE.items() if k != "gold_sql"}
    assert judge_case(case, _green([{"n": 3}]), oracle_db)[0] == "NO_ORACLE"


def test_refuse_trap_stays_badge_judged(oracle_db: Path) -> None:
    trap = {"id": "trap", "expect": "refuse"}
    assert judge_case(trap, _green([{"n": 1}]), None)[0] == "WRONG"
    assert judge_case(trap, _green([{"n": 1}]), oracle_db)[0] == "WRONG"
    abstain = {"badge": "ABSTAIN", "abstained": True, "rows": []}
    assert judge_case(trap, abstain, None)[0] == "ABSTAIN"


# --- live(): the summary a reader sees -------------------------------------


def _pack(tmp_path: Path) -> Path:
    data = yaml.safe_load(DEFAULT_PACK.read_text(encoding="utf-8"))
    for case in data["questions"]:
        if case["id"] == "gender_row_count":
            case["gold_sql"] = COUNT_SQL
        if case["id"] == "gender_distinct":
            case["gold_sql"] = DISTINCT_SQL
    data["questions"] = [
        c for c in data["questions"] if c["id"] in {"gender_row_count", "gender_distinct"}
    ] + [c for c in data["questions"] if str(c.get("expect")) == "refuse"]
    out = tmp_path / "pack.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    answers: dict[str, list[dict[str, Any]]],
) -> None:
    pack_path = _pack(tmp_path)
    real_load = score_bird.load_bird
    monkeypatch.setattr(score_bird, "load_bird", lambda *a, **k: real_load(pack_path))
    monkeypatch.setattr(
        score_bird, "fetch_bronze", lambda *a, **k: ("ok", ["bronze.public_gender"])
    )
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path / "art"))

    def _ask(_base: str, question: str, _space: str, _timeout: float) -> dict[str, Any]:
        if question in answers:
            return _green(answers[question])
        return {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "no"}

    monkeypatch.setattr(score_bird, "_ask_live", _ask)


ROW_Q = "How many rows are in the gender table?"
DISTINCT_Q = "List the distinct values in the gender table."
RIGHT = {
    ROW_Q: [{"count": 3}],
    DISTINCT_Q: [{"gender": "Female"}, {"gender": "N/A"}, {"gender": "Male"}],
}


def _artifact(tmp_path: Path) -> dict[str, Any]:
    import json

    return json.loads((tmp_path / "art" / "score_bird.json").read_text(encoding="utf-8"))


def test_live_without_oracle_prints_no_ok_and_no_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doubled = {**RIGHT, ROW_Q: [{"n": 3}, {"n": 3}]}
    _wire(monkeypatch, tmp_path, doubled)
    rc = live("http://127.0.0.1:1", 1.0, score_bird.BIRD_SPACE)
    out = capsys.readouterr().out
    assert rc == EXIT_BLOCKED, out
    assert "PASS" not in out
    assert "gender_row_count\tlive\tNO_ORACLE" in out
    assert "gender_distinct\tlive\tNO_ORACLE" in out
    assert "\tlive\tOK" not in out
    assert "VERDICT: NOT SCORED" in out
    art = _artifact(tmp_path)
    assert art["generative"]["ok"] is None
    assert art["generative"]["answered"] is None
    assert art["generative"]["no_oracle"] == 2
    assert art["answered"] is None and art["bound_pct"] is None
    assert art["passed"] is False


def test_live_with_oracle_right_rows_pass_with_n_and_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    oracle_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wire(monkeypatch, tmp_path, RIGHT)
    rc = live("http://127.0.0.1:1", 1.0, score_bird.BIRD_SPACE, oracle_db)
    out = capsys.readouterr().out
    assert rc == EXIT_PASS, out
    assert "gender_row_count\tlive\tOK" in out
    assert "answered=2 bound about 150.00 pct" in out
    art = _artifact(tmp_path)
    assert art["generative"]["ok"] == 2
    assert art["answered"] == 2


def test_live_with_oracle_doubled_row_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    oracle_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wire(monkeypatch, tmp_path, {**RIGHT, ROW_Q: [{"n": 3}, {"n": 3}]})
    rc = live("http://127.0.0.1:1", 1.0, score_bird.BIRD_SPACE, oracle_db)
    out = capsys.readouterr().out
    assert rc == EXIT_FAIL, out
    assert "gender_row_count\tlive\tWRONG" in out
    assert "rows_mismatch:count=2/1" in out
    assert "PASS" not in out


def test_live_with_oracle_short_total_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    oracle_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _wire(monkeypatch, tmp_path, {**RIGHT, ROW_Q: [{"n": 2}]})
    rc = live("http://127.0.0.1:1", 1.0, score_bird.BIRD_SPACE, oracle_db)
    out = capsys.readouterr().out
    assert rc == EXIT_FAIL, out
    assert "gender_row_count\tlive\tWRONG" in out
    assert _artifact(tmp_path)["wrong"] == 1


def test_live_gold_error_fails_as_oracle_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    _wire(monkeypatch, tmp_path, RIGHT)
    rc = live("http://127.0.0.1:1", 1.0, score_bird.BIRD_SPACE, empty)
    out = capsys.readouterr().out
    assert rc == EXIT_FAIL, out
    assert "gender_row_count\tlive\tORACLE_ERROR" in out
    assert "\tlive\tOK" not in out
    assert "PASS" not in out


def test_cli_oracle_db_missing_file_is_config(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--ab", "--oracle-db", str(tmp_path / "nope.duckdb")],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == EXIT_CONFIG, proc.stdout + proc.stderr
    assert "CONFIG" in proc.stdout


@pytest.mark.parametrize(
    "mode",
    [["--self-check"], ["--minidev", "x.json", "--offline"], ["--compare", "a", "b"]],
)
def test_cli_oracle_db_outside_pack_path_is_config_not_ignored(
    oracle_db: Path, mode: list[str]
) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *mode, "--oracle-db", str(oracle_db)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == EXIT_CONFIG, proc.stdout + proc.stderr
    assert "--oracle-db applies to the pack" in proc.stdout
