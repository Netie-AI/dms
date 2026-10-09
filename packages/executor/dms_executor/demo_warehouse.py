"""Local demo serving warehouse — duckdb.execute lives only in this package.

Synthetic tables (no PII) for personal stress-test. Path via DMS_WAREHOUSE_DB.
Demo uses txn_type='outbound' (Cortex warehouse uses 'OUT' — do not point demo at
Cortex's duckdb file). Uploaded bronze is copied to the engine file by
``warehouse_identity.sync_bronze_to_serving`` instead.
"""

from __future__ import annotations

import contextvars
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

# Product SQL may not bind this. Oracle and scorer calls may.
RESERVED_PARAM_AS_OF = "reserved_param:as_of"

_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: dict[str, threading.RLock] = {}
_SEEDED: set[str] = set()

# Last successful execute_sql on this process. Pending until an envelope stamps it.
# ponytail: process-global, one in-flight ask. Upgrade: pass the clock on the call.
_ENGINE_CLOCK: dict[str, str] | None = None
_CLOCK_PENDING = False


class ReservedParamError(Exception):
    """Product SQL named $as_of. The statement was not executed."""


# Monotonic instant the in-flight ask must finish. Unset off the ask path.
_SERVING_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "dms_serving_deadline", default=None
)


class ServingLockWait(TimeoutError):
    """The serving file lock was not free before the ask deadline."""

    def __init__(self) -> None:
        super().__init__("serving lock")


def serving_lock_wait_s() -> float:
    """Seconds a serving-path lock acquire may block.

    An armed ask gets only the time it has left. Otherwise the full serving
    deadline (``insights_timeout_s``). Call sites do not pick their own number.
    """
    from cortex_client.compute import insights_timeout_s

    budget = insights_timeout_s()
    deadline = _SERVING_DEADLINE.get()
    if deadline is None:
        return budget
    return min(budget, max(0.0, deadline - time.monotonic()))


def arm_serving_deadline() -> contextvars.Token[float | None]:
    """Start this ask's deadline. Reset the token on the same context."""
    from cortex_client.compute import insights_timeout_s

    return _SERVING_DEADLINE.set(time.monotonic() + insights_timeout_s())


def reset_serving_deadline(token: contextvars.Token[float | None]) -> None:
    _SERVING_DEADLINE.reset(token)


@contextmanager
def serving_deadline() -> Iterator[None]:
    """Bound every serving lock wait in this context by the ask deadline."""
    token = arm_serving_deadline()
    try:
        yield
    finally:
        reset_serving_deadline(token)


def _file_lock_wait(explicit: float | None) -> float | None:
    """None waits without a bound. An armed ask caps every wait, including an explicit one."""
    if explicit is None and _SERVING_DEADLINE.get() is None:
        return None
    if explicit is None:
        return serving_lock_wait_s()
    deadline = _SERVING_DEADLINE.get()
    if deadline is None:
        return explicit
    return min(explicit, max(0.0, deadline - time.monotonic()))


def _acquire_file_lock(lock: threading.RLock, timeout: float | None) -> None:
    """Take ``lock``. A missed serving deadline raises and does not leave it held."""
    wait = _file_lock_wait(timeout)
    if wait is None:
        lock.acquire()
        return
    if not lock.acquire(timeout=wait):
        raise ServingLockWait()


def sql_has_reserved_as_of(sql: str) -> bool:
    """True when sqlglot sees a real $as_of placeholder.

    A `$` inside a string, an identifier, or a comment is not a placeholder.
    Tokenizer only. No regex and no substring scan of the SQL text.
    """
    from sqlglot import tokenize
    from sqlglot.tokens import TokenType

    try:
        tokens = tokenize(sql or "", read="duckdb")
    except Exception:  # noqa: BLE001 - unreadable SQL is not a placeholder
        return False
    saw_param = False
    for tok in tokens:
        if saw_param and tok.token_type == TokenType.VAR and tok.text.lower() == "as_of":
            return True
        saw_param = tok.token_type == TokenType.PARAMETER and tok.text == "$"
    return False


