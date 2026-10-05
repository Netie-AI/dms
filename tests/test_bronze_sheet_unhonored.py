"""The workbook+sheet lane answers exactly two shapes; a clause it would ignore must abstain.

Red team (2026-10-02, Refs the Plan C report): the lane served ``L0_CERTIFIED`` plain SUMs for
"excluding Electronics", "the lowest", "by average", "as a percentage of the total", "by number of
SKUs", "top 1 SKU per category" and "... for sku SKU-3" on a top-N ask, because it only read
``top N`` + a category noun + a measure and ignored every other word. Each of those is a wrong
number under the strongest badge, so the lane now abstains by name instead.

Asserted on the customer envelope (hard rule 10a): badge, abstained, rows, text.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.bronze import bronze_table_for_sheet
from dms_executor.bronze_sheet_ask import maybe_bronze_sheet_ask
from dms_executor.envelope import assert_envelope_valid
from dms_executor.lake_schema import ensure_lake_schemas

WB = "cf98e431_p50_01_sales_messy.xlsx"
SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "wh.duckdb"
    ident = bronze_table_for_sheet(WB, "Sales").split(".", 1)[-1]
    con = duckdb.connect(str(path))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(
            f'CREATE TABLE bronze."{ident}" '
            "(category VARCHAR, sku VARCHAR, city VARCHAR, sales_value_myr DOUBLE)"
        )
        con.execute(
            f"""
            INSERT INTO bronze."{ident}" VALUES
              ('Electronics', 'SKU-1', 'Kuala Lumpur', 1500.0),
              ('Electronics', 'SKU-2', 'Johor Bahru', 500.0),
              ('Home', 'SKU-3', 'Kuala Lumpur', 900.0),
              ('Sports', 'SKU-4', 'Johor Bahru', 300.0),
              ('Misc', 'SKU-5', 'Kuala Lumpur', 100.0)
            """
        )
    finally:
        con.close()
    return path


def _ask(db: Path, tail: str) -> dict:
    question = f"In {WB} on the Sales sheet, {tail}"
    env = maybe_bronze_sheet_ask(question, warehouse=db, space_id=SPACE)
    assert env is not None
    return env


# Each of these carries a clause the lane would have silently dropped.
UNHONORED = {
    "exclusion": "what are the top 3 categories by sales_value_myr, excluding Electronics?",
    "exclusion-prefix": "excluding Electronics, what are the top 3 categories by sales_value_myr?",
    "exclusion-gloss": "what are the top 3 categories by sales_value_myr (excluding Electronics)?",
    "direction": "show the top 3 categories with the lowest sales_value_myr",
    "average": "what are the top 3 categories by average sales_value_myr?",
    "percentage": (
        "what are the top 3 categories by sales_value_myr, as a percentage of the total?"
    ),
    "count-measure": "what are the top 2 categories by number of SKUs?",
    "grain": "what is the top 1 SKU per category by sales_value_myr?",
    "extra-filter": "what are the top 3 categories by sales_value_myr for sku SKU-3?",
    "time": "what are the top 3 categories by sales_value_myr in 2025?",
    "total-excluding": "what is total sales_value_myr, excluding returns, for sku SKU-1?",
}


@pytest.mark.parametrize("tail", list(UNHONORED.values()), ids=list(UNHONORED))
def test_ignored_clause_abstains_by_name_not_a_plain_sum(db: Path, tail: str) -> None:
    env = _ask(db, tail)
    assert env["abstained"] is True, env["text"]
    assert env["badge"] == "ABSTAIN"
    assert env["route"] == "abstain"
    assert not env["rows"] and not env["values"]
    assert any("bronze_sheet_unhonored:" in a for a in env["assumptions"]), env["assumptions"]
    # no figure from the table may reach the customer
    for figure in ("1500", "2000", "900", "300", "100"):
        assert figure not in (env["text"] or ""), env["text"]
    assert_envelope_valid(env)


def test_the_abstain_names_the_kind_of_clause(db: Path) -> None:
    kinds = {
        name: next(a for a in _ask(db, tail)["assumptions"] if "bronze_sheet_unhonored:" in a)
        for name, tail in UNHONORED.items()
    }
    assert kinds["exclusion"].endswith(":exclusion")
    assert kinds["direction"].endswith(":direction")
    assert kinds["average"].endswith(":shape") or kinds["average"].endswith(":aggregation")
    assert kinds["time"].endswith(":shape") or kinds["time"].endswith(":time")


# The lane's own contract: these phrasings must keep answering with an L0 figure.
SUPPORTED = {
    "plain-top-n": "what are the top 3 categories by sales_value_myr?",
    "show": "show the top 5 categories by sales_value_myr.",
    "no-question-mark": "top 3 categories by sales_value_myr",
    "product-family-synonym": (
        "top 3 product families by MYR sales (cat / product line synonym for category)?"
    ),
    "sheet-only-ignore-other-sheet": (
        "on the Sales sheet only (ignore Wide_Fill), what are the top 3 categories "
        "by sales_value_myr?"
    ),
}


@pytest.mark.parametrize("tail", list(SUPPORTED.values()), ids=list(SUPPORTED))
def test_supported_top_n_shapes_still_answer(db: Path, tail: str) -> None:
    env = _ask(db, tail)
    assert env["abstained"] is False, env["text"]
    assert env["badge"] == "L0_CERTIFIED"
    assert [r["category"] for r in env["rows"]][:3] == ["Electronics", "Home", "Sports"]
    assert env["rows"][0]["sales_value_myr"] == 2000.0
    assert_envelope_valid(env)


def test_supported_total_shape_still_answers(db: Path) -> None:
    env = _ask(db, "what is total sales_value_myr for city Kuala Lumpur?")
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"][0]["sales_value_myr"] == 2500.0
    assert_envelope_valid(env)


def test_malay_phrasing_still_answers(db: Path) -> None:
    env = maybe_bronze_sheet_ask(
        f"Dalam fail {WB} helaian Sales, apakah 3 kategori teratas mengikut sales_value_myr?",
        warehouse=db,
    )
    assert env is not None and env["abstained"] is False
    assert [r["category"] for r in env["rows"]] == ["Electronics", "Home", "Sports"]


def test_empty_filter_result_still_abstains_via_rule_12(db: Path) -> None:
    env = _ask(db, "what is total sales_value_myr for sku SKU-X?")
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
