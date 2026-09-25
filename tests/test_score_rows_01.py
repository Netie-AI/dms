"""SCORE-ROWS-01: curated judge compares answer rows with oracle SQL.

Seeded DuckDB is built here. No skip, no xfail, no importorskip.
Figures in this file are CI fixtures, not live. Not COMPLETE.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from oracle_row_match import read_schema_version  # noqa: E402
from score_curated import (  # noqa: E402
    EXIT_CONFIG,
    FIGURE_LABEL_FIXTURE,
    PACK_DENOMINATOR,
    REFUSE,
    judge,
    judge_detailed,
    load_pack,
    main,
    pack_category_report,
    self_check,
)

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"

MONTHLY_SQL = "SELECT month, ROUND(SUM(amount), 2) AS total FROM sales GROUP BY month"
TOP5_SQL = (
    "SELECT sku, ROUND(SUM(amount), 2) AS total FROM sku_sales "
    "GROUP BY sku ORDER BY total DESC, sku ASC LIMIT 5"
)
L0 = {"id": "monthly", "expect": "l0", "min_rows": 1}
GREEN = {"badge": "L0_CERTIFIED", "abstained": False}


def _seed(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        con.execute("CREATE TABLE sales (month INTEGER, sku VARCHAR, amount DOUBLE)")
        con.executemany(
            "INSERT INTO sales VALUES (?, 'A', 10.0)",
            [(m,) for m in range(1, 13)],
        )
        con.execute("CREATE TABLE sku_sales (sku VARCHAR, amount DOUBLE)")
        con.executemany(
            "INSERT INTO sku_sales VALUES (?, ?)",
            [
                ("A", 50.0),
                ("B", 40.0),
                ("C", 30.0),
                ("D", 20.0),
                ("E", 10.0),
                ("F", 5.0),
            ],
        )
    finally:
        con.close()
    return path


def _env(rows: list[dict[str, object]]) -> dict[str, object]:
    return {**GREEN, "rows": rows}


def _refuse_cases() -> list[dict[str, object]]:
    pack = load_pack(PACK)
    return [
        c
        for c in pack["questions"]
        if str(c.get("expect") or "").lower() in REFUSE
    ]


def test_seeded_fixture_records_schema_version(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    assert read_schema_version(db) == "1"


def test_three_row_ungrouped_answer_to_monthly_oracle_is_wrong(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    three = [
        {"sku": "A", "amount": 10.0},
        {"sku": "B", "amount": 9.0},
        {"sku": "C", "amount": 8.0},
    ]
    got = judge_detailed(
        L0,
        _env(three),
        oracle_db=db,
        oracle_sql=MONTHLY_SQL,
    )
    assert got.verdict == "WRONG"
    assert got.reason == "rows_mismatch:count=3/12"
    assert got.scorer_ok_rows_not_compared == "OK"


def test_exact_match_is_ok_column_order_insensitive(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    rows = [{"total": 10.0, "month": m} for m in range(1, 13)]
    got = judge_detailed(L0, _env(rows), oracle_db=db, oracle_sql=MONTHLY_SQL)
    assert got.verdict == "OK"
    assert got.reason == ""


def test_top5_one_different_sku_is_wrong_values(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    # Gold top-5 is A,B,C,D,E. Swap E for F.
    rows = [
        {"sku": "A", "total": 50.0},
        {"sku": "B", "total": 40.0},
        {"sku": "C", "total": 30.0},
        {"sku": "D", "total": 20.0},
        {"sku": "F", "total": 5.0},
    ]
    got = judge_detailed(
        {"id": "top5", "expect": "l0", "min_rows": 1},
        _env(rows),
        oracle_db=db,
        oracle_sql=TOP5_SQL,
    )
    assert got.verdict == "WRONG"
    assert got.reason == "rows_mismatch:values"
    assert got.scorer_ok_rows_not_compared == "OK"


def test_float_past_oracle_rounding_is_ok(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    rows = [{"month": m, "total": 10.001} for m in range(1, 13)]
    got = judge_detailed(L0, _env(rows), oracle_db=db, oracle_sql=MONTHLY_SQL)
    assert got.verdict == "OK"


def test_oracle_missing_column_is_oracle_error_never_ok(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    got = judge_detailed(
        L0,
        _env([{"month": 1, "total": 10.0}]),
        oracle_db=db,
        oracle_sql="SELECT no_such_column FROM sales",
    )
    assert got.verdict == "ORACLE_ERROR"
    assert got.verdict != "OK"
    assert got.verdict != "ABSTAIN"
    assert got.verdict != "LAYER"
    assert got.reason.startswith("oracle_error:")
    assert got.scorer_ok_rows_not_compared == "OK"


def test_confident_trap_answer_is_wrong(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    got = judge_detailed(
        {"id": "trap_last_month", "expect": "abstain"},
        _env([{"v": 1}]),
        oracle_db=db,
        oracle_sql="SELECT 1 AS v",
    )
    assert got.verdict == "WRONG"
    assert got.verdict != "OK"


def test_oracle_error_not_counted_ok_in_categories(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    got = judge(
        L0,
        _env([{"month": 1, "total": 10.0}]),
        oracle_db=db,
        oracle_sql="SELECT no_such_column FROM sales",
    )
    cats = pack_category_report(
        {"OK": 0, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0, "ORACLE_ERROR": 1},
        figure_label=FIGURE_LABEL_FIXTURE,
    )
    assert got == "ORACLE_ERROR"
    assert cats["oracle_error_of"] == "1/52"
    assert cats["answered_of"] == "0/52"
    assert cats["figure_label"] == FIGURE_LABEL_FIXTURE
    assert "CI fixtures, not live" in cats["figure_label"]


def test_refuse_shapes_are_nine() -> None:
    cases = _refuse_cases()
    assert len(cases) == 9
    ids = {str(c["id"]) for c in cases}
    assert ids == {
        "ops_spend_boundary",
        "trap_last_month",
        "trap_short_paraphrase",
        "trap_alerts_ungranted",
        "trap_high_risk_pending",
        "ops_supplier_rank_boundary",
        "trap_delayed_count",
        "trap_stock_by_bin",
        "trap_how_full_synonym",
    }


@pytest.mark.parametrize(
    "qid",
    [
        "ops_spend_boundary",
        "trap_last_month",
        "trap_short_paraphrase",
        "trap_alerts_ungranted",
        "trap_high_risk_pending",
        "ops_supplier_rank_boundary",
        "trap_delayed_count",
        "trap_stock_by_bin",
        "trap_how_full_synonym",
    ],
)
def test_each_refuse_trap_shape_confident_is_wrong_abstain_passes(
    qid: str, tmp_path: Path
) -> None:
    by_id = {str(c["id"]): c for c in _refuse_cases()}
    case = by_id[qid]
    db = _seed(tmp_path / "oracle.duckdb")
    green = judge_detailed(
        case,
        _env([{"v": 1}]),
        oracle_db=db,
        oracle_sql="SELECT 1 AS v",
    )
    assert green.verdict == "WRONG", qid
    refused = judge_detailed(
        case,
        {"badge": "ABSTAIN", "abstained": True, "rows": []},
        oracle_db=db,
        oracle_sql="SELECT 1 AS v",
    )
    assert refused.verdict == "ABSTAIN", qid


def test_null_equals_null(tmp_path: Path) -> None:
    db = _seed(tmp_path / "oracle.duckdb")
    con = duckdb.connect(str(db))
    try:
        con.execute("ALTER TABLE sales ADD COLUMN note VARCHAR")
    finally:
        con.close()
    sql = "SELECT sku, note FROM sales WHERE month = 1"
    got = judge_detailed(
        L0,
        _env([{"note": None, "sku": "A"}]),
        oracle_db=db,
        oracle_sql=sql,
    )
    assert got.verdict == "OK"


def test_categories_never_use_a_smaller_denominator() -> None:
    cats = pack_category_report(
        {"OK": 40, "LAYER": 0, "ABSTAIN": 9, "WRONG": 2, "ORACLE_ERROR": 1},
        figure_label=FIGURE_LABEL_FIXTURE,
    )
    assert cats["denominator"] == PACK_DENOMINATOR == 52
    assert cats["answered_of"].endswith("/52")
    assert cats["abstained_of"].endswith("/52")
    assert cats["wrong_of"].endswith("/52")
    assert cats["layer_of"].endswith("/52")
    assert cats["oracle_error_of"].endswith("/52")
    assert cats["excluded_pending_scan_of"].endswith("/52")
    assert cats["figure_label"] == "CI fixtures, not live"


def test_live_modes_require_oracle_db() -> None:
    assert main(["--live"]) == EXIT_CONFIG
    assert main(["--climb", "--url", "https://studio.netie.ai/api"]) == EXIT_CONFIG
    assert main(["--climb", "--ab", "--url", "https://studio.netie.ai/api"]) == EXIT_CONFIG
    assert main(["--prove-path", "--url", "https://studio.netie.ai/api"]) == EXIT_CONFIG


def test_self_check_still_passes_on_52_question_pack() -> None:
    pack = load_pack(PACK)
    assert len(pack["questions"]) == PACK_DENOMINATOR
    assert self_check() == 0
