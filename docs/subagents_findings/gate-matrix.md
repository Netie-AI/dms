> **STATUS UPDATE 2026-10-05 (supersedes the status table below). Steps 1-4 have run on 7a8d6c1; the rendered
> matrix and the PRD hand-off are not written yet.**
>
> - Entry points n = 49. Trace: 392 cells = 45 yes, 131 partial, 103 no, 113 n/a. Raw cells with file:line
>   evidence: gate-matrix-trace.json.
> - Review (blind, seed 20261005, gate-matrix-review-sample.json): 14 of 45 yes cells re-judged, 11 agreed,
>   3 overturned (bronze-grant-abstain G1, insights ontology ranking G8, source-panel G5). Agreement 11/14 = 79%.
>   On 22 unlabeled non-yes decoys the reviewers matched 19 (86%). Inferred, not measured: if the 3-in-14 rate
>   held, about 10 of the 45 yes cells would not survive review. The other 31 yes cells are single-traced and
>   unreviewed. Treat every yes as unconfirmed until a second pass.
> - Gaps: the 234 no/partial cells reduce to 48 root-cause gaps (25 bypass, 14 systemic, 9 dormant), 0 unassigned.
>   9 tracer contradictions are listed in gate-matrix-gaps.json. Many partials depend on how strictly the gates were
>   defined (e.g. G6 has no serving-time check of served fields, G2 does not re-verify stored SQL), so read
>   class=bypass as the provable set.
> - Failing tests: tests/gate_matrix/, 28 gaps, all demonstrated. Full run by me: 19 passed (harness self-check),
>   50 xfailed, 0 failed, 0 error. Tests are xfail(strict, raises=AssertionError): the gap assertion failing is
>   XFAIL, a fixed gap is a loud XPASS, and a broken fixture is a hard failure. No product file was modified.
>   Not run (no in-process test possible): ui-derives-figures-and-labels-client-side (needs vitest) and
>   chunk-text-routes-unmasked (needs Postgres). They stay unproven by reading only.
> - Verifier caveats (gate-matrix-tests.json): the follow-up submit tests assert call counts and audit_id but not
>   the rendered text; one export test could flip for the wrong reason; test_doc_abstain_with_an_empty_engine_answer
>   is mis-scoped and should be re-done or dropped; the E7 demo-fallback variant is a semantic stretch.
> - Reviewer finding to test further: table_is_granted accepts a warehouse_ alias, so a bronze ask may read another
>   Space's table. A test exists in test_gap_grants.py; Epic should rule on whether it belongs under BRONZE-GRANT-01.
> - Not done: render the full matrix, 31 unreviewed yes cells, route findings to DMS PRD (feedback only, no tickets).

# Gate matrix - does every answer path pass every gate? (IN PROGRESS)

