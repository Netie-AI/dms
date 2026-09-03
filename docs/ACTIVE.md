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
