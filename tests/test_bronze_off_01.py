"""BRONZE-OFF-01: the hand-coded sheet lane is off unless DMS_LANE_BRONZE_SHEET is set.

On f9ffc3e1 these phrasings come back L0_CERTIFIED from route bronze_sheet
with zero model calls. With the flag unset they must not. Synthetic sheets
only. The stub Cortex has no model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskResponse
from dms_executor import Executor
from dms_executor.bronze import _ensure_registry, bronze_table_for_sheet
from dms_executor.envelope import assert_envelope_valid
from dms_executor.lake_schema import ensure_lake_schemas

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
TOP_FILE = "sheetbook.xlsx"
TOP_SHEET = "Data"
FILTER_FILE = "itemlist.xlsx"
FILTER_SHEET = "Lines"

# Surface forms of one stock-value ranking. The sheet has no stock column.
TOP_FORMS = [
    "In sheetbook.xlsx sheet Data, what are the top 3 categories by stock value?",
    "In sheetbook.xlsx sheet Data, top 3 categories by stock value.",
    "In sheetbook.xlsx sheet Data, top 3 categories by stock value!",
    "In sheetbook.xlsx sheet Data, top 3 categories by stock value??",
    "Using sheetbook.xlsx, on the Data sheet, top 3 categories by stock value",
    "sheetbook.xlsx sheet Data: top 3 categories by stock value",
    "In sheetbook.xlsx sheet Data, top 3 categories by stock value and sales value?",
    "In sheetbook.xlsx sheet Data, top 3 categories by stock value and sales_value_myr?",
    "In sheetbook.xlsx sheet Data, what are the top 3 categories?",
    "In sheetbook.xlsx sheet Data, top 3 product families by stock value?",
    "Dalam sheetbook.xlsx helaian Data, 3 kategori teratas by stock value?",
    "In sheetbook.xlsx, on the Data sheet only, top 3 categories by stock value",
]

# Filtered total for one item key. The sheet has no stock column.
FILTER_FORMS = [
    "In itemlist.xlsx sheet Lines, what is total sales_value_myr for sku SKU-BETA?",
    "In itemlist.xlsx sheet Lines, total myr sales for sku SKU-BETA?",
    "In itemlist.xlsx on the Lines sheet, total sales_value_myr for sku SKU-BETA",
]

ON_VALUES = ["1", "true", "yes", "on", "TRUE", "Yes", "On"]
OFF_VALUES = ["", "0", "false", "no", "off", "OFF"]


class _Stub:
    """Cortex stand-in. No compute_insights, so no model call."""

    def __init__(self) -> None:
        self.asks = 0

    def submit(self, _req: Any) -> Any:
        from cortex_contract.execution import QueryResult

        return QueryResult(ok=True, status="bound", run_id="run-bronze-off")

    def ask(self, _req: Any) -> AskResponse:
        self.asks += 1
        return AskResponse(
            answer="I cannot answer that here.",
            abstained=True,
            badge="abstain",
            rows=[],
            route="abstain",
            audit_id="aud-stub",
        )


def _insert(
    con: duckdb.DuckDBPyConnection,
    filename: str,
    sheet: str,
    columns: str,
    rows: list[tuple[Any, ...]],
    *,
    space_id: str | None,
) -> str:
    table = bronze_table_for_sheet(filename, sheet)
    ident = table.split(".", 1)[-1]
    con.execute(f'CREATE TABLE bronze."{ident}" ({columns})')
    placeholders = ", ".join(["?"] * len(rows[0]))
    for row in rows:
        con.execute(f'INSERT INTO bronze."{ident}" VALUES ({placeholders})', list(row))
    if space_id:
        con.execute(
            """
            INSERT INTO bronze._ingest_registry
              (table_name, filename, sha256, ingest_id, created_at, space_id)
            VALUES (?, ?, 'abc', 'ing-bronze-off', now(), ?)
            """,
            [ident, filename, space_id],
        )
    return table


def _warehouse(path: Path, *, space_id: str | None) -> None:
    con = duckdb.connect(str(path))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        _ensure_registry(con)
        _insert(
            con,
            TOP_FILE,
            TOP_SHEET,
            "category VARCHAR, sales_value_myr DOUBLE",
            [("Electronics", 88001.25), ("Home", 20.0), ("Sports", 5.0)],
            space_id=space_id,
        )
        _insert(
            con,
            FILTER_FILE,
            FILTER_SHEET,
            "sku VARCHAR, sales_value_myr DOUBLE",
            [("SKU-BETA", 1500.75), ("SKU-ALPHA", 10.0)],
            space_id=space_id,
        )
    finally:
        con.close()


def _point(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.delenv("CORTEX_WAREHOUSE_DB", raising=False)
    monkeypatch.delenv("DMS_ORACLE_WAREHOUSE", raising=False)
    monkeypatch.delenv("CORTEX_HOME", raising=False)
    monkeypatch.delenv("DMS_CCA_CASCADE", raising=False)
    monkeypatch.delenv("DMS_LANE_BRONZE_SHEET", raising=False)


def _ask(
    path: Path,
    question: str,
    *,
    space_id: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    _point(monkeypatch, path)
    stub = _Stub()
    env = Executor(cortex=stub, warehouse_path=path).live_ask(  # type: ignore[arg-type]
        question,
        space_id=space_id,
        session_id="ses_bronze_off",
    )
    assert_envelope_valid(env)
    assert env.get("model_calls") == 0
    assert stub.asks == 0 or env.get("route") != "bronze_sheet"
    return env


def _not_bronze(env: dict[str, Any], question: str) -> None:
    assert env.get("route") != "bronze_sheet", question
    assert env.get("lane") != "bronze", question
    assert env.get("badge") != "L0_CERTIFIED", question
    assert "88001.25" not in str(env.get("text") or "")
    assert "1500.75" not in str(env.get("text") or "")


@pytest.mark.parametrize("question", TOP_FORMS)
def test_stock_ranking_is_not_bronze_when_flag_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    db = tmp_path / "top.duckdb"
    _warehouse(db, space_id=FINANCE)
    env = _ask(db, question, space_id=FINANCE, monkeypatch=monkeypatch)
    _not_bronze(env, question)


@pytest.mark.parametrize("question", FILTER_FORMS)
def test_filtered_total_is_not_bronze_when_flag_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    db = tmp_path / "filt.duckdb"
    _warehouse(db, space_id=FINANCE)
    env = _ask(db, question, space_id=FINANCE, monkeypatch=monkeypatch)
    _not_bronze(env, question)


@pytest.mark.parametrize("raw", OFF_VALUES)
def test_off_spellings_leave_the_lane_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    db = tmp_path / "off.duckdb"
    _warehouse(db, space_id=FINANCE)
    _point(monkeypatch, db)
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", raw)
    env = Executor(cortex=_Stub(), warehouse_path=db).live_ask(  # type: ignore[arg-type]
        TOP_FORMS[0],
        space_id=FINANCE,
        session_id="ses_bronze_off",
    )
    assert_envelope_valid(env)
    assert env.get("model_calls") == 0
    _not_bronze(env, raw)


@pytest.mark.parametrize("raw", ON_VALUES)
def test_flag_on_bronze_envelope_carries_plan_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    db = tmp_path / "on.duckdb"
    _warehouse(db, space_id=FINANCE)
    _point(monkeypatch, db)
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", raw)
    env = Executor(cortex=_Stub(), warehouse_path=db).live_ask(  # type: ignore[arg-type]
        TOP_FORMS[0],
        space_id=FINANCE,
        session_id="ses_bronze_off",
    )
    assert_envelope_valid(env)
    assert env.get("model_calls") == 0
    assert env["route"] == "bronze_sheet"
    assert env["lane"] == "bronze"
    assert env["plan_origin"] == "bronze_sheet"
    assert env["served_attribution"] == "none"
    assert env["badge"] == "L0_CERTIFIED"


def test_flag_on_filtered_total_carries_plan_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "on-filt.duckdb"
    _warehouse(db, space_id=FINANCE)
    _point(monkeypatch, db)
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", "1")
    env = Executor(cortex=_Stub(), warehouse_path=db).live_ask(  # type: ignore[arg-type]
        FILTER_FORMS[0],
        space_id=FINANCE,
        session_id="ses_bronze_off",
    )
    assert_envelope_valid(env)
    assert env.get("model_calls") == 0
    assert env["route"] == "bronze_sheet"
    assert env["lane"] == "bronze"
    assert env["plan_origin"] == "bronze_sheet"
    assert env["served_attribution"] == "none"


def test_flag_off_ungranted_table_still_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Safety refusal. No rows. The flag does not open another table."""
    db = tmp_path / "ungranted.duckdb"
    _warehouse(db, space_id=None)
    table = bronze_table_for_sheet(TOP_FILE, TOP_SHEET)
    env = _ask(db, TOP_FORMS[0], space_id=OPS, monkeypatch=monkeypatch)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert f"ungranted_table:{table}" in env["text"]
    assert not env["rows"]
    assert env.get("route") != "bronze_sheet"
    assert env.get("lane") != "bronze"


def test_flag_off_no_space_still_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "nospace.duckdb"
    _warehouse(db, space_id=FINANCE)
    env = _ask(db, TOP_FORMS[0], space_id=None, monkeypatch=monkeypatch)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert "no_space" in env["text"]
    assert not env["rows"]
    assert env.get("route") != "bronze_sheet"