def clear_engine_clock() -> None:
    global _ENGINE_CLOCK, _CLOCK_PENDING
    _ENGINE_CLOCK = None
    _CLOCK_PENDING = False


# Survives clear_engine_clock. stamp copies the clock onto the envelope and
# then drops the process clock, so the scorer reads this log instead.
_CONNECTION_LOG: list[dict[str, str] | None] = []


def clear_connection_log() -> None:
    _CONNECTION_LOG.clear()


def connection_log_len() -> int:
    return len(_CONNECTION_LOG)


def case_connection_read(start: int) -> dict[str, str] | str | None:
    """Connection read published since ``start``.

    None: this case did not connect. ``missing``: ``_read_con_clock`` returned
    no date. A dict is that read (date plus zone).
    """
    new = _CONNECTION_LOG[start:]
    if not new:
        return None
    last = new[-1]
    if last is None:
        return "missing"
    return last


def current_engine_clock() -> dict[str, str] | None:
    if not _ENGINE_CLOCK:
        return None
    return dict(_ENGINE_CLOCK)


def stamp_engine_clock(env: dict[str, Any]) -> dict[str, Any]:
    """Copy this answer's pending SQL clock onto its envelope, then drop it.

    A later answer, including one that ran no SQL, must not inherit it.
    """
    clock = _ENGINE_CLOCK
    if _CLOCK_PENDING and clock and clock.get("engine_as_of") and clock.get("engine_as_of_after"):
        for key in (
            "engine_as_of",
            "engine_as_of_after",
            "engine_timezone",
            "engine_timezone_after",
        ):
            val = clock.get(key)
            if val:
                env[key] = val
    clear_engine_clock()
    return env


def _read_con_clock(con: Any) -> tuple[str | None, str | None]:
    try:
        row = con.execute(
            "SELECT CAST(CURRENT_DATE AS VARCHAR), current_setting('TimeZone')"
        ).fetchone()
    except Exception:  # noqa: BLE001 - clock must not fail the query
        return None, None
    if not row:
        return None, None
    as_of = str(row[0]).strip() if row[0] is not None else ""
    tz = str(row[1]).strip() if row[1] is not None else ""
    return (as_of or None, tz or None)


def _publish_engine_clock(
    before: str | None,
    before_tz: str | None,
    after: str | None,
    after_tz: str | None,
) -> None:
    global _ENGINE_CLOCK, _CLOCK_PENDING
    if not before or not after:
        _CONNECTION_LOG.append(None)
        return
    clock = {
        "engine_as_of": before,
        "engine_as_of_after": after,
        "engine_timezone": before_tz or "",
        "engine_timezone_after": after_tz or "",
    }
    _ENGINE_CLOCK = {k: v for k, v in clock.items() if v}
    _CLOCK_PENDING = True
    _CONNECTION_LOG.append(dict(_ENGINE_CLOCK))


def read_health_engine_clock() -> dict[str, str]:
    """Point read so /health can open a live round. Not the answer SQL connection.

    The case clock is the before/after pair on the connection that ran the SQL.
    """
    try:
        con = connect_readonly()
    except Exception:  # noqa: BLE001 - health must stay up
        return {}
    try:
        as_of, tz = _read_con_clock(con)
    finally:
        con.close()
    if not as_of:
        return {}
    out = {
        "engine_as_of": as_of,
        "engine_as_of_after": as_of,
        "engine_timezone": tz or "",
        "engine_timezone_after": tz or "",
    }
    return {k: v for k, v in out.items() if v}

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


# The serving file is DuckDB. Schema context copies this string; it does not invent one.
SERVING_DIALECT = "duckdb"
# Second layer under the single-SELECT gate. File reads and writes stay off.
_SERVING_CONFIG: dict[str, str | bool | int | float | list[str]] = {
    "enable_external_access": False,
}


