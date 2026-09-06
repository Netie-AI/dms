"""The ``ensure_demo_warehouse`` seeding contract, and the memo that keeps it cheap.

``ensure_demo_warehouse`` sits on every Library and ingest entry point
(``list_bronze_tables``, ``list_warehouse_tables`` twice - directly and again via
``connect_readonly`` - ``preview_warehouse_table``, bronze ingest). It used to
hold one module-global lock across a fresh ``duckdb.connect`` plus a seven-query
``_schema_ok`` probe, so concurrent callers of an *already seeded* warehouse were
fully re-serialised behind that probe.

Two halves are under test here and they pull against each other, which is the
whole point:

* the memo must make the seeded fast path free (no lock, no DuckDB), and
* it must not buy that by forgetting how to reseed a stale or missing schema.

Numbers and the barrier probe: ``scripts/probe_ensure_warehouse.py`` and
``docs/subagents_findings/2026-09-06_ensure-demo-warehouse-lock.md``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import duckdb
import pytest


@pytest.fixture()
def seeded(tmp_path: Path) -> Path:
    """A warehouse this process has seeded - i.e. the fast path is armed."""
    from dms_executor import demo_warehouse as dw

    path = tmp_path / "seed.duckdb"
    dw._SEEDED.clear()
    dw.ensure_demo_warehouse(path)
    return path


def _row_count(db: Path, table: str) -> int:
    con = duckdb.connect(str(db))
    try:
        row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    finally:
        con.close()
    return int(row[0]) if row else -1


def _count_probes(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count ``_schema_ok`` calls without changing what it returns."""
    from dms_executor import demo_warehouse as dw

    seen = {"n": 0}
    real = dw._schema_ok

    def counting(db: Path) -> bool:
        seen["n"] += 1
        return real(db)

    monkeypatch.setattr(dw, "_schema_ok", counting)
    return seen


# --------------------------------------------------------------------------
# The memo makes the seeded path free
# --------------------------------------------------------------------------


