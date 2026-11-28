"""DMS_BIG_SCHEMA is off unless the value is the digit 1.

On, the AI floor sends a retrieved schema slice, caps the result at 200
rows, and repairs a bad column from the database error plus the schema.
The 300-table catalog stays out of the prompt. Aggregates run in the
database. WRONG stays 0 because this flag does not serve a rule lane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cortex_contract.execution import QueryResult
from dms_executor.ai_floor import run_floor
from dms_executor.big_schema import (
    ROW_CAP,
    TOKEN_BUDGET,
    bound_rows,
    enabled,
    prepare_sql,
    pushes_aggregation,
    retrieve,
)
from dms_executor.demo_warehouse import connect_file
from dms_executor.envelope import assert_envelope_valid

FACT = "fact_revenue"
DIM = "dim_account"
BRIDGE = "junc_rev_acct"
NEEDED = (FACT, DIM, BRIDGE)
SENTINEL = "424242"
JOIN_Q = "What is revenue amount by customer account region?"
RANK_Q = "Which customer account ranks highest by revenue amount?"
TREND_Q = "How did revenue amount change by month for each customer account region?"
AGG_SQL = (
    "SELECT dim_account.region AS region, SUM(fact_revenue.amount) AS total "
    "FROM fact_revenue "
    "JOIN junc_rev_acct ON fact_revenue.rev_id = junc_rev_acct.left_key "
    "JOIN dim_account ON dim_account.acct_id = junc_rev_acct.right_key "
    "GROUP BY dim_account.region "
    "ORDER BY dim_account.region"
)
NORTH = 10 + 20 + 30 + 424242
SOUTH = 5 + 15


def _noise_column(index: int) -> dict[str, str]:
    return {"name": f"zz_col_{index}", "type": "VARCHAR", "description": "locker bin tag"}


def _catalog() -> dict[str, Any]:
    """301 tables. Three of them answer the questions. The bridge has no shared words."""
    noise = []
    for index in range(290):
        noise.append(
            {
                "name": f"noise_{index:03d}",
                "description": "unrelated facility locker bin tag " * 4,
                "columns": [_noise_column(col) for col in range(6)],
            }
        )
    notes = []
    for index in range(8):
        notes.append(
            {
                "name": f"revenue_note_{index:02d}",
                "description": "revenue memo",
                "columns": [{"name": "memo", "type": "VARCHAR", "description": "revenue memo"}],
            }
        )
    signal = [
        {
            "name": FACT,
            "description": "revenue amount by month",
            "columns": [
                {"name": "rev_id", "type": "INTEGER", "primary_key": True},
                {"name": "revenue_month", "type": "DATE", "description": "month"},
                {
                    "name": "amount",
                    "type": "INTEGER",
                    "description": "revenue amount",
                    "samples": ["10", "20", "30"],
                },
            ],
        },
        {
            "name": DIM,
            "description": "customer account region",
            "columns": [
                {"name": "acct_id", "type": "INTEGER", "primary_key": True},
                {"name": "region", "type": "VARCHAR", "samples": ["north", "south"]},
                {"name": "account_name", "type": "VARCHAR", "description": "customer account"},
            ],
        },
        {
            "name": BRIDGE,
            "description": "paired surrogate keys",
            "columns": [
                {"name": "left_key", "type": "INTEGER"},
                {"name": "right_key", "type": "INTEGER"},
            ],
        },
    ]
    return {
        "datasets": signal + notes + noise,
        "relationships": [
            {
                "from": BRIDGE,
                "from_column": "left_key",
                "to": FACT,
                "to_column": "rev_id",
            },
            {
                "from": BRIDGE,
                "from_column": "right_key",
                "to": DIM,
                "to_column": "acct_id",
            },
        ],
        "measures": [
            {"name": "revenue", "grain": FACT, "expression": "SUM(amount)"},
        ],
    }


def test_flag_accepts_only_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_BIG_SCHEMA", raising=False)
    assert enabled() is False
    for token in ("", "0", "true", "yes", "on", "2"):
        monkeypatch.setenv("DMS_BIG_SCHEMA", token)
        assert enabled() is False
    monkeypatch.setenv("DMS_BIG_SCHEMA", "1")
    assert enabled() is True


def test_300_tables_retrieve_the_three_and_stay_under_budget(
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = _catalog()
    assert len(catalog["datasets"]) >= 300
    lines = []
    for label, question in (("join", JOIN_Q), ("ranking", RANK_Q), ("trend", TREND_Q)):
        got = retrieve(question, catalog)
        assert set(NEEDED) <= set(got.picked)
        assert "noise_000" not in got.prompt
        assert "noise_000" not in got.picked
        assert SENTINEL not in got.prompt
        assert "samples: 10, 20, 30" in got.prompt
        assert f"measure revenue grain={FACT}" in got.prompt
        assert f"join {BRIDGE}.left_key = {FACT}.rev_id" in got.prompt
        assert got.tokens <= TOKEN_BUDGET
        assert got.full_tokens > TOKEN_BUDGET
        assert got.tokens < got.full_tokens
        assert got.next_names
        assert all(name.startswith("revenue_note_") for name in got.next_names)
        lines.append(f"TOKENS {label} before={got.full_tokens} after={got.tokens}")
    print("\n".join(lines))
    captured = capsys.readouterr()
    assert "TOKENS join before=" in captured.out


def test_tiny_budget_still_fits() -> None:
    got = retrieve(JOIN_Q, _catalog(), max_tokens=40)
    assert got.tokens <= 40
    assert SENTINEL not in got.prompt


def test_prepare_sql_is_read_only_and_capped() -> None:
    dropped, why = prepare_sql("DROP TABLE fact_revenue")
    assert dropped is None
    assert why == "read_only"
    inserted, why = prepare_sql("INSERT INTO fact_revenue VALUES (1, DATE '2024-01-01', 1)")
    assert inserted is None
    assert why == "read_only"
    multi, why = prepare_sql("SELECT 1; SELECT 2")
    assert multi is None
    assert why == "read_only"
    capped, why = prepare_sql("SELECT amount FROM fact_revenue")
    assert why is None
    assert capped is not None
    assert "LIMIT 200" in capped
    assert "SELECT amount FROM fact_revenue" in capped
    tight, why = prepare_sql("SELECT amount FROM fact_revenue LIMIT 10")
    assert why is None
    assert tight == "SELECT amount FROM fact_revenue LIMIT 10"
    wide, why = prepare_sql("SELECT amount FROM fact_revenue LIMIT 5000")
    assert why is None
    assert wide is not None
    assert "LIMIT 200" in wide
    rows, count, hit = bound_rows([{"n": index} for index in range(500)])
    assert len(rows) == ROW_CAP
    assert count == 500
    assert hit is True
    assert pushes_aggregation(AGG_SQL) is True
    assert pushes_aggregation("SELECT amount FROM fact_revenue") is False


def _seed(path: Path) -> None:
    con = connect_file(path, write=True)
    try:
        con.execute(
            "CREATE TABLE dim_account ("
            "acct_id INTEGER PRIMARY KEY, region VARCHAR, account_name VARCHAR)"
        )
        con.execute(
            "CREATE TABLE fact_revenue ("
            "rev_id INTEGER PRIMARY KEY, revenue_month DATE, amount INTEGER)"
        )
        con.execute(
            "CREATE TABLE junc_rev_acct ("
            "left_key INTEGER, right_key INTEGER, "
            "FOREIGN KEY (left_key) REFERENCES fact_revenue(rev_id), "
            "FOREIGN KEY (right_key) REFERENCES dim_account(acct_id))"
        )
        con.execute(
            "INSERT INTO dim_account VALUES (1, 'north', 'acme'), (2, 'south', 'beta')"
        )
        con.execute(
            "INSERT INTO fact_revenue VALUES "
            "(1, DATE '2024-01-01', 10), (2, DATE '2024-02-01', 20), "
            "(3, DATE '2024-03-01', 30), (4, DATE '2024-04-01', 424242), "
            "(5, DATE '2024-01-01', 5), (6, DATE '2024-02-01', 15)"
        )
        con.execute(
            "INSERT INTO junc_rev_acct VALUES (1, 1), (2, 1), (3, 1), (4, 1), (5, 2), (6, 2)"
        )
        for index in range(8):
            con.execute(f"CREATE TABLE revenue_note_{index:02d} (memo VARCHAR)")
    finally:
        con.close()


class _Led:
    entry_id = "led_big"
    hash = "hash_big_not_the_entry"


def _run(
    db: Path,
    question: str,
    scripts: list[str],
) -> tuple[dict[str, Any], list[str], list[str]]:
    prompts: list[str] = []
    submitted: list[str] = []

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        prompts.append(str(catalog.get("sql_vote_prompt") or ""))
        sql = scripts[min(len(prompts) - 1, len(scripts) - 1)]
        return {
            "query_sql": sql,
            "served_model": "ov-routed",
            "served_provider": "openvault",
        }

    def submit(sql: str) -> QueryResult:
        from dms_executor.sql_loop import run_readonly

        submitted.append(sql)
        rows, err = run_readonly(sql, db)
        if err:
            return QueryResult(ok=False, status="error", run_id="run_big", error=err)
        return QueryResult(ok=True, status="ok", run_id="run_big", output={"rows": rows or []})

    grants = {FACT, DIM, BRIDGE, *[f"revenue_note_{index:02d}" for index in range(8)]}
    env = run_floor(
        question,
        compute=compute,
        submit=submit,
        ledger_append=lambda _payload: _Led(),
        warehouse=db,
        grantable=grants,
        dialect="duckdb",
        space_id="big-schema-space",
        session_id="ses_big_schema",
    )
    return env, prompts, submitted


def _totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    found: dict[str, int] = {}
    for row in rows:
        region = str(row.get("region") or "")
        total = row.get("total")
        if region and total is not None:
            found[region] = int(total)
    return found


def test_aggregate_runs_in_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    monkeypatch.setenv("DMS_BIG_SCHEMA", "1")
    db = tmp_path / "big.duckdb"
    _seed(db)
    env, prompts, submitted = _run(db, JOIN_Q, [AGG_SQL])
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert submitted
    assert "LIMIT 200" in submitted[0]
    assert pushes_aggregation(submitted[0]) is True
    assert _totals(env["rows"]) == {"north": NORTH, "south": SOUTH}
    assert len(env["rows"]) == 2
    assert SENTINEL not in prompts[0]
    assert FACT in prompts[0]
    assert "SUM" in prompts[0]
    meta = env["big_schema"]
    assert meta["aggregated"] is True
    assert meta["returned"] == 2
    assert meta["row_cap"] == 200
    assert meta["tokens"] <= TOKEN_BUDGET
    assert set(NEEDED) <= set(meta["tables"])


def test_retry_on_a_bad_column_sends_the_error_and_schema_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    monkeypatch.setenv("DMS_BIG_SCHEMA", "1")
    db = tmp_path / "retry.duckdb"
    _seed(db)
    env, prompts, submitted = _run(db, JOIN_Q, ["SELECT nope FROM fact_revenue", AGG_SQL])
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert len(prompts) == 2
    assert "explain:" in prompts[1]
    assert "fact_revenue" in prompts[1]
    assert "repair_schema:" in prompts[1]
    assert SENTINEL not in prompts[0]
    assert SENTINEL not in prompts[1]
    assert "route_hint" not in prompts[1]
    assert submitted and "nope" not in submitted[0]
    assert _totals(env["rows"]) == {"north": NORTH, "south": SOUTH}
    assert env["ai_floor"]["calls"] == 2


def test_row_cap_limits_what_the_ui_receives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    monkeypatch.setenv("DMS_BIG_SCHEMA", "1")
    db = tmp_path / "cap.duckdb"
    _seed(db)
    prompts: list[str] = []

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        prompts.append(str(catalog.get("sql_vote_prompt") or ""))
        return {
            "query_sql": "SELECT amount FROM fact_revenue",
            "served_model": "ov-routed",
            "served_provider": "openvault",
        }

    def submit(sql: str) -> QueryResult:
        assert "LIMIT 200" in sql
        rows = [{"amount": index} for index in range(500)]
        return QueryResult(ok=True, status="ok", run_id="run_cap", output={"rows": rows})

    env = run_floor(
        JOIN_Q,
        compute=compute,
        submit=submit,
        ledger_append=lambda _payload: _Led(),
        warehouse=db,
        grantable={FACT, DIM, BRIDGE},
        dialect="duckdb",
        space_id="big-schema-space",
        session_id="ses_big_schema",
    )
    assert_envelope_valid(env)
    assert len(env["rows"]) == 200
    assert env["big_schema"]["row_count"] == 500
    assert env["big_schema"]["returned"] == 200
    assert SENTINEL not in prompts[0]


def test_flag_off_does_not_cap_or_instruct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    monkeypatch.delenv("DMS_BIG_SCHEMA", raising=False)
    db = tmp_path / "off.duckdb"
    _seed(db)
    prompts: list[str] = []

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        prompts.append(str(catalog.get("sql_vote_prompt") or ""))
        return {
            "query_sql": "SELECT amount FROM fact_revenue",
            "served_model": "ov-routed",
            "served_provider": "openvault",
        }

    def submit(sql: str) -> QueryResult:
        assert "LIMIT 200" not in sql.upper()
        rows = [{"amount": index} for index in range(500)]
        return QueryResult(ok=True, status="ok", run_id="run_off", output={"rows": rows})

    env = run_floor(
        "How many SKUs do we have in inventory?",
        compute=compute,
        submit=submit,
        ledger_append=lambda _payload: _Led(),
        warehouse=db,
        grantable={FACT},
        dialect="duckdb",
        space_id="big-schema-space",
        session_id="ses_big_schema",
    )
    assert_envelope_valid(env)
    assert len(env["rows"]) == 500
    assert "big_schema" not in env
    assert "Push filters, joins, and aggregates" not in prompts[0]


def test_steps_stream_the_picked_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_core.ask_stream import bind_ask, clear_asks, finish_ask, public_event, wait_events

    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    monkeypatch.setenv("DMS_BIG_SCHEMA", "1")
    monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    db = tmp_path / "stream.duckdb"
    _seed(db)
    clear_asks()
    token = bind_ask("bigschema1")
    assert token is not None
    try:
        env, _prompts, _submitted = _run(db, JOIN_Q, [AGG_SQL])
    finally:
        finish_ask(token)
    batch, done = wait_events("bigschema1", 0, 0.01)
    clear_asks()
    assert done is True
    assert env["abstained"] is False
    details = [str(event.get("detail")) for event in batch if event.get("detail")]
    assert any(item.startswith("finding tables (picked:") and FACT in item for item in details)
    assert "writing SQL" in details
    assert "running on database" in details
    assert "checking result" in details
    leaked = public_event(
        {"stage": "write", "t_ms": 1, "status": "started", "detail": "DROP TABLE t"}
    )
    assert "detail" not in leaked
    kept = public_event(
        {"stage": "run", "t_ms": 1, "status": "started", "detail": "running on database"}
    )
    assert kept["detail"] == "running on database"
