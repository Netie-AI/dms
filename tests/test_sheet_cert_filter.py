"""SHEET-CERT-FILTER-01: L0_CERTIFIED only when the ask is grounded in the shape.

A top-N sheet question that adds a filter, a negation, or a period-then-qualifier
abstains ``ungrounded_qualifier:<word>``. The same shape with no extra content
word still serves L0_CERTIFIED. Executor.live_ask, no keys.
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
RANK_FILE = "f32_ambiguous_scope.xlsx"
FILTER_FILE = "encoding_value_norm.xlsx"
_PREFIX = f"In {RANK_FILE} on the Sales sheet, "

_GROUNDED = (
    _PREFIX + "what are the top 3 categories by sales_value_myr?",
    _PREFIX + "what are the top 3 categories by sales_value_myr.",
    _PREFIX + "show me the top 3 categories by sales_value_myr",
)

# Human phrasings a certified sheet entry does not cover. Word is the first
# content token the shape does not ground.
_UNGROUNDED = (
    (
        "what are the top 3 categories by sales_value_myr, excluding Electronics?",
        "excluding",
    ),
    (
        "show the top 3 categories with the lowest sales_value_myr",
        "lowest",
    ),
    (
        "what are the top 3 categories by sales_value_myr. Exclude Electronics.",
        "exclude",
    ),
    (
        "what are the top 3 categories by sales_value_myr that are not Electronics.",
        "not",
    ),
    (
        "what are the top 3 categories by sales_value_myr except Home?",
        "except",
    ),
    (
        "what are the top 3 categories by sales_value_myr for sku SKU-3?",
        "sku",
    ),
    (
        "what are the top 3 categories by sales_value_myr in 2024?",
        "2024",
    ),
    (
        "what are the top 3 categories by sales_value_myr where category is Misc?",
        "where",
    ),
    (
        "what are the top 3 categories by sales_value_myr without Sports?",
        "without",
    ),
    (
        "what are the top 3 categories by sales_value_myr, ignoring Misc.",
        "ignoring",
    ),
    (
        "top 3 product families by MYR sales excluding Electronics?",
        "excluding",
    ),
    (
        "what are the top 3 categories by sales_value_myr for Q1.",
        "q1",
    ),
)


class _Stub:
    """Cortex stand-in. The bronze lane returns before ask()."""

    def submit(self, _req: Any) -> Any:
        from cortex_contract.execution import QueryResult

        return QueryResult(ok=True, status="bound", run_id="run-sheet-cert")

    def ask(self, _req: Any) -> AskResponse:
        return AskResponse(
            answer="I cannot answer that here.",
            abstained=True,
            badge="abstain",
            rows=[],
            route="abstain",
            audit_id="aud-stub",
        )


def _install(con: duckdb.DuckDBPyConnection, filename: str, ddl: str, values: str) -> str:
    table = bronze_table_for_sheet(filename, "Sales")
    ident = table.split(".", 1)[-1]
    con.execute(f'CREATE TABLE bronze."{ident}" ({ddl})')
    con.execute(f'INSERT INTO bronze."{ident}" VALUES {values}')
    con.execute(
        """
        INSERT INTO bronze._ingest_registry
          (table_name, filename, sha256, ingest_id, created_at, space_id)
        VALUES (?, ?, 'abc', 'ing-sheet-cert', now(), ?)
        """,
        [ident, filename, FINANCE],
    )
    return table


def _seed(path: Path) -> str:
    con = duckdb.connect(str(path))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        _ensure_registry(con)
        rank = _install(
            con,
            RANK_FILE,
            "category VARCHAR, sales_value_myr DOUBLE",
            "('Electronics', 1545366.40), ('Home', 1199018.49), "
            "('Misc', 380948.33), ('Sports', 300000.0)",
        )
        _install(
            con,
            FILTER_FILE,
            "sku VARCHAR, city VARCHAR, sales_value_myr DOUBLE",
            "('SKU-BETA', 'Kuala Lumpur', 1500.75), "
            "('SKU-ALPHA', 'Kuala Lumpur', 200.00)",
        )
    finally:
        con.close()
    return rank


def _point(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.delenv("CORTEX_WAREHOUSE_DB", raising=False)
    monkeypatch.delenv("DMS_ORACLE_WAREHOUSE", raising=False)
    monkeypatch.delenv("CORTEX_HOME", raising=False)
    monkeypatch.delenv("DMS_CCA_CASCADE", raising=False)


def _ask(
    path: Path,
    question: str,
    *,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    env = Executor(cortex=_Stub(), warehouse_path=path).live_ask(  # type: ignore[arg-type]
        question,
        space_id=FINANCE,
        session_id="ses_sheet_cert",
        tables=tables,
    )
    assert_envelope_valid(env)
    return env


@pytest.fixture
def wh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "wh.duckdb"
    _seed(path)
    _point(monkeypatch, path)
    return path


def _assert_certified_top3(env: dict[str, Any]) -> None:
    assert env["badge"] == "L0_CERTIFIED" and env["abstained"] is False
    assert env["route"] == "bronze_sheet"
    sql = str(env.get("sql_used") or "")
    assert "GROUP BY 1 ORDER BY 2 DESC LIMIT 3" in sql
    cats = [r["category"] for r in env["rows"]]
    assert cats == ["Electronics", "Home", "Misc"]
    assert env["rows"][0]["sales_value_myr"] == 1545366.40


@pytest.mark.parametrize("question", _GROUNDED)
def test_fully_grounded_certified_question_stays_l0(wh: Path, question: str) -> None:
    _assert_certified_top3(_ask(wh, question))


def test_scope_prefix_is_not_an_ungrounded_qualifier(wh: Path) -> None:
    """live_ask may prepend ``Using only <table>:``. That is not user content."""
    table = bronze_table_for_sheet(RANK_FILE, "Sales")
    question = _GROUNDED[0]
    assert table.split(".", 1)[-1].lower() not in question.lower()
    _assert_certified_top3(_ask(wh, question, tables=[table]))


@pytest.mark.parametrize(("suffix", "word"), _UNGROUNDED)
def test_ungrounded_qualifier_abstains(wh: Path, suffix: str, word: str) -> None:
    env = _ask(wh, _PREFIX + suffix)
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["route"] == "abstain"
    assert env.get("sql_used") in (None, "")
    assert not env["rows"]
    assert f"ungrounded_qualifier:{word}" in str(env.get("text") or "")
    assert "1545366" not in str(env.get("text") or "")


def test_grounded_sku_filter_stays_l0(wh: Path) -> None:
    env = _ask(
        wh,
        f"In {FILTER_FILE} sheet Sales, what is total sales_value_myr for sku SKU-BETA?",
    )
    assert env["badge"] == "L0_CERTIFIED" and env["abstained"] is False
    assert env["route"] == "bronze_sheet"
    assert env["rows"][0]["sku"] == "SKU-BETA"
    assert env["rows"][0]["sales_value_myr"] == 1500.75


def test_filter_shape_drops_nothing_it_does_not_cover(wh: Path) -> None:
    env = _ask(
        wh,
        f"In {FILTER_FILE} sheet Sales, excluding returns, what is total "
        "sales_value_myr for sku SKU-BETA?",
    )
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert "ungrounded_qualifier:excluding" in str(env.get("text") or "")
    assert "1500.75" not in str(env.get("text") or "")
    assert not env["rows"]
