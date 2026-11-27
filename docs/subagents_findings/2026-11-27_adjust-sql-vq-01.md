# ADJUST-SQL-VQ-01

**Status:** Not PASS. `tests/test_studio_adjust_01.py` 9 passed. UI vitest `AdjustSqlPanel.test.ts`, `ResultView.test.ts`, and `AnswerMessage.test.ts` 18 passed. ruff, mypy, and import-linter passed. Full `pytest tests/` with `DMS_SKIP_CONTROL_PLANE_TESTS=1` was started. grade52 was not run. Armed 52 and pack H were not run. Not PASS.

## What

`DMS_STUDIO_ADJUST` defaults to off. Only the digit 1 enables it. The router is mounted from `app.py` only when that check is true, so flag off the OpenAPI document matches main and `POST /v1/studio/adjust/run` is 404.

On, a steward (`holds_steward_grant`) can `POST /v1/studio/adjust/run` and `POST /v1/studio/adjust/save`. Both call `validate_compiled_sql` then `run_readonly`. The refusal string is that checker code. Rows go through `fail_closed_mask_payload`. Save stores provenance `steward_adjusted`, the configured actor, and the catalog schema hash. `retrieve_verified_examples` returns the row when `DMS_VQ_RAG` is on. A viewer is 403 and nothing is stored.

The answer view mounts the button only for role steward, and the panel stays null until `GET /v1/studio/adjust/status` returns `enabled: true`. A static render has no button and no textarea.

## Not a word rule

The question is the chat ask passed into the panel. The SQL gates are sqlglot plus DuckDB. No question-text table.

## Ceiling

ponytail: the preview returns at most 200 rows and says `truncated`. Upgrade: the ask page size. The UI pages the first 10 of what the API returned.
