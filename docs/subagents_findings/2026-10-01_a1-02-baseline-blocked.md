# A1-02 Mini-Dev baseline not run from the cloud seat (dms#264)

Keywords: A1-02, bird_minidev, baseline, BIRD-ALLOWLIST-01, dms-304, PII-MASK-02, dms-318, CURSOR_API_KEY, api.cursor.com, minidev.zip, dms-264, dms-257, dms-312
Main idea: No baseline recorded. The harness (PR #275) is on main and its self-check passes. A live n=500 run is held by a written gate: the #264 epic stamp says no Mini-Dev score runs until `bird_minidev` passes its counts-only personal-data scan, and the fail-closed allowlist for that (dms#304) is open. Per draft PR #312 (not re-run here), the 2026-09-25 local run was 500/500 ABSTAIN because generation never reached SQL. Not COMPLETE. No BIRD number.

## Expected vs actual

- Expected (#264): `score_bird.py --minidev <json> --live` over all 500 questions on the BIRD Space, graded on the `/v1/chat/ask` envelope against gold rows, recorded as the baseline with no target.
- Actual (2026-10-01, this seat): not run. Nothing recorded as baseline. The question set was not shrunk or sampled.

## Blockers, in order

1. **Personal-data gate (hard).** Epic stamp on #264 (2026-09-25): no Mini-Dev score is run or quoted until `bronze.public_*` and `bird_minidev` pass their own counts-only scan. Main at `87a9497` has PII-MASK-02 (#318) at 258 PASS / 27 FAIL of 285 flagged columns (`drivers.nationality`, `patient.diagnosis`, `users.location`, `schools.*` and others). dms#304 BIRD-ALLOWLIST-01 (fail-closed table allowlist for BIRD scoring) is open. Unlock: #304 merged and the scan passes.
2. **Generation path.** PR #312 finding: Cortex `InsightsAskIn` drops the `ontology` / `mode` fields DMS sends, so a SQL-source Space refuses before generation. ONTO-DERIVE-01 (draft PR #316) is in flight. A run before that lands reproduces 500/500 ABSTAIN and adds nothing.
3. **Named in the ticket.** dms#173 (bronze, open) and dms#196 (hosted generate via OpenVault, open).

## Changed since the ticket was written

- **Dataset reachability.** The ticket says the cloud seat cannot reach the dataset. On 2026-10-01 `https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip` answered HEAD 200, `Content-Length: 800943648`. A download was started, then stopped and the partial file deleted because of blocker 1. Nothing BIRD-derived was kept or committed.
- **Model.** `CURSOR_API_KEY` is set and authenticates: `GET https://api.cursor.com/v1/models` returned 200 with 43 model ids (includes `default`, `composer-2.5`, `grok-4.5`). `POST` to `/v1/chat/completions`, `/v1/completions` and `/v1/responses` each returned 404 `Route ... not found`. No model answered a prompt. So that base is not OpenAI-compatible for completions, and it cannot be dropped in as a provider behind Cortex as-is. The baseline needs no model. Any model-armed run goes through Cortex's gate path, never DMS to provider.

## Repro

```
curl -sS -o /dev/null -w "%{http_code}" https://api.cursor.com/v1/models -H "Authorization: Bearer $CURSOR_API_KEY"        # 200
curl -sS -w "%{http_code}" https://api.cursor.com/v1/chat/completions -H "Authorization: Bearer $CURSOR_API_KEY" \
  -H 'Content-Type: application/json' -d '{"model":"default","messages":[{"role":"user","content":"ping"}]}'           # 404
python scripts/score_bird.py --self-check                                                                              # rc 0
```

## Root-cause class

The measurement depends on upstream lanes (PII allowlist, generation through Cortex) that are not merged. Running anyway would either break a written gate or produce an all-ABSTAIN number that reads like a baseline.

## Invariant

"Silent fallback is a lie" and "A skipped test is a failing test": the baseline is reported as not run, with its unlock, not as zero.
