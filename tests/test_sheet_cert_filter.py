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
        "in",
    ),
    (
        "what are the top 3 categories by sales_value_myr at WH-B?",
        "at",
    ),
    (
        "what are the top 3 categories by sales_value_myr from the archive?",
        "from",
    ),
    (
        "what are the top 3 categories by sales_value_myr, bottom first?",
        "bottom",
    ),
    (
        "what are the top 3 categories by sales_value_myr, least first?",
        "least",
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
            "category VARCHAR, sales_value_myr DOUBLE, stock_value_myr DOUBLE",
            "('Electronics', 1545366.40, 10.0), ('Home', 1199018.49, 50.0), "
            "('Misc', 380948.33, 30.0), ('Sports', 300000.0, 40.0)",
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


# Bare phrases are not a bronze hit. The certified entry is this sheet scope
# plus the phrase. Stock totals differ from sales so a sales default fails.
_STOCK_Q = _PREFIX + "top 3 categories by stock value"
_PAIR_RULES = (
    ("top 3 RAW by stock value excluding WH-B", "raw"),
    ("top 3 parts by value last month", "parts"),
    ("top 3 RAW, lowest first", "raw"),
    ("top 3 categories in March", "in"),
    ("top 3 categories in WH-B", "in"),
    ("top 3 categories by stock value 2025", "2025"),
    ("top 3 categories per 10023", "per"),
)


def test_top3_categories_by_stock_value_stays_l0(wh: Path) -> None:
    assert "top 3 categories by stock value" in _STOCK_Q
    env = _ask(wh, _STOCK_Q)
    assert env["badge"] == "L0_CERTIFIED" and env["abstained"] is False
    assert env["route"] == "bronze_sheet"
    sql = str(env.get("sql_used") or "")
    assert "stock_value_myr" in sql
    assert "GROUP BY 1 ORDER BY 2 DESC LIMIT 3" in sql
    assert [r["category"] for r in env["rows"]] == ["Home", "Sports", "Misc"]
    assert env["rows"][0]["stock_value_myr"] == 50.0


@pytest.mark.parametrize(("phrase", "word"), _PAIR_RULES)
def test_connector_and_digit_rules_abstain(wh: Path, phrase: str, word: str) -> None:
    env = _ask(wh, _PREFIX + phrase)
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["route"] == "abstain"
    assert env.get("sql_used") in (None, "")
    assert not env["rows"]
    assert f"ungrounded_qualifier:{word}" in str(env.get("text") or "")
    assert "1545366" not in str(env.get("text") or "")
    assert "50.0" not in str(env.get("text") or "")


def test_never_free_words_stay_off_the_allow_list() -> None:
    from dms_executor.bronze_sheet_ask import (
        _CERTIFIED_NO_GROUND,
        _CONNECTORS,
        _NEVER_FREE,
    )

    assert _CERTIFIED_NO_GROUND == frozenset(
        {
            "a",
            "an",
            "the",
            "of",
            "on",
            "for",
            "to",
            "with",
            "and",
            "or",
            "what",
            "are",
            "is",
            "show",
            "me",
            "please",
            "our",
            "this",
            "that",
            "using",
            "only",
            "sheet",
            "top",
            "teratas",
        }
    )
    assert _NEVER_FREE.isdisjoint(_CERTIFIED_NO_GROUND)
    assert _CONNECTORS.isdisjoint(_CERTIFIED_NO_GROUND)
    for word in (
        "bottom",
        "least",
        "lowest",
        "smallest",
        "terendah",
        "terbawah",
        "highest",
        "biggest",
        "worst",
        "last",
    ):
        assert word not in _CERTIFIED_NO_GROUND


# Ranked-N that is not ``top N``. On 7f3c13d6 these miss the lane and fall
# through. Head must abstain on the direction word before any answer SQL.
_NON_TOP_RANK = (
    ("bottom 3 categories by stock value", "bottom"),
    ("least 3 categories by stock value", "least"),
    ("lowest 3 categories by stock value", "lowest"),
    ("smallest 3 categories by stock value", "smallest"),
    ("3 kategori terendah mengikut nilai stok", "terendah"),
    ("bottom three categories by stock value", "bottom"),
    ("3 lowest categories by stock value", "lowest"),
)


@pytest.mark.parametrize(("phrase", "word"), _NON_TOP_RANK)
def test_non_top_rank_abstains_before_sql(
    wh: Path, monkeypatch: pytest.MonkeyPatch, phrase: str, word: str
) -> None:
    def _no_sql(*_a: object, **_k: object) -> None:
        raise AssertionError("answer SQL path opened")

    # _grouped_top_n and _eq_filter_total both go through this before execute.
    monkeypatch.setattr("dms_executor.bronze_sheet_ask._db_with_table", _no_sql)
    env = _ask(wh, _PREFIX + phrase)
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["route"] == "abstain"
    assert env.get("lane") in (None, "")
    assert env.get("sql_used") in (None, "")
    assert not env["rows"]
    assert not env.get("values")
    blob = str(env.get("text") or "") + " " + " ".join(
        str(a) for a in (env.get("assumptions") or [])
    )
    assert f"ungrounded_qualifier:{word}" in blob
    assert "ov_service_token_missing" not in blob
    assert "ORDER BY" not in blob
    assert "50.0" not in blob
    assert "1545366" not in blob


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
