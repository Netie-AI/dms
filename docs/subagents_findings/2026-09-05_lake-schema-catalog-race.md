# Lake schema catalog write-write race

**Date:** 2026-09-05
**Keywords:** duckdb, lake_schema, CREATE SCHEMA IF NOT EXISTS, write-write conflict, flaky test, library-tree, R-0002, R-0004, R-0005

PREFLIGHT: PARTIAL (`2026-09-03_library-tree-duckdb-config.md` covers the RO/RW
config class on this same path; nothing covered concurrent catalog creation).
Root cause class: **a check-then-create that is not atomic under concurrency**.
Invariant: **R-0004** (fix the root cause class, not the symptom) and **R-0002 /
R-0005** (a gate that refuses correct work is a failure, and must not be skipped
or quarantined to get green).

## Golden rule

> `CREATE SCHEMA IF NOT EXISTS` is not concurrency-safe in DuckDB. `IF NOT EXISTS`
> only skips a schema that is already **committed**, so two connections on one
> file that start the same CREATE together both write the catalog entry and
> DuckDB aborts the loser. Any `CREATE ... IF NOT EXISTS` on a shared DuckDB file
> needs the postcondition re-checked after a conflict, not just the `IF NOT EXISTS`.

## Expected vs actual

**Expected:** `tests/test_warehouse_browse.py::test_parallel_library_lists_same_file`
passes on every run. Library serves both concurrent `/tree` requests.

**Actual:** roughly 1 failure in 60 runs of the test's own shape; observed as 1
failure in 3 full-suite runs on `claude/build-dms-tickets-scale-4df6a9` (PR #155),
whose diff touches no DuckDB code. The exception is not in the test harness:

```
File "packages/executor/dms_executor/bronze.py", line 524, in list_bronze_tables
    ensure_lake_schemas(con)
File "packages/executor/dms_executor/lake_schema.py", line 10, in ensure_lake_schemas
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
_duckdb.TransactionException: TransactionContext Error:
  Catalog write-write conflict on create with "bronze"
```

This is the **production path**, not test setup. Library fires `/tree` twice; the
first load against a warehouse whose lake schemas do not exist yet races itself
and one request 500s for the customer. Eight call sites share
`ensure_lake_schemas` (bronze ingest x5, promote, warehouse browse, warehouse
identity), so every one of them carried it.

Verified on duckdb 1.5.5 / Python 3.13.15. Conflict is MVCC, not filesystem, so
it is not Windows-specific; CI is `ubuntu-latest`.

## Repro

```
python scripts/repro_lake_schema_race.py schemas 40 12   # root cause, isolated
python scripts/repro_lake_schema_race.py lists 60 8 16   # customer path
python -m pytest tests/test_warehouse_browse.py::test_ensure_lake_schemas_concurrent_first_create -q
```

Barrier-synchronised. Measured, pre-fix vs post-fix on the same machine:

| Check | Pre-fix | Post-fix |
|---|---|---|
| `schemas` 30 iters x 8 threads | 30/30 iterations failed | not re-run at this shape |
| `schemas` 15 iters x 12 threads | 164 aborted threads | -- |
| `schemas` 40 iters x 12 threads | -- | 0 failures |
| lock disabled, 25 iters x 8 threads | -- | 0 failures, all 5 schemas present |
| `test_ensure_lake_schemas_concurrent_first_create` | fails, 11 of 12 threads aborted | 5/5 runs passed |

The `lock disabled` row is the one that matters for correctness: it shows the
postcondition re-check, not the in-process lock, is what makes this safe.

## Why it looked flaky rather than broken

`ensure_demo_warehouse` takes a module-global `_LOCK` and holds it across a fresh
`duckdb.connect` plus a seven-query schema probe. Measured: 8 threads entered
within **0.7 ms** of each other and left over a **22.7 s** spread. Threads
released together are fully re-serialised before any of them reaches
`ensure_lake_schemas`, so the race window is only the gap after that lock
releases -- hence ~1 in 60 rather than every run.

That is why a barrier at task entry does **not** make the end-to-end test
deterministic, and why the deterministic guard had to go on `ensure_lake_schemas`
directly.

## Fix class

`packages/executor/dms_executor/lake_schema.py` -- the shared function, not any
one of its eight callers (R-0004):

- a module lock serialises the catalog write in-process (the DMS API's real
  shape: one uvicorn process, a threadpool), matching the `_REGISTRY_LOCK`
  idiom already in `bronze.py` for the other catalog writer on this path;
- a conflict is swallowed **only** when the schema is present afterwards. The
  contract is "these schemas exist when this returns", not "this connection
  created them". Verified: with the lock disabled entirely, 8 barrier-synced
  threads x 25 iterations all succeeded with all 5 schemas present -- so the
  re-check carries correctness on its own and the fix is not single-process-only.

A DuckDB connection is **usable** after a write-write conflict, and the winner's
schema is visible to it. Confirmed directly before relying on it.

## Tests

- `test_ensure_lake_schemas_concurrent_first_create` -- new, deterministic,
  12 barrier-synced threads. Asserts no exception **and** the postcondition
  (all five schemas present): swallowing a conflict without creating the schema
  would be the worse bug. Does not import `LAKE_SCHEMAS` from the module under
  test, so it stays runnable against the pre-fix module -- which is how it was
  shown to fail before being trusted green (R-0007).
- `test_parallel_library_lists_same_file` -- kept at 8 workers / 16 tasks, not
  reduced. Upgraded from "nothing raised" to asserting the rows the customer
  receives: `list_warehouse_tables` swallows a per-table count failure as
  `row_count 0`, so a path that degraded under contention satisfied the old
  assertion by handing back zeros (CLAUDE.md rule 10). Its docstring now states
  what it does **not** guarantee.

## Does not prove

- The remaining Windows-only `IOException` ("The process cannot access the file
  because it is being used by another process" / "Insufficient system
  resources") seen at `duckdb.connect` under heavy temp-file churn is **not**
  fixed and not explained. Seen 2x in 60 harness iterations, 1x in 10, always in
  a harness creating a fresh temp DuckDB per iteration. Not reproduced in a
  pytest run. Separate class; open item.

  Context that may explain it rather than a code defect: this machine runs
  several lanes at once (24 python processes during this work, one at 3130 CPU
  seconds), and Git Bash on it was concurrently failing to fork
  (`fork: Resource temporarily unavailable`, `fork: File too large`,
  `CreateProcessW failed ... errno 27`). "Insufficient system resources" at
  `duckdb.connect` is consistent with that host pressure. Do not chase it as a
  DuckDB bug before ruling out the host.
- `ensure_demo_warehouse` holding a global lock across a connect plus a
  seven-query probe (22.7 s for 8 threads) is a real serialisation defect. Left
  alone deliberately -- out of scope here.
- No claim about `<1%` residual flake rate: rule of three needs n>=300 (R-0010).
