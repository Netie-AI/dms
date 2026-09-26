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

_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: dict[str, threading.RLock] = {}
_SEEDED: set[str] = set()

# ponytail: one live RW attach per resolved path. DuckDB 1.5 unique-file-handle
# 500s a second attach of the same file (alias = stem, so browse.duckdb -> "browse").
# Ceiling: Library /tree lists serialize. Upgrade: RO pool if P-DMS-34 lifts.

DEFAULT_REL = Path("data") / "dms_demo.duckdb"
SCHEMA_VERSION = 5

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


def _lock_for(db: Path) -> threading.RLock:
    key = str(Path(db).resolve())
    with _LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[key] = lock
        return lock


class _LockedConnection:
    """DuckDB handle that releases the per-file attach lock on close()."""

    def __init__(self, con: duckdb.DuckDBPyConnection, lock: threading.RLock) -> None:
        self._con = con
        self._lock = lock
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._con.close()
        finally:
            self._lock.release()

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._con.execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> Any:
        return self._con.executemany(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._con, name)

    def __enter__(self) -> _LockedConnection:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def connect_file(path: Path) -> duckdb.DuckDBPyConnection:
    """Write-mode attach. Caller must close(); one live attach per file until then."""
    db = Path(path)
    lock = _lock_for(db)
    lock.acquire()
    try:
        con = duckdb.connect(str(db))
    except BaseException:
        lock.release()
        raise
    return _LockedConnection(con, lock)  # type: ignore[return-value]


class WarehouseBusy(RuntimeError):
    """A read could not attach the warehouse: another writer holds it, or it is
    unreadable. Read routes turn this into a named degraded response, never 5xx."""

    code = "warehouse_unavailable"


#: How long a read waits for this process's own writer before reporting busy.
READ_LOCK_TIMEOUT_S = 5.0


def connect_file_readonly(
    path: Path, *, lock_timeout_s: float | None = None
) -> duckdb.DuckDBPyConnection:
    """Read-only attach for read routes. Caller must close().

    A GET must not take the warehouse's write lock: a SQL-source ingest in another
    process holds that lock for minutes, and a Spaces read that opened RW failed
    against it. ``read_only=True`` takes DuckDB's shared lock instead.

    Waits (bounded) for this process's own per-file lock, so it does not meet an
    in-process RW handle opened through ``connect_file``. It can still meet one
    opened outside it (several executor paths call ``duckdb.connect`` directly), or
    re-enter from a thread already holding the lock. DuckDB refuses a second
    configuration of a file it already has open in-process; that case joins the
    existing instance, which takes no new file lock because this process already
    holds it. Anything else DuckDB refuses (another process's writer, an unreadable
    file) raises ``WarehouseBusy``.
    """
    db = Path(path)
    lock = _lock_for(db)
    wait = READ_LOCK_TIMEOUT_S if lock_timeout_s is None else lock_timeout_s
    if not lock.acquire(timeout=max(0.0, wait)):
        raise WarehouseBusy(f"warehouse busy in this process: {db.name}")
    try:
        try:
            con = duckdb.connect(str(db), read_only=True)
        except duckdb.ConnectionException as exc:
            if "different configuration" not in str(exc):
                raise WarehouseBusy(f"warehouse unreadable: {exc}") from exc
            try:
                con = duckdb.connect(str(db))
            except duckdb.Error as exc2:
                raise WarehouseBusy(f"warehouse unreadable: {exc2}") from exc2
        except duckdb.Error as exc:
            raise WarehouseBusy(f"warehouse unreadable: {exc}") from exc
    except BaseException:
        lock.release()
        raise
    return _LockedConnection(con, lock)  # type: ignore[return-value]


def ensure_demo_warehouse(path: Path | None = None) -> Path:
    """Thin-reseed the DMS local demo file. Idempotent within one process.

    First call this process always ``_seed``s (DROP the six demo tables, write
    the small fixture). That is by design: the rich founder lake lives on the
    Cortex path (``/var/cortex/data/dms_demo.duckdb``), not here. Do not point
    ``DMS_WAREHOUSE_DB`` at the Cortex file.
    """
    db = path or warehouse_path()
    key = str(db.resolve())
    lock = _lock_for(db)
    with lock:
        # Do not probe schema via a second duckdb.connect(): DuckDB 1.5 treats
        # a second RW attach of the same file as BinderException. The old
        # ``except Exception: return False`` then fell through to another
        # connect() (CI 34750069692 on b5f02be, test_parallel_library_lists_same_file).
        if key in _SEEDED and db.is_file():
            return db
        db.parent.mkdir(parents=True, exist_ok=True)
        con = connect_file(db)
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
          current_load_kg DOUBLE,
          location_code VARCHAR,
          is_cold_storage BOOLEAN,
          cctv_camera_id VARCHAR
        )
        """
    )
    con.execute(
        """
        INSERT INTO locations VALUES
          ('WH-A', 'Warehouse A', 100000, 72000, 'WH-A', FALSE, 'CAM-A-01'),
          ('WH-B', 'Warehouse B', 80000, 45000, 'WH-B', FALSE, 'CAM-B-01'),
          ('WH-C', 'Warehouse C', 60000, 58000, 'WH-C', TRUE, 'CAM-C-01'),
          ('WH-D', 'Warehouse D', 120000, 31000, 'WH-D', FALSE, 'CAM-D-01'),
          ('WH-E', 'Warehouse E', 90000, 88000, 'WH-E', FALSE, 'CAM-E-01')
        """
    )

    con.execute(
        """
        CREATE TABLE suppliers (
          supplier_id VARCHAR PRIMARY KEY,
          supplier_name VARCHAR,
          country VARCHAR,
          lead_time_days INTEGER,
          risk_score DOUBLE,
          last_audit_date DATE
        )
        """
    )
    con.execute(
        """
        INSERT INTO suppliers VALUES
          ('SUP-01', 'Northshore Materials', 'MY', 7, 0.22, '2025-01-01'),
          ('SUP-02', 'Peninsula Polymers', 'SG', 12, 0.41, '2026-08-01'),
          ('SUP-03', 'Delta Logistics Co', 'MY', 5, 0.18, '2024-06-01'),
          ('SUP-04', 'Orbit Packing', 'TH', 9, 0.55, '2026-09-01')
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
          supplier_id VARCHAR,
          category VARCHAR,
          expiry_date DATE
        )
        """
    )
    con.execute(
        """
        INSERT INTO inventory VALUES
          ('RS622XK', 'WH-A', 1200, 500, 4.50, 'SUP-01', 'RAW', NULL),
          ('RS622XKR', 'WH-A', 80, 200, 5.20, 'SUP-01', 'RAW', NULL),
          ('SKU-ALPHA', 'WH-B', 3400, 1000, 2.10, 'SUP-02', 'PACKAGING', NULL),
          ('SKU-BETA', 'WH-B', 900, 400, 8.75, 'SUP-03', 'PACKAGING', NULL),
          ('SKU-GAMMA', 'WH-C', 150, 300, 12.00, 'SUP-04', 'CHEMICALS', '2020-01-15'),
          ('SKU-DELTA', 'WH-D', 60, 250, 6.40, 'SUP-02', 'PARTS', NULL),
          ('SKU-EPSILON', 'WH-E', 2100, 800, 3.25, 'SUP-03', 'PARTS', NULL)
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
    return connect_file(db)


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
