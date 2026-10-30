"""Four ingest fixes: declared types, valid names, per-Space copies, serving sync.

Each test fails if that one fix is removed. No live Cortex.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest
from dms_executor import db_connector as dbc
from dms_executor.bronze import (
    aggregate_on_untyped_numeric,
    write_bronze_rows,
)
from dms_executor.demo_grants import ingested_bronze_tables
from dms_executor.demo_warehouse import ensure_demo_warehouse

from test_db_connector import _FakeConnection, _cfg, _install


def _types(wh: Path, table: str) -> dict[str, str]:
    con = duckdb.connect(str(wh), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'bronze' AND table_name = ?
            """,
            [table.split(".")[-1]],
        ).fetchall()
    finally:
        con.close()
    return {str(name): str(kind).upper() for name, kind in rows}


def _column(wh: Path, table: str, column: str) -> list[object]:
    con = duckdb.connect(str(wh), read_only=True)
    try:
        rows = con.execute(
            f'SELECT "{column}" FROM {table} ORDER BY 1'
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def test_declared_types_land_and_inexact_stays_text(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection(
        [("dbo", "orders")],
        (
            ["qty", "amount", "ordered_on", "price"],
            [["2", "9.50", "2024-03-01", "$9.50"], ["10", "100.25", "2024-03-02", "$100.25"]],
        ),
    )
    con.column_types = {
        "dbo.orders": [
            ("qty", "bigint"),
            ("amount", "decimal"),
            ("ordered_on", "date"),
            ("price", "varchar"),
        ]
    }
    _install(monkeypatch, con)
    pull = dbc.ingest_source_table(_cfg(), "orders", path=wh)
    kinds = _types(wh, pull.bronze_table)
    assert kinds["qty"] == "BIGINT"
    assert kinds["amount"].startswith("DECIMAL")
    assert kinds["ordered_on"] == "DATE"
    assert kinds["price"] == "VARCHAR"
    assert "price" in pull.untyped_numeric
    assert "price" in pull.kept_text
    assert aggregate_on_untyped_numeric(
        f"SELECT MAX(price) FROM {pull.bronze_table}", path=wh
    )


def test_zoned_timestamps_stay_text(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Casting these to a date counts 1 order. The strings are two different days."""
    con = _FakeConnection(
        [("dbo", "orders")],
        (
            ["ordered_at"],
            [["2024-01-01 16:30:00+00:00"], ["2024-01-02 00:30:00+08:00"]],
        ),
    )
    con.column_types = {"dbo.orders": [("ordered_at", "timestamptz")]}
    _install(monkeypatch, con)
    pull = dbc.ingest_source_table(_cfg(), "orders", path=wh)
    assert _types(wh, pull.bronze_table)["ordered_at"] == "VARCHAR"
    assert "ordered_at" in pull.kept_text
    assert "ordered_at" not in pull.untyped_numeric
    assert _column(wh, pull.bronze_table, "ordered_at") == [
        "2024-01-01 16:30:00+00:00",
        "2024-01-02 00:30:00+08:00",
    ]


def test_digit_first_source_gets_a_valid_name(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection([("2024", "sales")], (["id"], [["1"]]))
    _install(monkeypatch, con)
    pull = dbc.ingest_source_table(_cfg(), "sales", schema="2024", path=wh)
    assert pull.bronze_table == "bronze.t_2024_sales"


def test_legacy_digit_name_is_dropped_from_grants(wh: Path, caplog: pytest.LogCaptureFixture) -> None:
    write_bronze_rows(table="2024_sales", columns=["id"], rows=[["1"]], path=wh)
    write_bronze_rows(table="orders", columns=["id"], rows=[["1"]], path=wh)
    with caplog.at_level("WARNING"):
        granted = ingested_bronze_tables(wh)
    assert "bronze.orders" in granted
    assert "bronze.2024_sales" not in granted
    assert any("2024_sales" in rec.message for rec in caplog.records)


def test_second_space_does_not_overwrite_the_first(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection([("dbo", "orders")], (["id"], [["1"], ["2"]]))
    _install(monkeypatch, con)
    first = dbc.ingest_source_table(_cfg(), "orders", path=wh, space_id="space-a")
    second = dbc.ingest_source_table(_cfg(), "orders", path=wh, space_id="space-b")
    assert first.bronze_table != second.bronze_table
    assert len(_column(wh, first.bronze_table, "id")) == 2
    assert len(_column(wh, second.bronze_table, "id")) == 2


def test_case_colliding_columns_land(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    con = _FakeConnection([("dbo", "orders")], (["Name", "name"], [["Ada", "ada"]]))
    _install(monkeypatch, con)
    pull = dbc.ingest_source_table(_cfg(), "orders", path=wh)
    kinds = _types(wh, pull.bronze_table)
    assert "Name" in kinds
    assert "name_2" in kinds


def test_names_are_claimed_before_rows_for_same_stem(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dbo.a-b and dbo.a_b still both survive. Claim-first must not collapse them."""
    con = _FakeConnection(
        [("dbo", "a-b"), ("dbo", "a_b")],
        {
            "[dbo].[a-b]": (["k"], [["1"], ["2"]]),
            "[dbo].[a_b]": (["k"], [["9"]]),
        },
    )
    _install(monkeypatch, con)
    extract = dbc.ingest_source_database(_cfg(), path=wh)
    names = [p.bronze_table for p in extract.pulls]
    assert len(set(names)) == 2
    assert extract.pulls[1].note


def test_sql_receipt_reports_serving_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_api.wiring import _serving_sync_fields
    from dms_executor.warehouse_identity import SyncResult

    monkeypatch.setattr(
        "dms_executor.maybe_sync_bronze_to_serving",
        lambda ingest=None: SyncResult(
            status="copied",
            ingest=Path("ingest.duckdb"),
            serving=Path("serving.duckdb"),
            copied=["bronze.t_2024_sales"],
        ),
    )
    fields = _serving_sync_fields()
    assert fields["serving_sync"] == "ok"
    assert fields["chat_can_see_it"] is True


def test_untyped_numeric_ask_abstains(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(wh))
    con = _FakeConnection(
        [("dbo", "orders")],
        (["price"], [["$9.50"], ["$100.25"]]),
    )
    con.column_types = {"dbo.orders": [("price", "varchar")]}
    _install(monkeypatch, con)
    dbc.ingest_source_table(_cfg(), "orders", path=wh)
    from cortex_client.models import AskResponse
    from dms_executor import map_ask_response_to_envelope

    resp = AskResponse.model_validate(
        {
            "answer": "The max is $9.50.",
            "audit_id": "aud_money",
            "route": "sql",
            "badge": "l2_validated",
            "sql_used": "SELECT MAX(price) AS price FROM bronze.dbo_orders",
            "rows": [{"price": "$9.50"}],
            "values": [{"id": "v0", "value": 9.5, "label": "price"}],
        }
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_money")
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "untyped_numeric" in env["text"]
    assert env["rows"] == []
    assert env["values"] == []


def test_postgres_declared_types_land(wh: Path) -> None:
    """Read a real Postgres catalog and land those types. Skips when Postgres is down."""
    url_user, url_pass, url_db = "dms", "dms", "dms"
    try:
        import psycopg
    except ImportError:
        pytest.skip("psycopg is not installed")
    try:
        pg = psycopg.connect(
            host="127.0.0.1",
            port=5432,
            user=url_user,
            password=url_pass,
            dbname=url_db,
            connect_timeout=2,
        )
    except Exception as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")
    schema = "ingest_fix_probe"
    try:
        pg.autocommit = True
        with pg.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(
                f"""
                CREATE TABLE {schema}.orders (
                    qty bigint,
                    amount numeric,
                    ordered_on date,
                    ordered_at timestamptz
                )
                """
            )
            cur.execute(
                f"""
                INSERT INTO {schema}.orders VALUES
                (2, 9.50, DATE '2024-03-01', '2024-01-02 00:30:00+08'),
                (10, 100.25, DATE '2024-03-02', '2024-01-01 16:30:00+00')
                """
            )
        cfg = dbc.SourceConfig(
            kind="postgres",
            host="127.0.0.1",
            database=url_db,
            user=url_user,
            password=url_pass,
        )
        pull = dbc.ingest_source_table(cfg, "orders", schema=schema, path=wh)
        kinds = _types(wh, pull.bronze_table)
        assert kinds["qty"] == "BIGINT", kinds
        assert kinds["amount"].startswith("DECIMAL"), kinds
        assert kinds["ordered_on"] == "DATE", kinds
        assert kinds["ordered_at"] == "VARCHAR", kinds
        assert "ordered_at" in pull.kept_text
    finally:
        try:
            with pg.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            pg.close()


@pytest.fixture()
def wh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "lake.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    return db


def test_sync_not_attempted_under_pytest_is_named() -> None:
    os.environ.pop("CORTEX_WAREHOUSE_DB", None)
    from dms_api.wiring import _serving_sync_fields

    fields = _serving_sync_fields()
    assert fields["serving_sync"] == "not_attempted"
    assert fields["chat_can_see_it"] is False
