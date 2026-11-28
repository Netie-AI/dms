# DMS-CREATE-01

**Status:** Not PASS. `tests/test_create_surface_01.py` and `tests/invariants/test_boundaries.py`: 31 passed. Armed grade52 not run. Not a live OpenVault bill.

PREFLIGHT: PARTIAL. The earlier create-surface finding covered the flag, the OpenVault role, and `create_accept`. This pass replaces the hand-built component loop with the shared provider adapter.

## What

`DMS_CREATE` defaults off. Only the digit 1 turns it on. DMS formats context, routes through OpenVault or the Cursor agents API, and judges. The model plans and checks. `packages/core/dms_core/create_generator.py` is the module the doc/slides writer imports too.

Claude requests carry a system prompt, XML tags (`schema`, `data`, `choices`, `style`, `examples`, `skills`), tool schemas, `thinking: {type: adaptive}`, and Anthropic skills (`pptx`, `docx`, `xlsx`, `pdf`) when the format needs them. The OpenVault role is `DMS_CREATE_OV_ROLE`, default `reason`. The default Claude body is a system message and a JSON reply, with no `tools` key. `DMS_CREATE_OV_TOOLS` (digit 1) is the later passthrough and stays off. `max_tokens` defaults to 8000. The timeout is 240 seconds. Spend reads `est_cost_usd`. Cursor is `POST /v0/agents` with no model field. The Cursor key is `CURSOR_API_KEY` or the secrets port, checked before the repo, and is not logged. Confirm writes pptx, pdf, and html. A one-page doc is one page. Chart SQL is read-only, and a share claim has to match the computed shares.

Tools, prompts, and local `SKILL.md` files are data. A tenant can edit the brand kit, the guide, examples, and which tools are on. A section edit splices one part and keeps every version. A shown app is scored from `create_score.json` against the same words with the SQL removed, and it is shown only when its points are at least that direct reading. Studio has a super guide field that is saved on the tenant.

## Review

Stub adapter, no usage from a live key: app `Orders` cost 0.02, deck `Orders deck` cost 0.04. The judge withheld an uncited number and a secret. A disabled `run_sql` did not run. grade52 was not re-run.

## Not a word rule

No phrase list. Ungranted and destructive SQL use the existing abstain. Numbers must appear in SQL results or aggregates. Secrets use `redact_text`. PII uses `mask_payload`.

## Not PASS

No live OpenVault key and no live Cursor key. The Studio click path was not exercised in a browser.
