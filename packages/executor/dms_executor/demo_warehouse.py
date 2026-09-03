"""Local demo serving warehouse — duckdb.execute lives only in this package.

Synthetic tables (no PII) for personal stress-test. Path via DMS_WAREHOUSE_DB.
Demo uses txn_type='outbound' (Cortex warehouse uses 'OUT' — do not point demo at
Cortex's duckdb file). Uploaded bronze is copied to the engine file by
``warehouse_identity.sync_bronze_to_serving`` instead.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import duckdb

_LOCK = threading.Lock()
_SEEDED: set[str] = set()

DEFAULT_REL = Path("data") / "dms_demo.duckdb"
SCHEMA_VERSION = 2

# Tables allowlisted on demo/live manifests
DEMO_TABLES = (
    "transactions",
    "locations",
    "inventory",
    "suppliers",
    "shipments",
    "alerts",
)

_REVENUE_SQL = """
SELECT COALESCE(SUM(quantity_kg * unit_cost_myr), 0)::DOUBLE AS revenue_myr
FROM transactions
WHERE txn_type = 'outbound'
"""


def warehouse_path() -> Path:
    raw = os.environ.get("DMS_WAREHOUSE_DB")
    if raw:
        return Path(raw)
    here = Path(__file__).resolve()
    repo = here.parents[3]  # packages/executor/dms_executor → repo
    return repo / DEFAULT_REL


def _schema_ok(db: Path) -> bool:
    try:
        con = duckdb.connect(str(db))
        try:
            row = con.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if not row or int(row[0]) != SCHEMA_VERSION:
                return False
            for table in DEMO_TABLES:
                n = con.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_name = ?",
                    [table],
                ).fetchone()
                if not n or int(n[0]) < 1:
                    return False
            return True
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return False


def ensure_demo_warehouse(path: Path | None = None) -> Path:
    """Create and seed the demo DuckDB if missing / stale schema. Idempotent."""
    db = path or warehouse_path()
    key = str(db.resolve())
    with _LOCK:
        if key in _SEEDED and db.is_file() and _schema_ok(db):
            return db
        db.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(db))
        try:
            _seed(con)
        finally:
            con.close()
        _SEEDED.add(key)
        return db


def _seed(con: duckdb.DuckDBPyConnection) -> None:
    for table in (*DEMO_TABLES, "meta"):
        con.execute(f"DROP TABLE IF EXISTS {table}")

    con.execute("CREATE TABLE meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    con.execute(
        "INSERT INTO meta VALUES ('schema_version', ?)",
        [str(SCHEMA_VERSION)],
    )

    con.execute(
        """
        CREATE TABLE locations (
          location_id VARCHAR PRIMARY KEY,
          name VARCHAR,
          capacity_kg DOUBLE,
          current_load_kg DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO locations VALUES
          ('WH-A', 'Warehouse A', 100000, 72000),
          ('WH-B', 'Warehouse B', 80000, 45000),
          ('WH-C', 'Warehouse C', 60000, 58000),
          ('WH-D', 'Warehouse D', 120000, 31000),
          ('WH-E', 'Warehouse E', 90000, 88000)
        """
    )

    con.execute(
        """
        CREATE TABLE suppliers (
          supplier_id VARCHAR PRIMARY KEY,
          supplier_name VARCHAR,
          lead_time_days INTEGER,
          risk_score DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO suppliers VALUES
          ('SUP-01', 'Northshore Materials', 7, 0.22),
          ('SUP-02', 'Peninsula Polymers', 12, 0.41),
          ('SUP-03', 'Delta Logistics Co', 5, 0.18),
          ('SUP-04', 'Orbit Packing', 9, 0.55)
        """
    )

    con.execute(
        """
        CREATE TABLE inventory (
          sku VARCHAR,
          location_id VARCHAR,
          quantity_kg DOUBLE,
          reorder_level_kg DOUBLE,
          unit_cost_myr DOUBLE,
          supplier_id VARCHAR
        )
        """
    )
    con.execute(
        """
        INSERT INTO inventory VALUES
          ('RS622XK', 'WH-A', 1200, 500, 4.50, 'SUP-01'),
          ('RS622XKR', 'WH-A', 80, 200, 5.20, 'SUP-01'),
          ('SKU-ALPHA', 'WH-B', 3400, 1000, 2.10, 'SUP-02'),
          ('SKU-BETA', 'WH-B', 900, 400, 8.75, 'SUP-03'),
          ('SKU-GAMMA', 'WH-C', 150, 300, 12.00, 'SUP-04'),
          ('SKU-DELTA', 'WH-D', 60, 250, 6.40, 'SUP-02'),
          ('SKU-EPSILON', 'WH-E', 2100, 800, 3.25, 'SUP-03')
        """
    )

    con.execute(
        """
        CREATE TABLE shipments (
          shipment_id VARCHAR PRIMARY KEY,
          sku VARCHAR,
          destination_location_id VARCHAR,
          quantity_kg DOUBLE,
          status VARCHAR,
          cost_myr DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO shipments VALUES
          ('SH-100', 'SKU-ALPHA', 'WH-B', 400, 'in_transit', 820),
          ('SH-101', 'SKU-BETA', 'WH-B', 120, 'delivered', 310),
          ('SH-102', 'RS622XK', 'WH-A', 250, 'delayed', 540),
          ('SH-103', 'SKU-GAMMA', 'WH-C', 80, 'in_transit', 190),
          ('SH-104', 'SKU-DELTA', 'WH-D', 200, 'delayed', 460)
        """
    )

    con.execute(
        """
        CREATE TABLE alerts (
          alert_id VARCHAR PRIMARY KEY,
          severity VARCHAR,
          location_id VARCHAR,
          message VARCHAR,
          resolved BOOLEAN
        )
        """
    )
    con.execute(
        """
        INSERT INTO alerts VALUES
          ('AL-1', 'high', 'WH-C', 'Load near capacity', FALSE),
          ('AL-2', 'medium', 'WH-A', 'SKU RS622XKR below reorder', FALSE),
          ('AL-3', 'low', 'WH-B', 'Inbound delayed 1 day', TRUE),
          ('AL-4', 'high', 'WH-E', 'Load near capacity', FALSE),
          ('AL-5', 'medium', 'WH-D', 'SKU-DELTA below reorder', FALSE)
        """
    )

    con.execute(
        """
        CREATE TABLE transactions (
          txn_id VARCHAR PRIMARY KEY,
          sku VARCHAR,
          location_id VARCHAR,
          txn_type VARCHAR,
          quantity_kg DOUBLE,
          unit_cost_myr DOUBLE,
          ts TIMESTAMP
        )
        """
    )
    # Deterministic outbound sales — keep original T001–T008 totals for tests,
    # then add richer rows for charts.
    rows = [
        ("T001", "RS622XK", "WH-A", "outbound", 400.0, 4.50, "2026-06-02 10:00:00"),
        ("T002", "RS622XK", "WH-A", "outbound", 350.0, 4.50, "2026-06-15 11:00:00"),
        ("T003", "RS622XKR", "WH-A", "outbound", 200.0, 5.20, "2026-06-20 09:00:00"),
        ("T004", "SKU-ALPHA", "WH-B", "outbound", 1500.0, 2.10, "2026-07-01 08:00:00"),
        ("T005", "SKU-ALPHA", "WH-B", "outbound", 800.0, 2.10, "2026-07-10 14:00:00"),
        ("T006", "SKU-BETA", "WH-B", "outbound", 600.0, 8.75, "2026-07-12 16:00:00"),
        ("T007", "SKU-GAMMA", "WH-C", "outbound", 220.0, 12.00, "2026-07-18 12:00:00"),
        ("T008", "SKU-BETA", "WH-B", "outbound", 100.0, 8.75, "2026-07-22 10:00:00"),
        ("T009", "RS622XK", "WH-A", "inbound", 500.0, 4.50, "2026-07-05 07:00:00"),
        ("T010", "SKU-ALPHA", "WH-B", "inbound", 2000.0, 2.10, "2026-07-08 07:00:00"),
        ("T011", "SKU-DELTA", "WH-D", "outbound", 180.0, 6.40, "2026-07-20 09:00:00"),
        ("T012", "SKU-EPSILON", "WH-E", "outbound", 900.0, 3.25, "2026-07-21 11:00:00"),
        ("T013", "SKU-BETA", "WH-B", "outbound", 250.0, 8.75, "2026-07-23 15:00:00"),
        ("T014", "RS622XK", "WH-A", "outbound", 120.0, 4.50, "2026-07-24 10:00:00"),
        ("T015", "SKU-ALPHA", "WH-B", "outbound", 400.0, 2.10, "2026-07-25 13:00:00"),
    ]
    con.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def connect_readonly(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    db = ensure_demo_warehouse(path)
    # Same config as writers. Mixed read_only=True vs RW on one file 500s DuckDB.
    return duckdb.connect(str(db))


def execute_sql(sql: str, *, path: Path | None = None) -> list[dict[str, Any]]:
    """Run SELECT-shaped SQL; returns list of row dicts."""
    con = connect_readonly(path)
    try:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()


def total_outbound_revenue(*, path: Path | None = None) -> float:
    rows = execute_sql(_REVENUE_SQL, path=path)
    return float(rows[0]["revenue_myr"]) if rows else 0.0
