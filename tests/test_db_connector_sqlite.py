"""FDE-SQLITE-SOURCE-01: SQLite files as an extract-only source.

Real ``sqlite3`` files (stdlib), no fake connection: the assertions are on what
lands in bronze and on what the connector refuses.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

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


@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "sources"
    d.mkdir()
    monkeypatch.setenv(dbc.SQLITE_ROOT_ENV, str(d))
    return d


def _shop(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE regions (code TEXT, country TEXT, PRIMARY KEY (country, code));
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT,
            country TEXT, region TEXT,
            FOREIGN KEY (region, country) REFERENCES regions (code, country));
        CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER
            REFERENCES customers, order_date DATE, amount NUMERIC(12,2));
        INSERT INTO regions VALUES ('C', 'MY'), ('N', 'MY');
        INSERT INTO customers VALUES (1, 'Ah Kow', 'MY', 'C'), (2, 'Siti', 'MY', 'N');
        INSERT INTO orders VALUES (10, 1, '2026-01-05', 12.5), (11, 2, '2026-02-01', 100),
                                  (12, 1, '2026-02-03', 7.25);
        """
    )
    con.commit()
    con.close()


def _cfg(database: str) -> dbc.SourceConfig:
    return dbc.SourceConfig(kind="sqlite", host="", database=database, user="")


def test_off_without_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dbc.SQLITE_ROOT_ENV, raising=False)
    _shop(tmp_path / "shop.db")
    with pytest.raises(dbc.SourceConnectionError, match="off"):
        dbc.list_source_tables(_cfg(str(tmp_path / "shop.db")))


def test_refuses_paths_outside_root(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.db"
    _shop(outside)
    for probe in (str(outside), "../outside.db"):
        with pytest.raises(dbc.SourceConnectionError, match="outside"):
            dbc.list_source_tables(_cfg(probe))
    link = root / "link.db"
    os.symlink(outside, link)
    with pytest.raises(dbc.SourceConnectionError, match="outside"):
        dbc.list_source_tables(_cfg("link.db"))


def test_hash_in_path_is_refused() -> None:
    with pytest.raises(ValueError):
        _cfg("a#b.db")


def test_reads_keys_including_composite_and_implicit_parent(root: Path) -> None:
    _shop(root / "shop.db")
    keys = dbc.list_source_keys(_cfg("shop.db"))
    assert keys.primary_keys["main.regions"] == ("country", "code")
    assert keys.primary_keys["main.orders"] == ("order_id",)
    fks = keys.foreign_keys
    pairs = sorted((f.from_table, f.from_column, f.to_table, f.to_column) for f in fks)
    assert pairs == [
        ("main.customers", "country", "main.regions", "country"),
        ("main.customers", "region", "main.regions", "code"),
        # REFERENCES customers (no column list) means the parent's primary key.
        ("main.orders", "customer_id", "main.customers", "id"),
    ]


def test_ingest_lands_typed_rows_with_provenance(root: Path, wh: Path) -> None:
    _shop(root / "shop.db")
    extract = dbc.ingest_source_database(_cfg("shop.db"), path=wh, space_id="s-sqlite")
    assert sorted(p.source for p in extract.pulls) == [
        # the path as given (relative to the root), not the host's directory layout
        f"sqlite:///shop.db#main.{t}"
        for t in ("customers", "orders", "regions")
    ]
    orders = next(p for p in extract.pulls if p.source.endswith("#main.orders"))
    assert orders.column_types["amount"].startswith("DECIMAL")
    assert orders.column_types["order_date"] == "DATE"
    assert orders.untyped_columns == {}
    con = duckdb.connect(str(wh), read_only=True)
    try:
        schema, name = orders.bronze_table.split(".")
        total = con.execute(f'SELECT SUM(amount) FROM "{schema}"."{name}"').fetchone()
    finally:
        con.close()
    assert total is not None and float(total[0]) == 119.75


def test_connection_is_read_only(root: Path) -> None:
    _shop(root / "shop.db")
    with dbc.connect(_cfg("shop.db")) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM orders")
    check = sqlite3.connect(root / "shop.db")
    try:
        assert check.execute("SELECT COUNT(*) FROM orders").fetchone() == (3,)
    finally:
        check.close()
