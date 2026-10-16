"""A column name from a connected SQL source is one quoted identifier, never DuckDB SQL.

``write_bronze_rows`` used to interpolate each source column name into DDL as
``"{name}" VARCHAR`` with no escaping of the double quote, and DuckDB's ``execute`` runs a
multi-statement string. Whoever controls a column name in the connected source could run a
second statement on the lake (CREATE TABLE, COPY ... TO, ATTACH, read_csv of a local file).
``contract_infer`` re-quoted the landed names the same raw way. Both go through
``bronze.quote_ident`` now.

Customer-facing assertions: the landed table and rows, the lake's table list and the exfil
file, never the generated SQL.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor.bronze import quote_ident, write_bronze_rows
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install


def _body(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "sqlserver",
        "host": "db.example.net",
        "database": "sales",
        "user": "reader",
        "password": "pw",
        "tables": ["orders"],
    }
    base.update(over)
    return base


@pytest.fixture()
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "colname.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


def _allow_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.pipelines as pipeline_routes
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    def _allow(*, action: str, **_: object) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr(studio_routes, "compliance_gate", _allow)
    monkeypatch.setattr(pipeline_routes, "compliance_gate", _allow)


def _tables(lake: Path) -> set[str]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return {
            f"{s}.{t}"
            for s, t in con.execute(
                "SELECT table_schema, table_name FROM information_schema.tables"
            ).fetchall()
        }
    finally:
        con.close()


def _ingest(monkeypatch: pytest.MonkeyPatch, evil: str) -> tuple[TestClient, dict]:
    con = _FakeConnection(
        [("dbo", "orders")], (["order_id", evil], [["A-1", "10.50"], ["A-2", "7"]])
    )
    _install(monkeypatch, con)
    _allow_gates(monkeypatch)
    client = TestClient(create_app())
    r = client.post("/v1/studio/sources/sql", json=_body())
    assert r.status_code == 200, (r.status_code, r.text)
    return client, r.json()


def test_a_source_column_name_cannot_run_a_second_statement(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil = 'x" VARCHAR); CREATE TABLE main.pwned_by_colname AS SELECT 42 AS v; --'
    _client, body = _ingest(monkeypatch, evil)

    assert "main.pwned_by_colname" not in _tables(lake), "the ingest ran the column name as SQL"
    (landed,) = body["tables"]
    assert landed["row_count"] == 2
    con = duckdb.connect(str(lake), read_only=True)
    try:
        cols = [c[0] for c in con.execute(f"DESCRIBE {landed['bronze_table']}").fetchall()]
        rows = con.execute(
            f"SELECT order_id, {quote_ident(evil)} FROM {landed['bronze_table']} ORDER BY order_id"
        ).fetchall()
    finally:
        con.close()
    assert evil in cols
    assert rows == [("A-1", "10.50"), ("A-2", "7")]


def test_a_lone_double_quote_in_a_column_name_lands_as_that_name_not_a_500(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, body = _ingest(monkeypatch, 'we"ird')
    (landed,) = body["tables"]
    con = duckdb.connect(str(lake), read_only=True)
    try:
        assert 'we"ird' in [
            c[0] for c in con.execute(f"DESCRIBE {landed['bronze_table']}").fetchall()
        ]
        assert con.execute(f"SELECT COUNT(*) FROM {landed['bronze_table']}").fetchone() == (2,)
    finally:
        con.close()


def test_a_landed_hostile_name_does_not_run_when_the_contract_is_inferred(
    tmp_path: Path, lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer is fixed and the name lands; the reader that re-quotes it must not run it."""
    exfil = tmp_path / "exfil_from_infer.csv"
    evil = (
        'x") FROM "bronze"."dbo_orders"; '
        f"COPY (SELECT 42 AS v) TO '{exfil.as_posix()}' (FORMAT CSV); "
        'SELECT COUNT("x'
    )
    client, body = _ingest(monkeypatch, evil)
    (landed,) = body["tables"]
    assert not exfil.exists(), "the ingest ran the name as a statement"

    r = client.post("/v1/pipelines/infer-contract", json={"source": landed["bronze_table"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert not exfil.exists(), "infer_contract ran a landed column name as a second statement"
    assert out["row_count"] == 2
    assert evil in out["columns"]
    assert out["null_rates"][evil] == 0.0


def test_write_bronze_rows_quotes_every_column_name(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    name = 'a" VARCHAR); CREATE TABLE main.t_pwn AS SELECT 1; --'
    write_bronze_rows(table="t", columns=["id", name], rows=[["1", "x"]], path=db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        tables = {
            t for (t,) in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        assert "t_pwn" not in tables
        assert con.execute(f"SELECT {quote_ident(name)} FROM bronze.t").fetchall() == [("x",)]
    finally:
        con.close()


@pytest.mark.parametrize("bad", ["", "a\x00b"])
def test_write_bronze_rows_refuses_a_name_that_cannot_be_an_identifier_and_keeps_the_old_table(
    tmp_path: Path, bad: str
) -> None:
    db = tmp_path / "keep.duckdb"
    write_bronze_rows(table="t", columns=["id"], rows=[["1"], ["2"]], path=db)
    with pytest.raises(ValueError):
        write_bronze_rows(table="t", columns=["id", bad], rows=[["9", "z"]], path=db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM bronze.t").fetchone() == (2,)
    finally:
        con.close()


def test_quote_ident_escapes_the_quote_and_refuses_nul_and_empty() -> None:
    assert quote_ident('a"b') == '"a""b"'
    assert quote_ident("plain") == '"plain"'
    for bad in ("", "a\x00b", None, 3):
        with pytest.raises(ValueError):
            quote_ident(bad)  # type: ignore[arg-type]