Plan E, read-only audit of `netie/dms` at `f0e6c61` (origin/main on 2026-10-02, BRONZE-GRANT-01 #333
merged, unstamped per the dms#303 Epic ruling). **Status: paused after step 1 of 4. Not a matrix yet.**
No gate cell below is verified. Everything under "Leads" is a claim from the enumeration agents,
to be settled by the trace step and a failing test, not quoted as a finding.

## Where it stands

| Step | State | Outcome counts |
|---|---|---|
| 1. Enumerate entry points | done | 2 blind enumerators (outside-in found 43, inside-out found 39) + 1 merge/completeness critic. Merged n = 49. Critic added 3 that both missed. 5 found only by outside-in, 4 only by inside-out. 1 dropped (`mcp:list-metrics`: returns metric definitions, not figures). |
| 2. Trace matrix (49 rows x 8 gates) | not done | Run 1: 49 tracers, one per entry point, 49/49 errored on the account session limit, 0 rows returned. Run 2: 16 tracers by shared code path (one row per entry point still), stopped on request before any cluster returned. Rows traced: 0 of 49. |
| 3. Failing test per gap | not started | 0 tests. Folder `tests/gate_matrix/` exists with the `gate_gap(row, gate, ticket)` marker. |
| 4. Independent review of 30% of pass cells | not started | n = 0. |

Deviation to record: run 2 groups related lanes (for example the three follow-up branches and the
turn cache) under one tracer to stay inside the usage limit. Each entry point still gets its own row.

## Gates

G1 grant / Space check. G2 ontology verification. G3 join rule + fan-out guard. G4 typed ingest.
G5 PII mask of the envelope and the record. G6 strict pin + served-field check. G7 Cortex
`/v1/contract/submit` (signed manifest + ledger). G8 named abstain on failure. The exact yes / partial /
no / n_a definitions are in the trace workflow script and are reused unchanged on resume.

## Known gaps already tracked

Both are Epic rulings in the dms#303 thread, not separate issues:

- **BRONZE-GRANT-01**: bronze-sheet lane reads without the Space grant. Merged as #333 = this commit,
  while Verify held it. Open question for the trace: a granted bronze table served with
  `CORTEX_WAREHOUSE_DB` pointing at a separate file (possible BRONZE-WAREHOUSE-01).
- **FOLLOWUP-CONTRACT-01**: follow-up SQL runs in DMS's own DuckDB, never calls `/v1/contract/submit`,
  has no manifest, no ledger entry, no lane field and no shared grant function. Queued, not built.

## Leads from enumeration (unverified)

Each needs a traced cell and a failing test on the synthetic seed before it counts. Citations are the
agents' and are re-checked in step 2.

1. **Bronze-sheet lane serves `L0_CERTIFIED` on local DuckDB.** No Cortex submit, no ledger entry, no
   `audit_id`, and no runtime `assert_envelope_valid` (`bronze_sheet_ask.py:186-234`, `:254-319`;
   `cca/cascade.py:449-450` returns before asserting when the cascade is off, the default). The
   zero-match case is demoted to ABSTAIN by `envelope.py:1450-1460`. BRONZE-GRANT-01 covers the grant
   only. The submit gap looks untracked.
2. **Follow-up lane serves `L2_VALIDATED` from a process-wide `_turns` cache.** Arithmetic runs on local
   DuckDB with no ledger (FOLLOWUP-CONTRACT-01). Separately, the cache is keyed only by the
   caller-supplied `session_id`, not by user identity (`session_followup.py:51-64`,
   `__init__.py:237-247`), so a second caller who sends the same `session_id` may be able to replay
   another caller's figures. That part looks untracked.
3. **Demo mode is weaker than the demo fallback.** `DMS_ASK_MODE=demo` applies no Space grant,
   ignores session and grounding, and sets no `demo_fallback_banner` (`chat.py:249-255`), which runs
   against CLAUDE.md hard rule 11.
4. **A verified-query submit or ledger error can come back as demo numbers.** `SubmitError` and ledger
   errors are not caught (`verified_queries.py:347`, `:355`). They reach the generic exception branch
   (`chat.py:336-340`), which skips the `_POLICY_CODES` screen. With `DMS_DEMO_FALLBACK=1` the caller
   gets demo numbers. Plausible, not reproduced.
5. **`_POLICY_CODES` misses three 403 codes**: `sql_not_analyzable`, `manifest_malformed` and
   `manifest_not_yet_valid` (`chat.py:34-49` vs `:71-77`). A demo fallback can therefore stand in for a
   policy refusal.
6. **A failed E1-E9 assertion on a live answer is a plain `Exception`.** With `DMS_DEMO_FALLBACK=1` it
   is answered with demo numbers (`chat.py:336-340`).
7. **Several outputs leave DMS unmasked.** Drillthrough (`chat.py:377-381`), `POST /v1/insights`
   (`routes/insights.py:182-193`), the warehouse and bronze previews (`warehouse_browse.py:114`,
   `:182`), chunk search and list (`wiring.py:51-89`) and the client CSV download. The xlsx and BI
   exports do re-mask (`bi_export.py:217`). Check against dms#303 / #318 before calling any of these new.
8. **Exports trust any envelope the caller sends** as long as it passes a shape check
   (`xlsx_export.py:117-136`). The UI also swaps drillthrough rows into the export envelope
   (`AnswerMessage.tsx:187-247`).
9. **`GET /v1/insights/*` calls no `compliance_gate`** (`routes/insights.py:97-150`). Hard rule 8
   covers only mutations, but A0007-03 gated the data-revealing GETs.
10. **Unprompted follow-up.** The UI sends a follow-up ask by itself 5 s after an exclusion-confirm
    ABSTAIN (`AnswerMessage.tsx:163-185`). The server rewrites that chip to "Top N selling SKUs by
    revenue" (`demo_ask.py:177-189`).
11. **Sources panel shows uncited figures.** It renders contribution weights as RM amounts that are not
    in `values[]` (`SourcePanel.tsx:43-47`, `:122-125`). This is an E9 concern.
12. **The verified-query grant check only covers demo tables.** It checks `DEMO_TABLES` identifiers, so
    a registered SQL naming a non-demo table passes the DMS check and only the Cortex manifest stops it
    (`verified_queries.py:54-61`, `:244-246`).

Not gaps, recorded so nobody re-hunts them:

- **Dead or shadowed code at this commit:** `bind_plan` (`live_ask` passes `bind_on_miss=False`), the
  generative paraphrase gate, the post-Cortex refuse overlay, `CortexClient.compute_query`,
  `apps/api/dms_api/scenarios/demo_ask.py` (raises) and `Executor.answer_user_sql` (no HTTP caller).
- **Grounded asks** skip follow-up, verified query, pack and generative, and land on the bronze lane or
  the Cortex contract ask.

## Resume

- **Worktree and branch:** worktree `E:/DMS-wt/gate-matrix`, branch `claude/gate-matrix-e` (local,
  not pushed), base `f0e6c61`. `main` may have moved since, so re-base the audit commit deliberately
  and record it.
- **Entry-point data:** `docs/subagents_findings/gate-matrix-entry-points.json` (merged list + all
  three agents' notes).
- **Next run:** re-run the step-2 trace workflow with the same 16 clusters. Nothing is cached, because
  no cluster completed. Then sample 30% of the yes cells with a recorded seed, run the blind reviewers
  and the test writers, and verify every test fails at its own gate, with a passing control on a gated
  path (KB R-0007, F-0011).
- **Test environment:** the system Python 3.13 has cortex-contract 1.2.0. `E:\DMS\.venv` is broken
  (it points at another machine's Python). Set `DMS_SKIP_CONTROL_PLANE_TESTS=1 DMS_DEMO_FALLBACK=0`. A
  site-packages `tests` package shadows `tests/` for `test_a2_06_scoped_ontology.py` only.
- **Routing:** new gaps go to DMS PRD as feedback. No tickets are minted from this audit.

## Entry points (n = 49)

| # | Entry point | Kind | Producer (file:line) |
|---|---|---|---|
| 1 | `chat-ask:followup-average` | ask_lane | packages/executor/dms_executor/session_followup.py:172 maybe_followup (average branch :191-225) -> _followup_execute :122 (execute_sql on local DuckDB :138) ->  |
| 2 | `chat-ask:followup-add` | ask_lane | packages/executor/dms_executor/session_followup.py:172 maybe_followup (add branch :226-259) -> _pack_compute :89; called at packages/executor/dms_executor/__ini |
| 3 | `chat-ask:followup-abstain` | ask_lane | packages/executor/dms_executor/session_followup.py:67 _abstain (plus reserved_as_of_abstain packages/executor/dms_executor/envelope.py:1830 via session_followup |
| 4 | `cache:session-turns` | cache | packages/executor/dms_executor/__init__.py:237-247 Executor._store_turn -> packages/executor/dms_executor/session_followup.py:58-64 snapshot_turn; the store is  |
| 5 | `chat-ask:verified-query` | ask_lane | packages/executor/dms_executor/verified_queries.py:319 maybe_verified_ask -> :275 envelope_from_verified_submit; called at packages/executor/dms_executor/__init |
| 6 | `chat-ask:governed-pack` | ask_lane | packages/executor/dms_executor/demo_pack.py:326 maybe_pack_ask -> :283 envelope_from_pack_submit; called at packages/executor/dms_executor/__init__.py:614-633 |
| 7 | `chat-ask:planted-refuse` | ask_lane | packages/executor/dms_executor/demo_pack.py:200 maybe_uncertified_refuse_ask; called at packages/executor/dms_executor/__init__.py:635-640 |
| 8 | `chat-ask:harness-exact-miss` | ask_lane | packages/executor/dms_executor/generative_ask.py:915 path_miss_envelope -> _abstain :670; called at packages/executor/dms_executor/__init__.py:642-650 |
| 9 | `chat-ask:cascade-abstain` | ask_lane | packages/executor/dms_executor/cca/cascade.py:338 run_cascade -> :410 cascade_abstain_envelope; invoked at packages/executor/dms_executor/__init__.py:659-686 |
| 10 | `chat-ask:cascade-attach` | other | packages/executor/dms_executor/cca/cascade.py:443 attach_cascade; applied at packages/executor/dms_executor/__init__.py:725, 761, 801-812 |
| 11 | `chat-ask:bronze-sheet` | ask_lane | packages/executor/dms_executor/bronze_sheet_ask.py:105 maybe_bronze_sheet_ask (_grouped_top_n :170, _eq_filter_total :237); called at packages/executor/dms_exec |
| 12 | `chat-ask:bronze-grant-abstain` | ask_lane | packages/executor/dms_executor/bronze_sheet_ask.py:78 bronze_grant_abstain; called at packages/executor/dms_executor/__init__.py:696-723 |
| 13 | `chat-ask:generative-pregate-abstain` | ask_lane | packages/executor/dms_executor/generative_ask.py:1020 maybe_generative_ask pre-gates :1072-1086 -> _abstain :670; called at packages/executor/dms_executor/__ini |
| 14 | `chat-ask:generative-multi-grain` | ask_lane | packages/executor/dms_executor/generative_ask.py:932 _try_multi_grain_envelope -> :766 _submit_validated -> :698 _l2_envelope (invoked :1209-1221) |
| 15 | `chat-ask:generative-insights-sql` | ask_lane | packages/executor/dms_executor/generative_ask.py:1222-1297 (kind=='sql' branch; submit :1283-1297) -> _submit_validated :766 -> _l2_envelope :698. Planner: pack |
| 16 | `chat-ask:generative-ontology-plan` | ask_lane | packages/executor/dms_executor/generative_ask.py:1359-1470 (plan_from_payload -> _compile_maybe_unverified :1001 -> _submit_validated :766); ranking overlay ont |
| 17 | `chat-ask:generative-named-abstain` | ask_lane | packages/executor/dms_executor/generative_ask.py:670 _abstain and packages/executor/dms_executor/envelope.py:1830 reserved_as_of_abstain, reached from maybe_gen |
| 18 | `chat-ask:harness-generative-miss` | ask_lane | packages/executor/dms_executor/generative_ask.py:915 path_miss_envelope; called at packages/executor/dms_executor/__init__.py:764-775 |
| 19 | `chat-ask:cortex-contract-ask` | ask_lane | packages/executor/dms_executor/__init__.py:776-814 (demo_acl + bind_session + packages/cortex_client/cortex_client/client.py:114-121 ask -> generated/api/contra |
| 20 | `chat-ask:cortex-doc-retrieval` | ask_lane | packages/executor/dms_executor/__init__.py:894 map_ask_response_to_envelope (doc stub :1006-1012) -> packages/executor/dms_executor/envelope.py:1340 build_answe |
| 21 | `chat-ask:space-refusal` | fallback | apps/api/dms_api/routes/chat.py:153 _space_refusal_envelope -> apps/api/dms_api/wiring.py:118-122 build_validated_envelope |
| 22 | `chat-ask:demo-mode` | demo | packages/executor/dms_executor/demo_ask.py:281 answer_demo_question via packages/executor/dms_executor/__init__.py:288-290 Executor.demo_ask |
| 23 | `chat-ask:demo-fallback-no-cortex` | fallback | packages/executor/dms_executor/demo_ask.py:281 answer_demo_question, restamped by apps/api/dms_api/routes/chat.py:185 _stamp_demo_fallback |
| 24 | `chat-ask:demo-fallback-ask-error` | fallback | packages/executor/dms_executor/demo_ask.py:281 + apps/api/dms_api/routes/chat.py:185 _stamp_demo_fallback |
| 25 | `chat-ask:demo-fallback-exception` | fallback | packages/executor/dms_executor/demo_ask.py:281 + apps/api/dms_api/routes/chat.py:185 _stamp_demo_fallback |
| 26 | `chat:drillthrough` | http_route | apps/api/dms_api/routes/chat.py:377-381 -> packages/cortex_client/cortex_client/client.py:268-275 drillthrough (generated/api/contract/drillthrough.py:28 '/v1/c |
| 27 | `export:xlsx` | export | packages/core/dms_core/xlsx_export.py:186 export_envelope_xlsx |
| 28 | `export:bi` | export | packages/core/dms_core/bi_export.py:203 export_envelope_bi |
| 29 | `export:csv-client` | export | apps/ui/src/components/AnswerMessage.tsx:201 downloadRowsCsv -> apps/ui/src/lib/rowsToCsv.ts:61 rowsToCsv |
| 30 | `ui:share-answer` | export | apps/ui/src/lib/answerDelivery.ts:23 shareEnvelopePayload |
| 31 | `ui:check-accuracy` | other | apps/ui/src/lib/answerDelivery.ts:68 checkAnswerTotals |
| 32 | `ui:exclusion-auto-confirm` | other | apps/ui/src/components/AnswerMessage.tsx:163-185 (auto ask(resolveExclusionNoChip(noChip)) after 5s; client rewrite :34-43); server rewrite packages/executor/dm |
| 33 | `ui:source-panel-contribution` | other | apps/ui/src/components/SourcePanel.tsx:43-47 (totalRows, totalContribution), :122-125 (pct), :153 formatMoney(src.contribution); headline apps/ui/src/lib/source |
| 34 | `ui:demo-source-fixture-preview` | demo | apps/ui/src/lib/previewFixtures.ts:20-61 (buildSheet / PREVIEW_SHEETS / sheetForSource) |
| 35 | `library:warehouse-preview` | preview | packages/executor/dms_executor/warehouse_browse.py:114 preview_warehouse_table (via apps/api/dms_api/wiring.py:92-93) |
| 36 | `library:bronze-preview` | preview | packages/executor/dms_executor/warehouse_browse.py:182 preview_bronze_table (via apps/api/dms_api/wiring.py:96-97) |
| 37 | `library:chunks-search` | preview | apps/api/dms_api/wiring.py:51-73 search_document_chunks -> packages/core/dms_core/control_plane/document_chunks.py:147 search_chunks |
| 38 | `studio:chunks-list` | preview | apps/api/dms_api/wiring.py:76-89 list_document_chunks -> packages/core/dms_core/control_plane/document_chunks.py:224 list_chunks |
| 39 | `mcp:ask` | mcp | apps/api/dms_api/routes/mcp.py:118-133 -> apps/api/dms_api/routes/chat.py:213 chat_ask |
| 40 | `mcp:preview` | mcp | apps/api/dms_api/routes/library.py:244 preview_wh_table -> packages/executor/dms_executor/warehouse_browse.py:114 |
| 41 | `insights:post-ask` | http_route | apps/api/dms_api/routes/insights.py:182-193 -> packages/cortex_client/cortex_client/client.py:243-266 insights_ask -> packages/cortex_client/cortex_client/insig |
| 42 | `insights:post-generate` | http_route | apps/api/dms_api/routes/insights.py:175-193 -> packages/cortex_client/cortex_client/insights.py:229-271 insights_post (generate branch) |
| 43 | `insights:get-ontology-ranking` | http_route | packages/cortex_client/cortex_client/client.py:234-241 insights_ontology -> packages/cortex_client/cortex_client/insights.py:210 insights_get -> _request :150 - |
| 44 | `studio:xlsx-orch-crosscheck` | studio_action | packages/executor/dms_executor/xlsx_orch.py:69 run_crosscheck -> packages/core/dms_core/xlsx_orch.py:296 crosscheck_pack -> :195 oracle_from_sheets (+ strengthe |
| 45 | `studio:xlsx-orch-extract` | studio_action | packages/executor/dms_executor/xlsx_orch.py:90 run_extract -> packages/core/dms_core/xlsx_orch.py:354 inspect_result_grids |
| 46 | `studio:xlsx-orch-golden` | studio_action | packages/executor/dms_executor/xlsx_orch.py:153 run_golden -> packages/core/dms_core/xlsx_orch.py:405 evaluate_frtr_golden |
| 47 | `cli:insights-report` | other | scripts/insights.py:199 mine (main :377) |
| 48 | `cli:brief-deck` | other | scripts/brief.py:296 build_pptx and :268 build_html (main :411) |
| 49 | `other:answer-user-sql` | other | packages/executor/dms_executor/__init__.py:256-286 answer_user_sql (Executor.execute :249-254) |
