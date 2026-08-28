"""Live source-DB connector — identifier safety, credential hygiene, landed rows.

No live server: a fake DB-API connection stands in for pyodbc/pymysql so the
suite runs in CI. The assertions that matter are on what actually lands in
bronze, not on the SQL that was generated.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor import db_connector as dbc
from dms_executor.demo_warehouse import ensure_demo_warehouse


@pytest.fixture()
def wh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "lake.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    return db


class _FakeCursor:
    def __init__(self, owner: _FakeConnection) -> None:
        self._owner = owner
        self._result: list[tuple[Any, ...]] = []
        self.description: list[tuple[Any, ...]] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        self._owner.executed.append(sql)
        if "INFORMATION_SCHEMA.TABLES" in sql:
            self.description = [("TABLE_SCHEMA",), ("TABLE_NAME",)]
            self._result = list(self._owner.catalog)
            return
        cols, rows = self._owner.table_data
        self.description = [(c,) for c in cols]
        self._result = [tuple(r) for r in rows]

    def fetchall(self) -> list[tuple[Any, ...]]:
        out, self._result = self._result, []
        return out

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        out, self._result = self._result[:size], self._result[size:]
        return out

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(
        self,
        catalog: list[tuple[str, str]],
        table_data: tuple[list[str], list[list[Any]]],
    ) -> None:
        self.catalog = catalog
        self.table_data = table_data
        self.executed: list[str] = []
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def _install(
    monkeypatch: pytest.MonkeyPatch,
    con: _FakeConnection,
) -> None:
    @contextmanager
    def _fake_connect(cfg: dbc.SourceConfig) -> Any:
        yield con

    monkeypatch.setattr(dbc, "connect", _fake_connect)


def _cfg(kind: str = "sqlserver") -> dbc.SourceConfig:
    return dbc.SourceConfig(
        kind=kind,  # type: ignore[arg-type]
        host="db.example.net",
        database="sales",
        user="reader",
        password="p;w}d",
    )


# --- credential hygiene ------------------------------------------------------


def test_password_never_appears_in_repr() -> None:
    cfg = _cfg()
    assert "p;w}d" not in repr(cfg)
    assert "reader" in repr(cfg)


def test_describe_is_credential_free() -> None:
    assert _cfg().describe() == "sqlserver://db.example.net:1433/sales"
    assert "p;w}d" not in _cfg().describe()


def test_odbc_string_braces_values_containing_delimiters() -> None:
    conn_str = dbc._sqlserver_connection_string(_cfg())
    # A bare PWD=p;w}d would terminate the value at the semicolon.
    assert "PWD={p;w}}d}" in conn_str
    assert "Encrypt=yes" in conn_str
    assert "SERVER={db.example.net,1433}" in conn_str


def test_azure_requires_encryption_by_default() -> None:
    assert _cfg().encrypt is True
    assert _cfg().trust_server_certificate is False


# --- identifier safety -------------------------------------------------------


def test_table_not_in_catalog_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    con = _FakeConnection([("dbo", "orders")], (["id"], [["1"]]))
    _install(monkeypatch, con)
    with pytest.raises(dbc.UnknownSourceTable):
        dbc.preview_source_table(_cfg(), "orders; DROP TABLE users--")


def test_ambiguous_table_requires_a_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    con = _FakeConnection([("dbo", "orders"), ("staging", "orders")], (["id"], [["1"]]))
    _install(monkeypatch, con)
    with pytest.raises(dbc.UnknownSourceTable, match="ambiguous"):
        dbc.preview_source_table(_cfg(), "orders")


def test_identifier_is_quoted_per_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    con = _FakeConnection([("dbo", "orders")], (["id"], [["1"]]))
    _install(monkeypatch, con)
    dbc.preview_source_table(_cfg("sqlserver"), "orders")
    assert "SELECT * FROM [dbo].[orders]" in con.executed[-1]

    con2 = _FakeConnection([("sales", "orders")], (["id"], [["1"]]))
    _install(monkeypatch, con2)
    dbc.preview_source_table(_cfg("mysql"), "orders")
    assert "SELECT * FROM `sales`.`orders`" in con2.executed[-1]


def test_system_schemas_are_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    con = _FakeConnection([("dbo", "orders"), ("sys", "objects")], (["id"], [["1"]]))
    _install(monkeypatch, con)
    assert [t.qualified for t in dbc.list_source_tables(_cfg())] == ["dbo.orders"]


# --- what actually lands -----------------------------------------------------


def _bronze_rows(wh: Path, table: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(wh), read_only=True)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        con.close()


def test_ingest_lands_source_rows_in_bronze(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection(
        [("dbo", "orders")],
        (["order_id", "amount"], [["A-1", "10.50"], ["A-2", "20.25"]]),
    )
    _install(monkeypatch, con)

    pull = dbc.ingest_source_table(_cfg(), "orders", path=wh)

    assert pull.bronze_table == "bronze.dbo_orders"
    assert pull.columns == ["order_id", "amount"]
    assert pull.row_count == 2
    assert pull.truncated is False
    assert pull.source == "sqlserver://db.example.net:1433/sales#dbo.orders"

    landed = _bronze_rows(wh, pull.bronze_table)
    assert [(r[0], r[1]) for r in landed] == [("A-1", "10.50"), ("A-2", "20.25")]
    # Appendix A provenance: every row carries _src and _ingest_id.
    assert all(r[-1] == pull.ingest_id for r in landed)
    assert all(r[-2][0]["ref_id"] == pull.ref_id for r in landed)


def test_null_values_survive_as_null(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    con = _FakeConnection([("dbo", "orders")], (["order_id", "amount"], [["A-1", None]]))
    _install(monkeypatch, con)
    pull = dbc.ingest_source_table(_cfg(), "orders", path=wh)
    landed = _bronze_rows(wh, pull.bronze_table)
    # A NULL must not land as the string "None".
    assert landed[0][1] is None


def test_pull_over_max_rows_is_capped_and_flagged(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [[f"A-{i}", str(i)] for i in range(5)]
    con = _FakeConnection([("dbo", "orders")], (["order_id", "amount"], rows))
    _install(monkeypatch, con)

    pull = dbc.ingest_source_table(_cfg(), "orders", path=wh, max_rows=2)

    assert pull.truncated is True
    assert pull.row_count == 2
    assert len(_bronze_rows(wh, pull.bronze_table)) == 2


def test_exact_fit_is_not_reported_as_truncated(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [[f"A-{i}", str(i)] for i in range(3)]
    con = _FakeConnection([("dbo", "orders")], (["order_id", "amount"], rows))
    _install(monkeypatch, con)
    pull = dbc.ingest_source_table(_cfg(), "orders", path=wh, max_rows=3)
    assert pull.truncated is False
    assert pull.row_count == 3


def test_max_rows_must_be_positive(wh: Path) -> None:
    with pytest.raises(ValueError):
        dbc.ingest_source_table(_cfg(), "orders", path=wh, max_rows=0)


def test_unsupported_kind_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported source kind"):
        dbc.SourceConfig(kind="oracle", host="h", database="d", user="u")  # type: ignore[arg-type]
