"""Reproduce the lake-schema catalog write-write conflict deterministically.

What this reproduces
--------------------
``CREATE SCHEMA IF NOT EXISTS`` is not concurrency-safe in DuckDB. ``IF NOT
EXISTS`` only skips a schema that is already *committed*, so two connections on
one file that start the same CREATE together both write the catalog entry and
DuckDB aborts the loser::

    _duckdb.TransactionException: TransactionContext Error:
      Catalog write-write conflict on create with "bronze"

Library fires ``/tree`` twice, so the first load against a warehouse whose lake
schemas do not exist yet raced itself and one request 500ed. In the suite this
surfaced as ``tests/test_warehouse_browse.py::test_parallel_library_lists_same_file``
failing roughly once in sixty runs, which read as flaky infrastructure rather
than as the defect it was reporting.

MODE ``schemas`` (default) - the root cause, barrier-synchronised so every
thread starts its catalog write at the same instant. 30/30 iterations failed
before the fix; 0/40 after.

MODE ``lists`` - the customer path the test drives (``list_bronze_tables`` ->
``list_warehouse_tables`` -> ``list_promote_targets``) at the test's own shape.

    python scripts/repro_lake_schema_race.py                # schemas, 40 x 12
    python scripts/repro_lake_schema_race.py schemas 40 12
    python scripts/repro_lake_schema_race.py lists 60 8 16

Exit 0 = no conflict observed (fix holding). Exit 1 = reproduced.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _pkg in ("executor", "core", "api", "cortex_client"):
    sys.path.insert(0, str(REPO / "packages" / _pkg))


def _fresh_db(tag: str) -> Path:
    db = Path(tempfile.mkdtemp(prefix=f"lakerace_{tag}_")) / "browse.duckdb"
    return db


def run_schemas(iters: int, threads: int) -> Counter[str]:
    """Race ensure_lake_schemas itself. This is the root cause, isolated."""
    import duckdb
    from dms_executor.lake_schema import ensure_lake_schemas

    kinds: Counter[str] = Counter()
    for i in range(iters):
        db = _fresh_db(str(i))
        duckdb.connect(str(db)).close()  # file exists, no schemas in it
        gate = threading.Barrier(threads)
        lock = threading.Lock()

        def worker() -> None:
            con = duckdb.connect(str(db))
            try:
                gate.wait(timeout=60)
                ensure_lake_schemas(con)
            except BaseException:
                with lock:
                    kinds[traceback.format_exc().strip().splitlines()[-1][:140]] += 1
            finally:
                con.close()

        pool = [threading.Thread(target=worker) for _ in range(threads)]
        for t in pool:
            t.start()
        for t in pool:
            t.join()

        # The postcondition, not just the absence of an exception: a swallowed
        # conflict that left the schema uncreated would be the worse bug.
        con = duckdb.connect(str(db))
        try:
            got = {
                r[0]
                for r in con.execute(
                    "SELECT schema_name FROM information_schema.schemata"
                ).fetchall()
            }
        finally:
            con.close()
        for missing in ("bronze", "silver", "gold", "quarantine", "dim"):
            if missing not in got:
                kinds[f"POSTCONDITION: schema {missing!r} missing after ensure"] += 1
    return kinds


def run_lists(iters: int, workers: int, tasks: int) -> Counter[str]:
    """Drive the customer path at the shape test_parallel_library_lists_same_file uses."""
    from dms_executor import demo_warehouse as dw
    from dms_executor.bronze import list_bronze_tables
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.warehouse_browse import list_promote_targets, list_warehouse_tables

    kinds: Counter[str] = Counter()
    for i in range(iters):
        db = _fresh_db(str(i))
        os.environ["DMS_WAREHOUSE_DB"] = str(db)
        dw._SEEDED.clear()
        ensure_demo_warehouse(db)  # seeds demo tables, deliberately no lake schemas
        gate = threading.Barrier(workers)

        def one() -> None:
            gate.wait(timeout=60)
            list_bronze_tables(path=db)
            list_warehouse_tables(path=db)
            list_promote_targets(path=db)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in [pool.submit(one) for _ in range(tasks)]:
                try:
                    fut.result()
                except BaseException:
                    kinds[traceback.format_exc().strip().splitlines()[-1][:140]] += 1
    return kinds


def main() -> int:
    argv = sys.argv[1:]
    mode = argv[0] if argv and not argv[0].isdigit() else "schemas"
    nums = [int(a) for a in argv if a.isdigit()]

    if mode == "lists":
        iters, workers, tasks = (nums + [60, 8, 16])[:3]
        print(f"lists: {iters} iterations, {workers} workers, {tasks} tasks")
        kinds = run_lists(iters, workers, tasks)
    else:
        iters, threads = (nums + [40, 12])[:2]
        print(f"schemas: {iters} iterations, {threads} threads")
        kinds = run_schemas(iters, threads)

    if not kinds:
        print("no conflict observed - fix holding")
        return 0
    print(f"REPRODUCED - {sum(kinds.values())} failures")
    for key, n in kinds.most_common():
        print(f"  [{n}x] {key}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
