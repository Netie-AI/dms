# Same-family second-model reviews merged three majors an opposite-family run reproduced

**Date:** 2026-10-09 · **Scope:** DMS PR queue sweep, 31 open PRs · **Main:** 57d85c52 · **Status:** finding, not a ruling

## Expected vs actual

- **Expected:** a second-model AGREE on the exact head catches majors before a `tier:full` squash.
- **Actual:** #404 (SCHEMA-RETRIEVE) and #405 (C-LOOP-B) merged on AGREEs from grok-4.6 reviewing grok-4.7, on their final heads (#404 @ 11ba62eb, #405 @ 8152caa9). A Claude-family review run found three majors on earlier heads. A separate Claude run then reproduced all three on main 57d85c52. Both features are behind default-off flags, and Lead Formal marks both BLOCKED (live), so nothing is served wrong today.
  1. #405, `DMS_CLOOP_B=1`: a DB error that echoes a cell value (synthetic 'Tan Wei Ming') reaches the customer envelope `text`, `assumptions`, `audit_receipt`, `loop[*].outcome`, and the `sql_feedback` sent to Cortex. `mask_feedback_text` masks only pattern-shaped PII.
  2. #404, `DMS_SCHEMA_CONTEXT=1`: `_mask_known` substring-masks every text value without word boundaries, so a Y/N column turns 'Net revenue in ringgit' into 'DMSVAL_01et reveDMSVAL_01ue iDMSVAL_01 riDMSVAL_01ggit'.
  3. #404, same flag: the value index is a 20-row `LIMIT` sample, so 'total sales for NORTH EAST' hints `region = NORTH` with `distinct=1` (true 3).
- The same sweep posted a DMS Check HOLD on #408 (vault reveal before the connector check; a pasted raw key reaches the httpx INFO log) and DISAGREEs with reproduced majors on #336, #339, #398 and #400.

## Repro

Commands and observed output are on the PRs: #405 issuecomment-6072588466, #404 issuecomment-6072588654, #408 issuecomment-6072529834. All data synthetic.

## Root-cause class

Reviewer independence. A different model from the same family shares blind spots. The PRs' own tests fed only shapes the code was written for: pattern-shaped PII in `test_c_loop_b.py:502-522`, fixture names and short tables in `test_schema_retrieve_01.py`. A review that re-runs those tests inherits the same blind spot. The opposite-family run found these by building inputs the writer had not imagined: a name in a cast error, a Y/N column next to prose, a table longer than the sample.

## Invariants

- *Adversary is not verifier:* every major was found by one run and re-verified by another before it was posted.
- *Assert the artifact the customer receives:* all three show up on the POST /v1/chat/ask envelope. None shows up in the SQL or the unit-level return values the PR tests assert.

## Not claimed

n is 2 merged PRs. This is not a rate, and it says nothing about Grok-family review quality in general. It is a reason to keep the opposite-family rule in the merge-tier text (#375) as written, and to keep `DMS_CLOOP_B` and `DMS_SCHEMA_CONTEXT` off until each defect has a fix and a test that asserts the envelope.
