"""Measure how much ``ensure_demo_warehouse`` serialises its already-seeded fast path.

What this measures
------------------
``ensure_demo_warehouse`` is on every Library / ingest call: ``list_bronze_tables``,
``list_warehouse_tables`` (twice - directly and again via ``connect_readonly``),
``preview_warehouse_table``, and the bronze ingest functions all call it. It used
to hold one module-global ``_LOCK`` across the *whole* fast path, including a
fresh ``duckdb.connect`` plus a seven-query ``_schema_ok`` probe, so N concurrent
callers on an already-seeded warehouse were fully re-serialised behind that probe.

MODE ``ensure`` (default) - N barrier-synchronised threads call
``ensure_demo_warehouse`` R times each against an *already seeded* path. Reports
the entry spread (how tightly the barrier released them), the return spread (the
number that matters), and per-call latency. A narrow lock shows entry spread and
return spread of the same order; a wide lock shows a return spread N times the
single-call cost.

MODE ``lists`` - the customer path at the shape
``tests/test_warehouse_browse.py::test_parallel_library_lists_same_file`` uses,
so the end-to-end effect is measured, not just the microbenchmark.

MODE ``probe`` - how many ``_schema_ok`` calls actually run, and what one costs
single-threaded. Answers "is the probe the cost, or is it the lock".

    python scripts/probe_ensure_warehouse.py                 # ensure, 8 threads x 20
    python scripts/probe_ensure_warehouse.py ensure 8 20
    python scripts/probe_ensure_warehouse.py lists 8 16
    python scripts/probe_ensure_warehouse.py probe

Always exits 0 - this is a measurement, not a gate.
"""

from __future__ import annotations

import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _pkg in ("executor", "core", "api", "cortex_client"):
    sys.path.insert(0, str(REPO / "packages" / _pkg))


def _fresh_seeded_db(tag: str):
    """A seeded warehouse whose path is already in ``_SEEDED`` - the fast path."""
    from dms_executor import demo_warehouse as dw

    db = Path(tempfile.mkdtemp(prefix=f"ensureprobe_{tag}_")) / "browse.duckdb"
    os.environ["DMS_WAREHOUSE_DB"] = str(db)
    dw._SEEDED.clear()
    dw.ensure_demo_warehouse(db)  # seeds, and marks the path seeded
    return db


def _fmt(seconds: float) -> str:
    return f"{seconds * 1000:8.2f} ms"


def _report(name: str, waves: list[tuple[list[float], list[float]]], calls: list[float]) -> None:
    """``waves`` is one (entry_times, exit_times) pair per barrier release.

    Spreads are computed *within* a wave and then aggregated, never across
    waves: a barrier only synchronises the threads it released, so a spread
    measured across two releases is the gap between waves, not contention.
    """
    entry_spreads = [max(en) - min(en) for en, _ in waves]
    walls = [max(ex) - min(en) for en, ex in waves]
    calls_sorted = sorted(calls)
    p95 = calls_sorted[max(0, int(len(calls_sorted) * 0.95) - 1)]
    print(f"\n{name}")
    print(f"  waves: {len(waves)}")
    print(f"  threads entered within        {_fmt(max(entry_spreads))} (worst wave)")
    print(f"  wall clock, barrier -> last return:"
          f" worst {_fmt(max(walls))} mean {_fmt(statistics.fmean(walls))}"
          f"   <- the serialisation cost")
    print(f"  per call: n={len(calls)} mean {_fmt(statistics.fmean(calls))}"
          f" median {_fmt(statistics.median(calls))} p95 {_fmt(p95)}"
          f" max {_fmt(max(calls))}")


def run_ensure(threads: int, repeats: int) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    db = _fresh_seeded_db("ensure")
    gate = threading.Barrier(threads)
    entries: list[float] = []
    exits: list[float] = []
    calls: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        gate.wait(timeout=120)
        t_enter = time.perf_counter()
        mine: list[float] = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            ensure_demo_warehouse(db)
            mine.append(time.perf_counter() - t0)
        t_exit = time.perf_counter()
        with lock:
            entries.append(t_enter)
            exits.append(t_exit)
            calls.extend(mine)

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    _report(f"ensure_demo_warehouse: {threads} threads x {repeats} calls (already seeded)",
            [(entries, exits)], calls)


def run_lists(workers: int, tasks: int) -> None:
    """The customer path: the three Library calls test_parallel_library_lists_same_file makes."""
    from dms_executor.bronze import list_bronze_tables
    from dms_executor.warehouse_browse import list_promote_targets, list_warehouse_tables

    db = _fresh_seeded_db("lists")
    gate = threading.Barrier(workers)
    calls: list[float] = []
    waves: list[tuple[list[float], list[float]]] = []
    lock = threading.Lock()
    entries: list[float] = []
    exits: list[float] = []

    def one() -> None:
        gate.wait(timeout=120)
        t0 = time.perf_counter()
        list_bronze_tables(path=db)
        list_warehouse_tables(path=db)
        list_promote_targets(path=db)
        t1 = time.perf_counter()
        with lock:
            entries.append(t0)
            exits.append(t1)
            calls.append(t1 - t0)

    # tasks > workers means later tasks do not see the barrier; run in waves of
    # `workers` so every task is barrier-released, matching the test's contention.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in range(max(1, tasks // workers)):
            entries, exits = [], []
            gate.reset()
            for fut in [pool.submit(one) for _ in range(workers)]:
                fut.result()
            waves.append((entries, exits))
    _report(f"Library /tree path: {workers} workers, {tasks} tasks", waves, calls)


def run_probe() -> None:
    """Single-threaded cost of one _schema_ok, and how many actually run."""
    from dms_executor import demo_warehouse as dw

    db = _fresh_seeded_db("probe")
    n = 20
    t0 = time.perf_counter()
    for _ in range(n):
        assert dw._schema_ok(db)
    probe_cost = (time.perf_counter() - t0) / n

    calls = {"n": 0}
    real = dw._schema_ok

    def counting(path):
        calls["n"] += 1
        return real(path)

    dw._schema_ok = counting  # type: ignore[assignment]
    try:
        for _ in range(50):
            dw.ensure_demo_warehouse(db)
    finally:
        dw._schema_ok = real  # type: ignore[assignment]

    t0 = time.perf_counter()
    for _ in range(50):
        dw.ensure_demo_warehouse(db)
    ensure_cost = (time.perf_counter() - t0) / 50

    print("\nsingle-threaded costs (already seeded)")
    print(f"  one _schema_ok (connect + {len(dw.DEMO_TABLES) + 1} queries) {_fmt(probe_cost)}")
    print(f"  one ensure_demo_warehouse                     {_fmt(ensure_cost)}")
    print(f"  _schema_ok calls per 50 ensure calls: {calls['n']}")


def main() -> int:
    argv = sys.argv[1:]
    mode = argv[0] if argv and not argv[0].isdigit() else "ensure"
    nums = [int(a) for a in argv if a.isdigit()]

    if mode == "lists":
        workers, tasks = (nums + [8, 16])[:2]
        run_lists(workers, tasks)
    elif mode == "probe":
        run_probe()
    else:
        threads, repeats = (nums + [8, 20])[:2]
        run_ensure(threads, repeats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
