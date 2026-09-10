"""SCHEMA_VERSION 3: add country/category in place. Never DROP a founder lake."""

from __future__ import annotations

from pathlib import Path

import duckdb
from dms_executor.demo_warehouse import (
    _SEEDED,
    DEMO_TABLES,
    SCHEMA_VERSION,
    ensure_demo_warehouse,
    table_columns,
)


def _v2_lake(path: Path) -> None:
    """Six demo tables without country/category, extra table, txn_type OUT."""
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE locations (location_id VARCHAR PRIMARY KEY, name VARCHAR)")
        con.execute("INSERT INTO locations VALUES ('WH-A', 'Warehouse A')")
        con.execute(
            "CREATE TABLE suppliers ("
            "supplier_id VARCHAR PRIMARY KEY, supplier_name VARCHAR)"
        )
        con.execute("INSERT INTO suppliers VALUES ('SUP-01', 'Northshore Materials')")
        con.execute(
            "CREATE TABLE inventory ("
            "sku VARCHAR, quantity_kg DOUBLE, unit_cost_myr DOUBLE, supplier_id VARCHAR)"
        )
        con.execute("INSERT INTO inventory VALUES ('RS622XK', 1200, 4.50, 'SUP-01')")
        con.execute("CREATE TABLE shipments (shipment_id VARCHAR PRIMARY KEY)")
        con.execute("INSERT INTO shipments VALUES ('SH-100')")
        con.execute("CREATE TABLE alerts (alert_id VARCHAR PRIMARY KEY)")
        con.execute("INSERT INTO alerts VALUES ('AL-1')")
        con.execute(
            "CREATE TABLE transactions ("
            "txn_id VARCHAR PRIMARY KEY, txn_type VARCHAR, quantity_kg DOUBLE)"
        )
        con.execute("INSERT INTO transactions VALUES ('T001', 'OUT', 400.0)")
        con.execute("CREATE TABLE founder_extra (id INTEGER)")
        con.execute("INSERT INTO founder_extra VALUES (42)")
        con.execute("CREATE TABLE meta (note VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('founder')")
    finally:
        con.close()


def test_ensure_migrates_in_place_without_drop(tmp_path: Path) -> None:
    path = tmp_path / "founder.duckdb"
    _v2_lake(path)
    _SEEDED.clear()
    out = ensure_demo_warehouse(path)
    assert out == path

    con = duckdb.connect(str(path))
    try:
        names = {
            str(r[0]).lower()
            for r in con.execute(
                "SELECT LOWER(table_name) FROM information_schema.tables"
            ).fetchall()
        }
        assert set(DEMO_TABLES) <= names
        assert "founder_extra" in names
        extra = con.execute("SELECT id FROM founder_extra").fetchall()
        assert extra == [(42,)]
        txn = con.execute("SELECT txn_type FROM transactions WHERE txn_id = 'T001'").fetchall()
        assert txn == [("OUT",)]
        note = con.execute("SELECT note FROM meta").fetchall()
        assert note == [("founder",)]
    finally:
        con.close()

    assert "country" in table_columns("suppliers", path=path)
    assert "category" in table_columns("inventory", path=path)
    country = duckdb.connect(str(path)).execute(
        "SELECT country FROM suppliers WHERE supplier_id = 'SUP-01'"
    ).fetchone()
    assert country is not None and country[0] == "MY"


def test_migrate_does_not_overwrite_landed_country(tmp_path: Path) -> None:
    path = tmp_path / "rich.duckdb"
    _v2_lake(path)
    con = duckdb.connect(str(path))
    try:
        con.execute("ALTER TABLE suppliers ADD COLUMN country VARCHAR")
        con.execute("ALTER TABLE inventory ADD COLUMN category VARCHAR")
        con.execute("UPDATE suppliers SET country = 'XX' WHERE supplier_id = 'SUP-01'")
        con.execute("UPDATE inventory SET category = 'CUSTOM' WHERE sku = 'RS622XK'")
    finally:
        con.close()
    _SEEDED.clear()
    ensure_demo_warehouse(path)
    con = duckdb.connect(str(path))
    try:
        got = con.execute(
            "SELECT country FROM suppliers WHERE supplier_id = 'SUP-01'"
        ).fetchone()
        assert got == ("XX",)
        cat = con.execute("SELECT category FROM inventory WHERE sku = 'RS622XK'").fetchone()
        assert cat == ("CUSTOM",)
        extra = con.execute("SELECT id FROM founder_extra").fetchone()
        assert extra == (42,)
    finally:
        con.close()
    assert SCHEMA_VERSION == 3
