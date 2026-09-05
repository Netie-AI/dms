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

EPIC-014 MCP-01: `apps/api/dms_api/routes/mcp.py` wraps existing
`POST /v1/chat/ask`, `GET /v1/library/warehouse/{table}/preview`,
`GET /v1/ontology/metrics`. Flag `DMS_MCP=0` (off). Regression:
`tests/test_mcp_tools.py`. Not a new serving engine.

EPIC-024 LINEAGE-01: promote receipts live in DuckDB `main._promote_receipts`
(same lake file as the target; `_` prefix hides them from Library listings).
Written at the end of `_run_silver` / `_run_gold` on the promote connection.
Read: `GET /v1/pipelines/receipts?target=` (gated, read posture).

EPIC-019 VQ-02: steward-registered Q→SQL assets live in DuckDB
`main._verified_queries` (underscore prefix). Write
`POST /v1/studio/verified-queries`, list `GET /v1/studio/verified-queries?space_id=`
(required). `live_ask` hits the Space's assets before Cortex pack match.
Studio register control: `apps/ui/src/pages/StudioPage.tsx`.
Regression: `tests/test_vq02_verified_register.py`.

EPIC-CCA constraint cascade: `packages/executor/dms_executor/cca/`. One
matching rule in `binder.py` (pack proposes, landed values decide, exact match
on a normalised form). Stage binders `sense.py`, `asset_class.py`, `geo.py`,
`segment.py`; each carries a `QUESTION_ALIASES`-style question lexicon that is
deliberately narrower than its value pack. `cascade.py` runs them on the ask
path before L0 from `Executor.live_ask`, after the verified-query hook and
before bronze-sheet and Cortex. The trace shape is CCA-01
(`constraint_cascade.py`); the envelope carries it as `constraint_trace`.
Regression: `tests/test_cca_*.py`. Surface: `apps/ui/src/lib/constraintTrace.ts`
plus `components/ConstraintTracePanel.tsx` on AuditPage.

RSF-02 typed artifacts: `packages/core/dms_core/rsf.py` (beside CCA, not inside
it; dms_core may not import dms_executor). Regression:
`tests/test_rsf_artifact.py`.