def connect_file(
    path: Path, *, timeout: float | None = None
) -> duckdb.DuckDBPyConnection:
    """Write-mode attach. Caller must close(); one live attach per file until then.

    ``timeout`` None waits until the lock is free, unless an ask deadline is
    armed: then the wait is whatever ``serving_lock_wait_s`` has left. A miss
    raises ``ServingLockWait`` and does not open the file.
    """
    db = Path(path)
    lock = _lock_for(db)
    _acquire_file_lock(lock, timeout)
    try:
        con = duckdb.connect(str(db), config=_SERVING_CONFIG)
    except BaseException:
        lock.release()
        raise
    return _LockedConnection(con, lock)  # type: ignore[return-value]


def acquire_serving_lock(
    path: Path, *, timeout: float | None = None
) -> threading.RLock | None:
    """Take the per-file serving lock, or None if the deadline passes.

    The wait is ``serving_lock_wait_s`` (time left on an armed ask, otherwise
    the full serving deadline). The caller must ``release()`` on the same
    thread. None means the lock was not taken.
    """
    lock = _lock_for(Path(path))
    try:
        _acquire_file_lock(lock, serving_lock_wait_s() if timeout is None else timeout)
    except ServingLockWait:
        return None
    return lock


def connect_locked_readonly(
    path: Path, *, timeout: float | None = None
) -> duckdb.DuckDBPyConnection:
    """Read-only attach under the same per-file lock as ``connect_file``.

    The lock is taken first and held until ``close()``. DuckDB 1.5 rejects a
    read-only connect while a write attach of that file is open, so a waiter
    blocks instead of raising a mixed-mode error. A missed deadline raises
    ``TimeoutError`` and does not open the file.
    """
    db = Path(path)
    lock = acquire_serving_lock(db, timeout=timeout)
    if lock is None:
        raise ServingLockWait()
    try:
        con = duckdb.connect(str(db), read_only=True, config=_SERVING_CONFIG)
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
    # Same wait as every other serving acquire. Off the ask path this still
    # waits the writer out (library lists share the file).
    _acquire_file_lock(lock, None)
    try:
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
    finally:
        lock.release()


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


def execute_sql(
    sql: str,
    *,
    path: Path | None = None,
    params: Mapping[str, Any] | None = None,
    product: bool = False,
    answer_clock: bool = False,
) -> list[dict[str, Any]]:
    """Run SELECT-shaped SQL; returns list of row dicts.

    product=True refuses a real $as_of placeholder before any execute.
    A $as_of inside a string or comment is not a placeholder. The default
    path auto-binds only a real placeholder, for oracle calls on this file.
    Only an answer publishes the process clock. A later live() must not
    adopt a clock from oracle or serving SQL that never became an answer.
    """
    real_as_of = sql_has_reserved_as_of(sql)
    if product and real_as_of:
        raise ReservedParamError(RESERVED_PARAM_AS_OF)
    con = connect_readonly(path)
    try:
        before, before_tz = _read_con_clock(con)
        bind: dict[str, Any] = dict(params) if params else {}
        if not product and real_as_of and "as_of" not in bind:
            # ponytail: omitted as_of uses this connection's CURRENT_DATE.
            # Offline only (same DuckDB file as submit()). Live must pass the
            # recorded answer-engine date; missing live date is INVALID.
            row = con.execute("SELECT CURRENT_DATE").fetchone()
            bind["as_of"] = row[0] if row else None
        rel = con.execute(sql, bind) if bind else con.execute(sql)
        cols = [d[0] for d in rel.description]
        rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        after, after_tz = _read_con_clock(con)
        if product or answer_clock:
            _publish_engine_clock(before, before_tz, after, after_tz)
        return rows
    finally:
        con.close()


def total_outbound_revenue(*, path: Path | None = None, answer_clock: bool = False) -> float:
    rows = execute_sql(_REVENUE_SQL, path=path, answer_clock=answer_clock)
    return float(rows[0]["revenue_myr"]) if rows else 0.0
