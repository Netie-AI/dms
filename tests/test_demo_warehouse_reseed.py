"""DMS local demo file always thin-reseeds. Rich lake is Cortex, not this path."""

from __future__ import annotations

from pathlib import Path

import duckdb
from dms_executor.demo_warehouse import (
    _SEEDED,
    DEMO_TABLES,
    SCHEMA_VERSION,
    ensure_demo_warehouse,
)


def _dirty_local(path: Path) -> None:
    """A rich-looking file on the DMS local path (must not survive first ensure)."""
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
        con.executemany(
            "INSERT INTO inventory VALUES (?, ?, ?, ?)",
            [(f"SKU-{i}", 1.0, 1.0, "SUP-01") for i in range(100)],
        )
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
        con.execute(
            "CREATE TABLE _verified_queries (query_id VARCHAR PRIMARY KEY)"
        )
        con.execute("INSERT INTO _verified_queries VALUES ('vq_keep')")
    finally:
        con.close()


def _counts(path: Path) -> tuple[int, int, str | None, str | None, str | None]:
    con = duckdb.connect(str(path))
    try:
        inv = int(con.execute("SELECT COUNT(*) FROM inventory").fetchone()[0])
        sup = int(con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0])
        country = con.execute(
            "SELECT country FROM suppliers WHERE supplier_id = 'SUP-01'"
        ).fetchone()
        category = con.execute(
            "SELECT category FROM inventory WHERE sku = 'RS622XK'"
        ).fetchone()
        txn = con.execute(
            "SELECT txn_type FROM transactions WHERE txn_id = 'T001'"
        ).fetchone()
        return (
            inv,
            sup,
            None if country is None else str(country[0]),
            None if category is None else str(category[0]),
            None if txn is None else str(txn[0]),
        )
    finally:
        con.close()


def test_first_ensure_this_process_reseeds_thin(tmp_path: Path) -> None:
    path = tmp_path / "local.duckdb"
    _dirty_local(path)
    _SEEDED.clear()
    out = ensure_demo_warehouse(path)
    assert out == path

    inv, sup, country, category, txn = _counts(path)
    assert inv == 7
    assert sup == 4
    assert country == "MY"
    assert category == "RAW"
    assert txn == "outbound"

    con = duckdb.connect(str(path))
    try:
        names = {
            str(r[0]).lower()
            for r in con.execute(
                "SELECT LOWER(table_name) FROM information_schema.tables"
            ).fetchall()
        }
        assert set(DEMO_TABLES) <= names
        ver = con.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        assert ver is not None and int(ver[0]) == SCHEMA_VERSION
        keep = con.execute(
            "SELECT query_id FROM _verified_queries"
        ).fetchall()
        assert keep == [("vq_keep",)]
    finally:
        con.close()


def test_schema_ok_rich_inventory_still_reseeds_on_new_process(tmp_path: Path) -> None:
    path = tmp_path / "ok_but_fat.duckdb"
    _SEEDED.clear()
    ensure_demo_warehouse(path)
    con = duckdb.connect(str(path))
    try:
        con.executemany(
            "INSERT INTO inventory VALUES (?, 'WH-A', 1, 1, 1.0, 'SUP-01', 'FAT')",
            [(f"FAT-{i}",) for i in range(50)],
        )
        n = int(con.execute("SELECT COUNT(*) FROM inventory").fetchone()[0])
        assert n > 7
    finally:
        con.close()

    _SEEDED.clear()
    ensure_demo_warehouse(path)
    inv, _sup, country, category, txn = _counts(path)
    assert inv == 7
    assert country == "MY"
    assert category == "RAW"
    assert txn == "outbound"


def test_second_call_same_process_does_not_reseed(tmp_path: Path) -> None:
    path = tmp_path / "same_proc.duckdb"
    _SEEDED.clear()
    ensure_demo_warehouse(path)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "INSERT INTO inventory VALUES "
            "('KEEP-ME', 'WH-A', 1, 1, 1.0, 'SUP-01', 'KEEP')"
        )
    finally:
        con.close()
    ensure_demo_warehouse(path)
    con = duckdb.connect(str(path))
    try:
        n = int(
            con.execute(
                "SELECT COUNT(*) FROM inventory WHERE sku = 'KEEP-ME'"
            ).fetchone()[0]
        )
        assert n == 1
    finally:
        con.close()