def test_seeded_fast_path_runs_no_schema_probe(
    seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warehouse only being read is validated once, not once per call.

    This is the deterministic form of the latency claim: ``_schema_ok`` is a
    fresh ``duckdb.connect`` plus seven queries, so "how many probes ran" is the
    mechanism behind the wall-clock number and does not vary with machine load.
    Before the fix this was 50.
    """
    from dms_executor import demo_warehouse as dw

    probes = _count_probes(monkeypatch)
    for _ in range(50):
        assert dw.ensure_demo_warehouse(seeded) == seeded
    assert probes["n"] == 0

    # And the warehouse is genuinely still there - a memo that skipped the probe
    # by skipping the work would satisfy the count above.
    assert _row_count(seeded, "transactions") == 15


def test_concurrent_ensure_on_seeded_warehouse_does_not_serialise(
    seeded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """8 barrier-released callers must not queue behind each other's probe.

    The timing bound is self-calibrating: one ``_schema_ok`` is measured on
    *this* machine first, and 8 concurrent calls must together cost less than
    that single probe. Before the fix they cost eight of them (measured: 456 ms
    for 8 threads, against a 63 ms probe), so the margin here is ~8x in the
    direction of the defect and ~25x in the direction of the fix - wide enough
    that a loaded CI box does not turn this red.
    """
    from dms_executor import demo_warehouse as dw

    t0 = time.perf_counter()
    assert dw._schema_ok(seeded)
    one_probe = time.perf_counter() - t0

    threads = 8
    gate = threading.Barrier(threads)
    errors: list[BaseException] = []
    lock = threading.Lock()
    probes = _count_probes(monkeypatch)

    def worker() -> None:
        try:
            gate.wait(timeout=60)
            dw.ensure_demo_warehouse(seeded)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    start = time.perf_counter()
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    wall = time.perf_counter() - start

    assert not errors, f"concurrent ensure_demo_warehouse raised: {errors!r}"
    assert probes["n"] == 0
    assert wall < one_probe, (
        f"{threads} concurrent ensure calls took {wall * 1000:.1f} ms, "
        f"more than one _schema_ok probe ({one_probe * 1000:.1f} ms) - "
        "the fast path is serialising again"
    )


def test_concurrent_first_seed_seeds_exactly_once(tmp_path: Path) -> None:
    """12 threads racing an unseeded path: one seeds, the rest see the result.

    ``_seed`` drops and recreates every demo table, so a second seeder is not a
    harmless duplicate - it is a caller reading a warehouse mid-truncate.
    """
    from dms_executor import demo_warehouse as dw

    db = tmp_path / "race.duckdb"
    dw._SEEDED.clear()

    seeds = {"n": 0}
    real_seed = dw._seed
    count_lock = threading.Lock()

    def counting_seed(con: duckdb.DuckDBPyConnection) -> None:
        with count_lock:
            seeds["n"] += 1
        real_seed(con)

    dw._seed = counting_seed  # type: ignore[assignment]
    try:
        threads = 12
        gate = threading.Barrier(threads)
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                gate.wait(timeout=60)
                dw.ensure_demo_warehouse(db)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        pool = [threading.Thread(target=worker) for _ in range(threads)]
        for t in pool:
            t.start()
        for t in pool:
            t.join()
    finally:
        dw._seed = real_seed  # type: ignore[assignment]

    assert not errors, f"concurrent first seed raised: {errors!r}"
    assert seeds["n"] == 1, f"expected exactly one seeder, got {seeds['n']}"
    assert _row_count(db, "transactions") == 15


# --------------------------------------------------------------------------
# ...without forgetting how to reseed
# --------------------------------------------------------------------------


def test_out_of_band_drop_is_reseeded(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The memo is keyed on the file, so a write to it re-arms the probe.

    Caching ``_schema_ok`` per *path* alone would make this call a no-op and
    hand Library a warehouse with no ``transactions`` table. Asserting the rows
    rather than "it did not raise" is the point: the broken version returns the
    same path and fails only later, in the customer's answer.
    """
    from dms_executor import demo_warehouse as dw

    con = duckdb.connect(str(seeded))
    try:
        con.execute("DROP TABLE transactions")
    finally:
        con.close()

    probes = _count_probes(monkeypatch)
    assert dw.ensure_demo_warehouse(seeded) == seeded
    assert probes["n"] == 1, "a changed file must be re-probed exactly once"
    assert _row_count(seeded, "transactions") == 15

    # ...and the refreshed memo is armed again: no probe on the next call.
    probes["n"] = 0
    dw.ensure_demo_warehouse(seeded)
    assert probes["n"] == 0


def test_stale_schema_version_is_reseeded(seeded: Path) -> None:
    """A ``SCHEMA_VERSION`` bump must reach a warehouse a previous build left."""
    from dms_executor import demo_warehouse as dw

    con = duckdb.connect(str(seeded))
    try:
        con.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    finally:
        con.close()

    dw.ensure_demo_warehouse(seeded)
    con = duckdb.connect(str(seeded))
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    finally:
        con.close()
    assert row is not None and int(row[0]) == dw.SCHEMA_VERSION


def test_deleted_file_is_reseeded(seeded: Path) -> None:
    from dms_executor import demo_warehouse as dw

    seeded.unlink()
    assert not seeded.exists()
    assert dw.ensure_demo_warehouse(seeded) == seeded
    assert _row_count(seeded, "transactions") == 15


def test_seeded_clear_forces_a_reseed(seeded: Path) -> None:
    """``dw._SEEDED.clear()`` is the memo's invalidation - fixtures depend on it.

    Roughly twenty test modules call it before pointing ``DMS_WAREHOUSE_DB`` at a
    fresh path, so it is load-bearing, not an implementation detail.
    """
    from dms_executor import demo_warehouse as dw

    seeds = {"n": 0}
    real_seed = dw._seed

    def counting_seed(con: duckdb.DuckDBPyConnection) -> None:
        seeds["n"] += 1
        real_seed(con)

    dw._seed = counting_seed  # type: ignore[assignment]
    try:
        dw.ensure_demo_warehouse(seeded)
        assert seeds["n"] == 0, "memo hit should not reseed"
        dw._SEEDED.clear()
        dw.ensure_demo_warehouse(seeded)
        assert seeds["n"] == 1, "clear() must force a reseed"
    finally:
        dw._seed = real_seed  # type: ignore[assignment]

    assert _row_count(seeded, "transactions") == 15
