---
keywords: [library, duckdb, read_only, lineage-05, 500]
main_idea: "Library /tree 500s when DuckDB mixes read_only=True with a RW connection on the same file. UI fires /tree twice. List/preview/receipt must share one connect config."
models: [grok-4.6]
workflow: worktree-continue-then-pr
reuse: golden_rule
status: raw
cite: agent: this-goal
repo: DMS
date: 2026-09-03
---

# Library tree DuckDB connection config

PREFLIGHT: PARTIAL
reuse: lineage-03-library-node, playwright-cream-chrome
spawn: skip

## Golden rule

> Do not open the DMS lake `read_only=True` while ingest/promote/list still hold a write-mode connection. DuckDB 500s: "Can't open a connection to same database file with a different configuration than existing connections."

## Symptom

Playwright `library-receipt` healthy test: Library shows `Error: 500`, empty tree. Sidecar log: `list_bronze_tables` `duckdb.connect(..., read_only=True)` after `ensure_lake_schemas` on a RW handle. Fan-out test then passed (writer gone).

## Fix class

One write-mode connect for list/ensure/preview/receipt on that file (`connect_readonly` name kept; flag dropped). `beforeAll` needs `testInfo.setTimeout(120_000)` -- `describe.configure` does not cover the hook.

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_warehouse_browse.py::test_parallel_library_lists_same_file tests/test_library_promote_nodes.py -q
npx playwright test e2e/library-receipt.spec.ts
```

Does not prove: ticket ports UI :3000 + API :8090 + Cortex :8010, `verify_demo_live.py` 31/31, full 12/12.
