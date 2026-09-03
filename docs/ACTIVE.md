# Active map

What exists in this repo and where. Update when structure changes, not when state changes
- state lives in STATUS.md.

S4 warehouse identity: `packages/executor/dms_executor/warehouse_identity.py`
copies Studio bronze from `DMS_WAREHOUSE_DB` into `CORTEX_WAREHOUSE_DB`.
CLI + check: `scripts/sync_bronze_to_serving.py`. Regression:
`tests/test_warehouse_identity.py`.

EPIC-016 xlsx orch (DMS half): `packages/core/dms_core/xlsx_orch.py` (pack
cross-check + FRTR golden), `packages/executor/dms_executor/xlsx_orch.py`
(read-only openpyxl + space_docs store). HTTP:
`POST /v1/studio/xlsx-orch/crosscheck|extract|golden`.
Regression: `tests/test_xlsx_orch.py`. Pointer owns Copilot paste (P-DMS-36).

EPIC-024 LINEAGE-01: promote receipts live in DuckDB `main._promote_receipts`
(same lake file as the target; `_` prefix hides them from Library listings).
Written at the end of `_run_silver` / `_run_gold` on the promote connection.
Read: `GET /v1/pipelines/receipts?target=` (gated, read posture). Nothing
renders them yet — tickets 2-4 own the UI.
