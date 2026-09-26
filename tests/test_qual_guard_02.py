"""QUAL-GUARD-01 R3 / dms#290: coverage is structural, not a text match.

The dms#290 Epic stamp listed what the merged guard could not prove: a grain
word anywhere in the plan counted, a named filter matched anywhere in the SQL,
and "last N months" passed on any WHERE clause. Each case below is an answer
to a different question that the text match passed; each must now be the named
abstain, asserted on the envelope (rendered text, rows, badge, no chart).

Seeded fixtures only. No LLM, no network, no keys.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.qualifiers import unhonored_qualifier_reason
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import Ontology

# (question, executed SQL the text match passed, named reason)
DROPPED: tuple[tuple[str, str, str], ...] = (
    # "day" inside lead_time_days is not a day bucket.
    (
        "What is revenue per day?",
        "SELECT lead_time_days, SUM(amount) AS revenue FROM sales GROUP BY lead_time_days",
        "unhonored_qualifier:time_grain=day",
    ),
    # month() in a WHERE filter is not a month bucket.
    (
        "What is revenue per month?",
        "SELECT SUM(amount) AS revenue FROM sales WHERE month(ts) = 1",
        "unhonored_qualifier:time_grain=month",
    ),
    # A filter value echoed as a SELECT label is not a filter.
    (
        "What is revenue for 'ACME'?",
        "SELECT 'ACME' AS supplier, SUM(amount) AS revenue FROM sales",
        "unhonored_qualifier:named_filter=ACME",
    ),
    # "last 3 months" over an unrelated WHERE is not a time window.
    (
        "What is revenue in the last 3 months?",
        "SELECT SUM(amount) AS revenue FROM sales WHERE region = 'North'",
        "unhonored_qualifier:time_filter=last_3_month",
    ),
    # supplier_id in the SELECT but grouped by sku: rows keyed by the wrong thing.
    (
        "What is revenue by supplier?",
        "SELECT MAX(supplier_id) AS supplier_id, sku, SUM(amount) AS revenue "
        "FROM sales GROUP BY sku",
        "unhonored_qualifier:group_by=supplier",
    ),
    # A year only in the SELECT list is not a year filter.
    (
        "What was revenue in 2023?",
        "SELECT 2023 AS year, SUM(amount) AS revenue FROM sales",
        "unhonored_qualifier:time_filter=year=2023",
    ),
)

HONOURED: tuple[tuple[str, str], ...] = (
    (
        "What is revenue per month?",
        "SELECT date_trunc('month', ts) AS m, SUM(amount) AS revenue FROM sales GROUP BY 1",
    ),
    (
        "What is revenue per day?",
        "SELECT CAST(ts AS DATE) AS day, SUM(amount) AS revenue FROM sales GROUP BY day",
    ),
    (
        "What is revenue for 'ACME'?",
        "SELECT SUM(amount) AS revenue FROM sales WHERE supplier_id = 'ACME'",
    ),
    (
        "What is revenue in the last 3 months?",
        "SELECT SUM(amount) AS revenue FROM sales "
        "WHERE CAST(ts AS DATE) >= CURRENT_DATE - INTERVAL 3 MONTH",
    ),
    (
        "What is revenue by supplier?",
        "SELECT supplier_id, SUM(amount) AS revenue FROM sales GROUP BY 1",
    ),
    (
        "What was revenue in 2023?",
        "SELECT SUM(amount) AS revenue FROM sales WHERE year(ts) = 2023",
    ),
)

# Typed plans: a grain word in the measure name or a note is not a bucket;
# a filter value in the measure description is not a filter.
PLAN_DROPPED: tuple[tuple[str, dict[str, Any], str], ...] = (
    (
        "What is revenue per month?",
        {"measure": "monthly_revenue", "group_by": [], "filters": []},
        "unhonored_qualifier:time_grain=month",
    ),
    (
        "What is revenue by supplier?",
        {"measure": "supplier_risk", "group_by": [["product", "sku"]], "filters": []},
        "unhonored_qualifier:group_by=supplier",
    ),
    (
        "What is revenue in the last 3 months?",
        {
            "measure": "revenue",
            "group_by": [],
            "filters": [["sale", "region", "=", "North"]],
        },
        "unhonored_qualifier:time_filter=last_3_month",
    ),
)


@pytest.mark.parametrize(("question", "sql", "reason"), DROPPED)
def test_sql_that_drops_a_qualifier_is_named(question: str, sql: str, reason: str) -> None:
    assert unhonored_qualifier_reason(question, sql=sql) == reason


@pytest.mark.parametrize(("question", "sql"), HONOURED)
def test_sql_that_honours_the_qualifier_passes(question: str, sql: str) -> None:
    assert unhonored_qualifier_reason(question, sql=sql) is None


@pytest.mark.parametrize(("question", "plan", "reason"), PLAN_DROPPED)
def test_plan_mentioning_a_qualifier_is_not_honouring_it(
    question: str, plan: dict[str, Any], reason: str
) -> None:
    assert unhonored_qualifier_reason(question, plan=plan) == reason


# ---------------------------------------------------------------------------
# Envelope: generate returns SQL the text match passed. The customer sees the
# named abstain, no rows, no green badge, no chart. The honoured variant
# answers with its rows (the guard does not over-block).
# ---------------------------------------------------------------------------


def _ontology() -> Ontology:
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_measure("revenue", "sale", "SUM(f.amount)")
    return o


def _seed(path: Path) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE sales (txn_id VARCHAR, sku VARCHAR, supplier_id VARCHAR, "
            "region VARCHAR, lead_time_days INTEGER, amount DOUBLE, ts DATE)"
        )
        con.execute(
            "INSERT INTO sales VALUES "
            "('T1','SKU-1','ACME','North',3,100,'2024-01-15'),"
            "('T2','SKU-2','BOLT','South',5,50,'2024-02-15'),"
            "('T3','SKU-1','ACME','North',3,70,'2024-02-20')"
        )
    finally:
        con.close()


def _ask(db: Path, question: str, sql: str) -> dict[str, Any]:
    import duckdb

    def submit(q: str) -> Any:
        con = duckdb.connect(str(db), read_only=True)
        try:
            cur = con.execute(q)
            cols = [str(c[0]) for c in cur.description or []]
            rows = [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(cols, r)}
                for r in cur.fetchall()
            ]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_qg02", output={"rows": rows})

    onto = load_verified_ontology(db, _ontology())
    assert onto is not None
    env = maybe_generative_ask(
        question,
        warehouse=db,
        grantable={"sales"},
        compute=lambda _c: {
            "query_sql": sql,
            "plan_source": "ontology_plan",
            "plan_origin": "generate_sql",
            "generate_legs": {"count": 1, "legs": [{"returned": "sql"}]},
        },
        submit=submit,
        ledger_append=lambda _p: SimpleNamespace(entry_id="led_qg02", hash="hash_qg02_x"),
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert_envelope_valid(env)
    return env


@pytest.mark.parametrize(
    ("question", "sql", "reason"),
    [DROPPED[0], DROPPED[2], DROPPED[3]],
    ids=["day_in_lead_time_days", "filter_as_label", "last_3_months_any_where"],
)
def test_envelope_abstains_named_with_no_chart(
    tmp_path: Path, question: str, sql: str, reason: str
) -> None:
    db = tmp_path / "qg02.duckdb"
    _seed(db)
    env = _ask(db, question, sql)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert not env.get("chart")
    assert reason in str(env.get("text") or "")
    assert reason in " ".join(str(a) for a in env.get("assumptions") or [])
    # The ungrouped/unfiltered total never reaches the customer.
    for leaked in ("220", "170"):
        assert leaked not in str(env.get("text") or "")


def test_envelope_answers_when_filter_is_a_predicate(tmp_path: Path) -> None:
    db = tmp_path / "qg02.duckdb"
    _seed(db)
    env = _ask(
        db,
        "What is revenue for 'ACME'?",
        "SELECT supplier_id, SUM(amount) AS revenue FROM sales "
        "WHERE supplier_id = 'ACME' GROUP BY supplier_id",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"] == [{"supplier_id": "ACME", "revenue": 170.0}]
    assert "revenue=170.0" in str(env.get("text") or "")
    assert "unhonored_qualifier" not in str(env.get("assumptions") or [])
