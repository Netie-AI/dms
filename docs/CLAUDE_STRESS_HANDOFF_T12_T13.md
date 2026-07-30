# CLAUDE_STRESS_HANDOFF — T12/T13 (2026-07-30)

For Claude Code: stress-test and verify. Do not invent behavior beyond this packet.

## Prior session ([56ef4bb2](56ef4bb2-d481-4c86-a154-4ba3cb11da4e))

Demo-core T3–T8 (Spaces, live ask, bronze light, amend+F5, actor headers, Caddy, UI shells). Ended ~53 tests. Substrate landed in commit `a74ec80` with T12.

## This session

| Commit | What |
|--------|------|
| `a74ec80` | T12 DuckDB promote: YAML `pipelines/`, contract gate, quarantine+reasons, `_src[]` propagate, idempotent dedup, gold steward sign via Cortex ledger, contract infer (propose only), lineage invariant |
| `ba744e6` | T13 triage: 5 classes, shape fingerprint, batch honest receipts, 14 fixtures, Studio multi-file |

Verified locally: **81 pytest passed**, **3 import-linter contracts kept**.

## Architecture locks exercised

- `duckdb.execute` only under `packages/executor`
- Mutations call `compliance_gate` (`/v1/pipelines/*`, `/v1/studio/ingest*`)
- `_src` = struct array `[{ref_id, row}]` (DuckDB 1.5: use struct literal, not `struct_pack(... row := ...)` — `row` is reserved)
- Excel: `openpyxl.load_workbook(read_only=True)` only — never Workbook/save/to_excel
- Wiring imports `import dms_executor` only (submodule `from dms_executor import x` breaks import-linter when `x` collides with a module name)

## Key paths

```
packages/core/dms_core/pipelines.py
packages/core/dms_core/triage.py
packages/executor/dms_executor/promote.py
packages/executor/dms_executor/pipeline_loader.py
packages/executor/dms_executor/contract_infer.py
packages/executor/dms_executor/triage.py
packages/executor/dms_executor/batch_ingest.py
packages/executor/dms_executor/bronze.py
pipelines/silver_sales.yaml
apps/api/dms_api/routes/pipelines.py
apps/api/dms_api/routes/studio.py
tests/test_pipeline_promote.py
tests/test_ingest_triage.py
tests/invariants/test_pipeline_lineage.py
tests/fixtures/ingest/*.csv   (14)
```

## Stress commands

```powershell
cd D:\DMS
$env:DMS_WAREHOUSE_DB = "$pwd\data\stress.duckdb"
python -m pytest tests/test_pipeline_promote.py tests/invariants/test_pipeline_lineage.py -q --tb=short
python -m pytest tests/test_ingest_triage.py tests/test_smoke.py -q --tb=short
python -m pytest -q --tb=line
python -c "from importlinter.cli import lint_imports; raise SystemExit(lint_imports())"
```

### Hostile checks

1. Re-run same promote YAML twice → silver row count unchanged (dedup).
2. Inject YAML missing `lineage` → `PipelineLoadError` / invariant fail.
3. Two-source join → `len(_src) == 2`.
4. Batch of clean+dirty+multi+unstructured → summary names each non-clean with `reason` + `fix`; UNSTRUCTURED has `blob_key` / `document_index=pending`, `table is None`.
5. AST: no `to_excel` / `openpyxl.Workbook` / `.save` excel writes; no `duckdb.execute` outside executor.
6. `POST /v1/pipelines/infer-contract` must not create silver tables.
7. Gold promote without signed metric + `ledger_entry_id` must refuse.

## Plan-only next (do not implement until packets exist)

1. **T14** — signed answer receipts + verify page (highest priority post-T12/T13).
2. **T13b** — Repair Desk matching T13 shape fingerprints.
3. **T15** — contribution rollup + generated export (never Excel outbound write).
4. **T9** external tables → after promote/triage proven on files.
5. **T10/T11** multi-pool / MinIO — demand-gated.

## Known gaps for stress

- UI Library/Amend/Audit pages may still be uncommitted local edits.
- `source_ref` / `ingest_run` Postgres rows still not fully persisted from DuckDB bronze path.
- F5 soft-allow on `gate_task_unknown` remains until Cortex task catalog packs DMS tasks.
- Live `scripts/smoke_live_ask.py` needs Cortex + OpenVault up.
