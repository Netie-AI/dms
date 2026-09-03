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
        if "REFERENTIAL_CONSTRAINTS" in sql or "REFERENCED_TABLE_NAME" in sql:
            self.description = [
                ("CONSTRAINT_NAME",),
                ("FROM_SCHEMA",),
                ("FROM_TABLE",),
                ("FROM_COLUMN",),
                ("TO_SCHEMA",),
                ("TO_TABLE",),
                ("TO_COLUMN",),
                ("ORDINAL_POSITION",),
            ]
            self._result = list(self._owner.fks)
            return
        if "PRIMARY KEY" in sql or "CONSTRAINT_NAME = 'PRIMARY'" in sql:
            self.description = [
                ("TABLE_SCHEMA",),
                ("TABLE_NAME",),
                ("COLUMN_NAME",),
                ("ORDINAL_POSITION",),
            ]
            self._result = list(self._owner.pks)
            return
        data = self._owner.table_data
        if isinstance(data, dict):
            cols, rows = next(payload for ident, payload in data.items() if ident in sql)
        else:
            cols, rows = data
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
        table_data: tuple[list[str], list[list[Any]]]
        | dict[str, tuple[list[str], list[list[Any]]]],
        pks: list[tuple[Any, ...]] | None = None,
        fks: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.catalog = catalog
        self.table_data = table_data
        self.pks = pks or []
        self.fks = fks or []
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


# --- DR-0005: source provenance + the collect/understand join ---------------


def _fanout_source() -> _FakeConnection:
    """The shape STATUS already measured: a declared FK on a non-unique parent side.

    Customers are identified by (cust_ref, version). The source still declares
    orders.cust_ref -> customers.cust_ref. Joining that inflates O1+O2 from 30 to 45.
    """
    return _FakeConnection(
        [("dbo", "orders"), ("dbo", "customers")],
        {
            "[dbo].[orders]": (
                ["order_id", "cust_ref", "amount"],
                [["O1", "C1", "10"], ["O2", "C1", "20"], ["O3", "C2", "30"]],
            ),
            "[dbo].[customers]": (
                ["cust_ref", "version", "region"],
                [["C1", "1", "North"], ["C1", "2", "South"], ["C2", "1", "South"]],
            ),
        },
        pks=[
            ("dbo", "orders", "order_id", 1),
            ("dbo", "customers", "cust_ref", 1),
            ("dbo", "customers", "version", 2),
        ],
        fks=[
            ("FK_Orders_Customers", "dbo", "orders", "cust_ref", "dbo", "customers", "cust_ref", 1),
        ],
    )


def test_sql_source_is_named_on_library_preview(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0001: the customer artifact is the preview, not the registry insert."""
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    con = _FakeConnection([("dbo", "orders")], (["order_id", "amount"], [["A-1", "10.50"]]))
    _install(monkeypatch, con)
    pull = dbc.ingest_source_table(_cfg(), "orders", path=wh)

    client = TestClient(create_app())
    r = client.get(f"/v1/library/bronze/{pull.bronze_table}/preview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "sqlserver://db.example.net:1433/sales#dbo.orders"
    assert body["source"] == pull.source
    assert body["extracted_at"]
    assert "p;w}d" not in r.text
    assert body["row_count"] == 1


def test_declared_fk_that_the_data_violates_is_refused(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collect and understand: the source says the join is safe; the rows say it is not."""
    import sys
    from pathlib import Path as _Path

    import duckdb

    sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))
    from ontology import Refusal, from_manifest

    _install(monkeypatch, _fanout_source())
    extracted = dbc.ingest_source_database(_cfg(), path=wh)
    assert extracted.skipped == []
    assert {p.bronze_table for p in extracted.pulls} == {
        "bronze.dbo_orders",
        "bronze.dbo_customers",
    }
    assert extracted.keys.primary_keys["dbo.customers"] == ("cust_ref", "version")

    bronze = {f"{t['schema']}.{t['table']}": t["path"] for t in extracted.manifest_entry["tables"]}

    def relation_for(schema: str, table: str) -> str:
        return bronze[f"{schema}.{table}"]

    onto = from_manifest(extracted.manifest_entry, relation_for=relation_for)
    onto.add_measure("revenue", "dbo.orders", "SUM(TRY_CAST(f.amount AS DOUBLE))")
    con = duckdb.connect(str(wh))
    try:
        onto.verify(con)
        got = onto.compile("revenue", group_by=[("dbo.customers", "region")])
        assert isinstance(got, Refusal)
        assert got.reason == "fanout_refused"
        truth = con.execute(
            "SELECT SUM(TRY_CAST(amount AS DOUBLE)) FROM bronze.dbo_orders"
        ).fetchone()
        inflated = con.execute(
            "SELECT SUM(TRY_CAST(o.amount AS DOUBLE)) FROM bronze.dbo_orders o "
            "JOIN bronze.dbo_customers c ON o.cust_ref = c.cust_ref"
        ).fetchone()
        assert truth[0] == 60.0
        assert inflated[0] == 90.0
    finally:
        con.close()


