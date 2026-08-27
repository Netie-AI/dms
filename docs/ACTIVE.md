# Active map

What exists in this repo and where. Update when structure changes, not when state changes
- state lives in STATUS.md.

S4 warehouse identity: `packages/executor/dms_executor/warehouse_identity.py`
copies Studio bronze from `DMS_WAREHOUSE_DB` into `CORTEX_WAREHOUSE_DB`.
CLI + check: `scripts/sync_bronze_to_serving.py`. Regression:
`tests/test_warehouse_identity.py`.

XLSX-ORCH-11: `packages/executor/dms_executor/xlsx_extract.py` copies a
Copilot-built workbook into content-addressed blobs + sidecar
`artifacts/{id}.json` and (when Postgres is up) `dms.space_artifacts`
(kind `xlsx_result`). Not ingested originals (`data_sources` /
`document_chunks` — AirGPT #20). Route: `POST /v1/studio/xlsx-extract`.
