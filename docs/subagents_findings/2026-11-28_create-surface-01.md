# DMS-CREATE-01

**Status:** Not PASS. `tests/test_create_surface_01.py` and `tests/invariants/test_boundaries.py`: 31 passed. Armed grade52 not run. Not a live OpenVault bill.

PREFLIGHT: PARTIAL. The earlier create-surface finding covered the flag, the OpenVault role, and `create_accept`. This pass replaces the hand-built component loop with the shared provider adapter.

## What

`DMS_CREATE` defaults off. Only the digit 1 turns it on. DMS formats context, routes through OpenVault or the Cursor agents API, and judges. The model plans and checks. `packages/core/dms_core/create_generator.py` is the module the doc/slides writer imports too.

Claude requests carry a system prompt, XML tags (`schema`, `data`, `choices`, `style`, `examples`, `skills`), tool schemas, `thinking: {type: adaptive}`, and Anthropic skills (`pptx`, `docx`, `xlsx`, `pdf`) when the format needs them. Cursor is `POST /v0/agents` with no model field. The Cursor key comes from role `create_cursor_key` and is not logged.

Tools, prompts, and local `SKILL.md` files are data. A tenant can edit the brand kit, the guide, examples, and which tools are on. A section edit splices one part and keeps every version.

## Review

Stub adapter, no usage from a live key: app `Orders` cost 0.02, deck `Orders deck` cost 0.04. The judge withheld an uncited number and a secret. A disabled `run_sql` did not run. grade52 was not re-run.

## Not a word rule

No phrase list. Ungranted and destructive SQL use the existing abstain. Numbers must appear in SQL results or aggregates. Secrets use `redact_text`. PII uses `mask_payload`.

## Not PASS

No live OpenVault key and no live Cursor key. The Studio click path was not exercised in a browser.
