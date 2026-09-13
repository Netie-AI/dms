---
keywords: [library, duckdb, unique-file-handle, browse.duckdb, parallel, CI, dms-ci]
main_idea: "DuckDB 1.5 unique-file-handle 500s a second RW attach of the same file. Library /tree lists serialize on one live attach; do not treat attach conflict as a reseed."
models: [grok-4.6]
workflow: ticket-runner
reuse: golden_rule
status: raw
cite: agent: this-goal
repo: DMS
date: 2026-09-13
---

# Parallel Library list unique file handle

PREFLIGHT: PARTIAL (library-tree-duckdb-config)

## Golden rule

> Do not `duckdb.connect()` a second RW handle to the same lake file. DuckDB 1.5 process-wide unique-file-handle refuses it (`Cannot attach "browse"` when the file is `browse.duckdb`). Catching that as "schema missing" reseeds (DROP) or 500s.

## Symptom

Push CI on `b5f02be` (run 34750069692): `test_parallel_library_lists_same_file` -> `list_bronze_tables` -> `ensure_demo_warehouse` line 88 `duckdb.connect`. `_schema_ok` swallowed BinderException, then the reseed connect raised.

Library UI fires `/tree` twice. Mixed RO+RW already 500s (2026-09-03 finding); RW+RW now 500s too.

## Fix class

Per-resolved-path RLock. `connect_file` / `connect_readonly` hold one live RW attach until `close()`. Seeded fast path does not probe schema via a second connect. P-DMS-34 still parked (ingest overlapping ask).

## Verify

```
python -m pytest tests/test_warehouse_browse.py tests/test_demo_warehouse_reseed.py tests/test_library_promote_nodes.py -q --tb=short
```

Does not prove: live `:8090` / `:8010`, two browser tabs vs ingest (P-DMS-34).
