# Active map

What exists in this repo and where. Update when structure changes, not when state changes
- state lives in STATUS.md.

S4 warehouse identity: `packages/executor/dms_executor/warehouse_identity.py`
copies Studio bronze from `DMS_WAREHOUSE_DB` into `CORTEX_WAREHOUSE_DB`.
CLI + check: `scripts/sync_bronze_to_serving.py`. Regression:
`tests/test_warehouse_identity.py`.

XLSX-ORCH-10: `packages/executor/dms_executor/xlsx_orch.py` consumes an AirGPT
Copilot pack, strengthens it, and emits a Pointer-handoff payload. Does not paste.
Golden gate: `xlsx_golden.py` (dms#32). HTTP: `POST /v1/studio/xlsx-orch/crosscheck`.
