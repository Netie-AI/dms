---
keywords: [stream, NDJSON, max_rows, sql extract, line chart, FETCH_CHUNK]
main_idea: Studio extract streams NDJSON chunk progress and accepts max_rows. Metric tools chart ISO dates as line. Live :8090 recycled; stream route in OpenAPI. Vault sealed. #116 still needs an engine.
---

# SQL extract stream + line charts (2026-09-08)

PREFLIGHT: HIT grouped-tool-draft.

## What landed

- `on_progress` on extract chunks. `POST /v1/studio/sources/sql/stream` is NDJSON.
- Studio Max rows field. Extract UI reads the stream.
- Time-series metric rows (ISO dates) render as line, not pie.

## Verify

- `pytest tests/test_db_connector.py tests/test_sql_source_route.py tests/test_metric_tools.py tests/invariants/test_extract_only.py tests/invariants/test_boundaries.py` -- 77 passed
- `npm test -- src/components/SimpleChart.test.ts` -- 4 passed
- Studio shows `SQL max rows`. OpenAPI has `/v1/studio/sources/sql/stream`. After recycle, `stock_on_hand` still L0 bignum.

## Not done

#116 live SQL host. Vault sealed. Goal OPEN. Stream is chunked copy, not CDC.