def test_requested_table_the_login_cannot_see_is_reported(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _FakeConnection([("dbo", "orders")], (["order_id"], [["1"]]))
    _install(monkeypatch, con)
    extracted = dbc.ingest_source_database(_cfg(), tables=["orders", "secret"], path=wh)
    assert extracted.skipped == ["secret"]
    assert [p.bronze_table for p in extracted.pulls] == ["bronze.dbo_orders"]


def _clean_source() -> _FakeConnection:
    """The R-0005 sibling of _fanout_source: same tables, but customers ARE unique on
    cust_ref, so the declared FK is genuinely safe to group through."""
    return _FakeConnection(
        [("dbo", "orders"), ("dbo", "customers")],
        {
            "[dbo].[orders]": (
                ["order_id", "cust_ref", "amount"],
                [["O1", "C1", "10"], ["O2", "C1", "20"], ["O3", "C2", "30"]],
            ),
            "[dbo].[customers]": (
                ["cust_ref", "region"],
                [["C1", "North"], ["C2", "South"]],
            ),
        },
        pks=[
            ("dbo", "orders", "order_id", 1),
            ("dbo", "customers", "cust_ref", 1),
        ],
        fks=[
            ("FK_Orders_Customers", "dbo", "orders", "cust_ref", "dbo", "customers", "cust_ref", 1),
        ],
    )


def test_a_declared_fk_the_data_supports_is_compiled_not_refused(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0005: a control that refuses legitimate work is a failure, not a win.

    Without this, a compile() that refused every link would pass the fan-out test
    above and read as a working guard. The guard must pass the join the data supports,
    and the number it compiles must equal the ungrouped truth.
    """
    import sys
    from pathlib import Path as _Path

    import duckdb

    sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))
    from ontology import Refusal, from_manifest

    _install(monkeypatch, _clean_source())
    extracted = dbc.ingest_source_database(_cfg(), path=wh)
    bronze = {f"{t['schema']}.{t['table']}": t["path"] for t in extracted.manifest_entry["tables"]}
    onto = from_manifest(extracted.manifest_entry, relation_for=lambda s, t: bronze[f"{s}.{t}"])
    onto.add_measure("revenue", "dbo.orders", "SUM(TRY_CAST(f.amount AS DOUBLE))")
    con = duckdb.connect(str(wh))
    try:
        violations = onto.verify(con)
        assert violations == [], [f"{v.check} {v.subject}: {v.detail}" for v in violations]
        got = onto.compile("revenue", group_by=[("dbo.customers", "region")])
        assert not isinstance(got, Refusal), f"a safe join was refused: {got}"
        grouped = con.execute(got.sql).fetchall()
        assert sum(float(r[-1]) for r in grouped) == 60.0, grouped
    finally:
        con.close()
