# DMS-CREATE-01

**Status:** Not PASS. `tests/test_create_surface_01.py`: 56 passed. `tests/invariants`: 104 passed. Armed grade52 not run. Not a live OpenVault bill.

PREFLIGHT: PARTIAL. The earlier create-surface finding covered the flag, the OpenVault role, and `create_accept`. This pass replaces the hand-built component loop with the shared provider adapter.

## What

`DMS_CREATE` defaults off. Only the digit 1 turns it on. DMS formats context, routes through OpenVault or the Cursor agents API, and judges. The model plans and checks. `packages/core/dms_core/create_generator.py` is the module the doc/slides writer imports too.

Claude requests carry XML tags (`schema`, `data`, `choices`, `style`, `examples`, `skills`). The OpenVault role is `DMS_CREATE_OV_ROLE`, default `reason`. The default Claude body is a system message and a JSON reply, with no `tools` key. The system message states the kind/title/parts schema and a short example, and says the object is the artifact. Tool names are not on that path. A wrapper whose `artifact`, `result`, `output`, or `summary` is the artifact is unwrapped. `{status:needs_review}` and `{tool:run_sql}` stay a shape miss. A parse or shape miss is one repair that sends the schema error back. `DMS_CREATE_OV_TOOLS` (digit 1) is the later passthrough and stays off. `max_tokens` defaults to 8000. The timeout is 240 seconds. Spend reads `est_cost_usd`. Cursor is `POST /v0/agents` then `GET /v0/agents/{id}` until FINISHED, both on the 240 second timeout, with no model field. The branch, PR, and artifact are judged. A miss does not launch a second agent. The Cursor key is `CURSOR_API_KEY` or the secrets port, checked before the repo, and is not logged. Confirm writes pptx, pdf, and html. A doc that asks for one page is one page, charts included. A named page count is that many pages. A deck is one page per slide. The model sets chart type, x, series, and labels, and the file follows that spec. A long quarter-by-region table is one series per category. Axis numbers stay plain. The logo and title colour are drawn. PDF embeds the brand font. Slides have a footer, a slide number, data labels, and a fade. Slide boxes are the slide width minus the margins. A long title grows and the body starts below it. Body text stays at least 9pt; a one-page doc that still will not fit is trimmed and the model is asked once to shorten it. Pie wedges and extra series cycle a palette grown from the brand. Bar and column axes start at zero. A doc chart sits with its section and a short table. Two tables with the same numbers collapse to one. An unreadable long deck is an outline, then groups of four slides. Every cited figure is copied from a result row. Html shows a table once, with human column labels. Chart SQL is the aggregate and is not cut at 40. A sliced part table says how many rows were shown. A percent within one point of a multi-row ratio traces. A difference of two result numbers traces, including a rounded one. K/M/B suffixes trace with rounding tolerance. A line-leading `2.` or `2)` is a list marker, not a figure. A share claim is checked only against the chart or SQL that sentence refers to, and a one-row result is skipped. `served_model` is logged. A served model from another family warns. Log seconds are the job.

Tools, prompts, and local `SKILL.md` files are data. A tenant can edit the brand kit, the guide, examples, and which tools are on. A section edit splices one part and keeps every version. A shown app is scored from `create_score.json` against the same words with the SQL removed, and it is shown only when its points are at least that direct reading. Studio has a super guide field that is saved on the tenant.

## Review

Stub adapter, no usage from a live key: app `Orders` cost 0.02, deck `Orders deck` cost 0.04. The judge withheld an uncited number and a secret. A disabled `run_sql` did not run. grade52 was not re-run.

## Not a word rule

No phrase list. Ungranted and destructive SQL use the existing abstain. Numbers must appear in SQL results or aggregates. Secrets use `redact_text`. PII uses `mask_payload`.

## Not PASS

No live OpenVault key and no live Cursor key. The Studio click path was not exercised in a browser.
