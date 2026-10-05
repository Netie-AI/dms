# Gate matrix - does every answer path pass every gate?

Plan E, a read-only audit of `netie/dms` at `7a8d6c1` (origin/main on 2026-10-05, BRONZE-GRANT-01 #333 and BRONZE-WAREHOUSE-01 #335 merged). **Generated** by `tests/gate_matrix/render_matrix.py` from the JSON beside it; do not hand-edit. Feedback for the PRD, not a fix and not a ticket: no product code was changed.

## Read this first

- **No entry point passes all 8 gates.** This describes the code, not an accuracy figure.
- **The gate definitions are strict and mine.** Part of the partial/no count comes from the definition (for example G6 asks for a serving-time check of served fields, which exists only in the offline scorer). Class `bypass` in the gap table is the provable set: a request on default or documented config that a test shows.
- **A tracer's yes counts only if a blind reviewer agreed.** A disagreement went to a third blind reviewer and the majority of three decides; `Y?` is a three-way split, treated as not passing.
- **Everything is from reading code at one commit, plus the tests.** No live lane, no real model, no Cortex enforcement was exercised. Test fakes stand in for Cortex.

## Outcome counts

- Entry points n = 49 (two blind enumerators found 43 and 39, a critic merged them and added 3). All 49 traced.
- Cells n = 392 = 49 x 8: yes 38 (of which 4 survived only on a 2-of-3 tie-break), unresolved split 0, partial 138, no 103, n/a 113. 7 tracer yes cells were lowered by the review and are counted under their new verdict.

| Gate | yes | Y? (split) | partial | no | n/a |
|---|---|---|---|---|---|
| G1 grant / Space check | 2 | 0 | 30 | 5 | 12 |
| G2 ontology verification | 1 | 0 | 2 | 25 | 21 |
| G3 join rule + fan-out guard | 3 | 0 | 0 | 12 | 34 |
| G4 typed ingest | 6 | 0 | 8 | 14 | 21 |
| G5 PII mask (envelope + record) | 19 | 0 | 27 | 3 | 0 |
| G6 strict pin + served fields | 0 | 0 | 21 | 13 | 15 |
| G7 Cortex submit (manifest + ledger) | 4 | 0 | 7 | 28 | 10 |
| G8 named abstain on failure | 3 | 0 | 43 | 3 | 0 |

## Independent review of the yes cells

- Every tracer yes was re-traced blind (the reviewer saw no earlier verdict or evidence, and each reviewer also judged unlabeled non-yes cells from the same row). Pass 1 was a seeded 30% sample (seed 20261005, 14 of 45 yes cells). Pass 2 covered the remaining yes cells.
- Yes cells re-judged: n = 45 of 45. Reviewer agreed 34, overturned 11. Agreement 34/45.
- Unlabeled non-yes cells: n = 42, reviewer reached the same verdict on 39.
- Overturned (tracer said yes): cache:session-turns G5 (reviewer: partial); chat-ask:bronze-grant-abstain G1 (reviewer: partial); chat-ask:bronze-grant-abstain G8 (reviewer: partial); chat-ask:generative-insights-sql G8 (reviewer: partial); chat-ask:harness-exact-miss G8 (reviewer: partial); chat-ask:planted-refuse G8 (reviewer: partial); chat-ask:space-refusal G1 (reviewer: partial); insights:get-ontology-ranking G8 (reviewer: partial); mcp:preview G4 (reviewer: partial); ui:check-accuracy G5 (reviewer: partial); ui:source-panel-contribution G5 (reviewer: partial).
- Reviewers agreed with 34 of 45 tracer yes cells (75%) but with 39 of 42 non-yes cells (92%). A tracer yes is therefore weaker evidence than a tracer partial or no: false passes were the common mistake. These are model reviews of the same code, not a measured error rate.
- Tie-break: the 11 overturned cells went to a third blind reviewer (seed 20261007 chose one unlabeled decoy gate per row). 4 kept yes, 7 lowered (2 of 3). The majority of three decides; see the matrix for each cell.

## Matrix

`yes` = tracer and blind reviewer agree; `(2/3)` = kept or lowered by a 2-of-3 tie-break. `Y?` = three different verdicts (not counted as passing). `part` = partial. `**NO**` = gate absent on that path. `n/a` = gate cannot apply (reason cited in the section below). Each row links to its cited cells.

| Entry point | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 |
|---|---|---|---|---|---|---|---|---|
| [`chat-ask:followup-average`](#ep-chat-ask-followup-average) | part | **NO** | n/a | n/a | yes | part | **NO** | part |
| [`chat-ask:followup-add`](#ep-chat-ask-followup-add) | part | **NO** | n/a | n/a | yes | part | **NO** | part |
| [`chat-ask:followup-abstain`](#ep-chat-ask-followup-abstain) | part | n/a | n/a | n/a | yes | part | n/a | part |
| [`cache:session-turns`](#ep-cache-session-turns) | part | **NO** | n/a | n/a | yes(2/3) | part | **NO** | part |
| [`chat-ask:verified-query`](#ep-chat-ask-verified-query) | part | **NO** | **NO** | part | part | part | yes | part |
| [`chat-ask:governed-pack`](#ep-chat-ask-governed-pack) | part | **NO** | **NO** | part | yes | part | yes | part |
| [`chat-ask:planted-refuse`](#ep-chat-ask-planted-refuse) | n/a | n/a | n/a | n/a | yes | part | n/a | part(2/3) |
| [`chat-ask:harness-exact-miss`](#ep-chat-ask-harness-exact-miss) | n/a | n/a | n/a | n/a | yes | part | n/a | part(2/3) |
| [`chat-ask:cascade-abstain`](#ep-chat-ask-cascade-abstain) | part | n/a | n/a | **NO** | yes | part | n/a | part |
| [`chat-ask:cascade-attach`](#ep-chat-ask-cascade-attach) | part | **NO** | **NO** | **NO** | part | part | part | part |
| [`chat-ask:bronze-sheet`](#ep-chat-ask-bronze-sheet) | yes | **NO** | n/a | **NO** | yes | part | **NO** | part |
| [`chat-ask:bronze-grant-abstain`](#ep-chat-ask-bronze-grant-abstain) | part(2/3) | n/a | n/a | n/a | yes | part | n/a | yes(2/3) |
| [`chat-ask:generative-pregate-abstain`](#ep-chat-ask-generative-pregate-abstain) | n/a | n/a | n/a | n/a | yes | part | n/a | yes |
| [`chat-ask:generative-multi-grain`](#ep-chat-ask-generative-multi-grain) | part | part | yes | part | yes | part | yes | part |
| [`chat-ask:generative-insights-sql`](#ep-chat-ask-generative-insights-sql) | part | **NO** | **NO** | part | part | part | yes | part(2/3) |
| [`chat-ask:generative-ontology-plan`](#ep-chat-ask-generative-ontology-plan) | part | part | yes | part | part | part | part | part |
| [`chat-ask:generative-named-abstain`](#ep-chat-ask-generative-named-abstain) | part | n/a | n/a | n/a | part | part | n/a | part |
| [`chat-ask:harness-generative-miss`](#ep-chat-ask-harness-generative-miss) | part | n/a | n/a | n/a | yes | part | n/a | part |
| [`chat-ask:cortex-contract-ask`](#ep-chat-ask-cortex-contract-ask) | part | **NO** | **NO** | part | part | **NO** | part | part |
| [`chat-ask:cortex-doc-retrieval`](#ep-chat-ask-cortex-doc-retrieval) | part | n/a | n/a | n/a | part | **NO** | **NO** | part |
| [`chat-ask:space-refusal`](#ep-chat-ask-space-refusal) | yes(2/3) | n/a | n/a | n/a | yes | **NO** | n/a | yes |
| [`chat-ask:demo-mode`](#ep-chat-ask-demo-mode) | **NO** | **NO** | n/a | yes | yes | **NO** | **NO** | part |
| [`chat-ask:demo-fallback-no-cortex`](#ep-chat-ask-demo-fallback-no-cortex) | **NO** | **NO** | n/a | yes | yes | **NO** | **NO** | part |
| [`chat-ask:demo-fallback-ask-error`](#ep-chat-ask-demo-fallback-ask-error) | part | **NO** | n/a | yes | yes | **NO** | **NO** | **NO** |
| [`chat-ask:demo-fallback-exception`](#ep-chat-ask-demo-fallback-exception) | part | **NO** | n/a | yes | part | **NO** | **NO** | **NO** |
| [`chat:drillthrough`](#ep-chat-drillthrough) | part | n/a | **NO** | **NO** | part | n/a | **NO** | part |
| [`export:xlsx`](#ep-export-xlsx) | n/a | n/a | n/a | n/a | part | **NO** | **NO** | part |
| [`export:bi`](#ep-export-bi) | n/a | n/a | n/a | n/a | part | **NO** | **NO** | part |
| [`export:csv-client`](#ep-export-csv-client) | part | n/a | n/a | n/a | part | n/a | part | **NO** |
| [`ui:share-answer`](#ep-ui-share-answer) | n/a | n/a | n/a | n/a | part | **NO** | part | part |
| [`ui:check-accuracy`](#ep-ui-check-accuracy) | n/a | **NO** | n/a | n/a | part(2/3) | n/a | **NO** | part |
| [`ui:exclusion-auto-confirm`](#ep-ui-exclusion-auto-confirm) | part | **NO** | **NO** | part | part | part | part | part |
| [`ui:source-panel-contribution`](#ep-ui-source-panel-contribution) | n/a | **NO** | n/a | n/a | part(2/3) | n/a | **NO** | part |
| [`ui:demo-source-fixture-preview`](#ep-ui-demo-source-fixture-preview) | n/a | **NO** | n/a | n/a | yes | n/a | **NO** | part |
| [`library:warehouse-preview`](#ep-library-warehouse-preview) | part | n/a | n/a | yes | part | n/a | **NO** | part |
| [`library:bronze-preview`](#ep-library-bronze-preview) | part | n/a | n/a | **NO** | part | n/a | **NO** | part |
| [`library:chunks-search`](#ep-library-chunks-search) | part | n/a | n/a | **NO** | part | n/a | **NO** | part |
| [`studio:chunks-list`](#ep-studio-chunks-list) | part | n/a | n/a | **NO** | part | n/a | **NO** | part |
| [`mcp:ask`](#ep-mcp-ask) | part | **NO** | **NO** | **NO** | part | part | part | part |
| [`mcp:preview`](#ep-mcp-preview) | part | n/a | n/a | yes(2/3) | part | n/a | **NO** | part |
| [`insights:post-ask`](#ep-insights-post-ask) | **NO** | **NO** | **NO** | **NO** | part | **NO** | **NO** | part |
| [`insights:post-generate`](#ep-insights-post-generate) | **NO** | **NO** | **NO** | **NO** | part | part | **NO** | part |
| [`insights:get-ontology-ranking`](#ep-insights-get-ontology-ranking) | n/a | n/a | n/a | n/a | part | **NO** | n/a | part(2/3) |
| [`studio:xlsx-orch-crosscheck`](#ep-studio-xlsx-orch-crosscheck) | part | **NO** | n/a | **NO** | part | n/a | **NO** | part |
| [`studio:xlsx-orch-extract`](#ep-studio-xlsx-orch-extract) | part | **NO** | n/a | **NO** | **NO** | n/a | **NO** | part |
| [`studio:xlsx-orch-golden`](#ep-studio-xlsx-orch-golden) | part | **NO** | n/a | **NO** | part | n/a | **NO** | part |
| [`cli:insights-report`](#ep-cli-insights-report) | n/a | yes | yes | part | **NO** | n/a | **NO** | part |
| [`cli:brief-deck`](#ep-cli-brief-deck) | n/a | **NO** | **NO** | n/a | **NO** | n/a | **NO** | part |
| [`other:answer-user-sql`](#ep-other-answer-user-sql) | **NO** | **NO** | **NO** | **NO** | yes | **NO** | **NO** | part |

## Gaps and their tests

The 234 no/partial cells reduce to 48 root-cause gaps (bypass 25, systemic 14, dormant 9), 0 unassigned. `bypass` = a request can show it; `systemic` = the gate exists only in the offline scorer or the definition is stricter than the design; `dormant` = needs a non-default flag or is dead code. Tests are in `tests/gate_matrix/` as strict xfails: the gap assertion failing is an expected failure, a fixed gap becomes a loud XPASS, anything else is a hard failure. Run `python -m pytest tests/gate_matrix -p no:cacheprovider --runxfail` to see the real failures.

| Gap | Class | Tracked by | Cells | Test result | Severity hint |
|---|---|---|---|---|---|
| `no-space-ask-widest-grant` | bypass | new | 12 | demonstrated (1 test) | high: an invariant the project records as closed (A-0007) is still open on the main ask p... |
| `audit-receipt-and-late-added-cards-skip-mask` | bypass | new | 7 | demonstrated (4 tests) | medium: exclude.reasons[].detail is predicate text that routinely carries the user's filt... |
| `ui-derives-figures-and-labels-client-side` | bypass | new | 6 | unproven, no in-process test: UI logic: runs under vitest with no network, but not under the Python... | medium: confident-looking UI claims ('Certified answer', fabricated RM/percent weights, f... |
| `xlsx-orch-coercion-and-false-ok` | bypass | new | 6 | demonstrated (2 tests) | medium: an oracle whose job is to catch Copilot errors answers ok:true on unreadable inpu... |
| `preview-and-drill-rows-unmasked` | bypass | new | 5 | demonstrated (2 tests) | high: unauthenticated GETs (DR-0004 Option A) return the most likely place for personal d... |
| `followup-no-submit` | bypass | FOLLOWUP-CONTRACT-01 | 4 | demonstrated (3 tests) | high: a green L2 figure whose audit_id resolves to nothing in the ledger; already ruled a... |
| `followup-no-grant-recheck` | bypass | FOLLOWUP-CONTRACT-01 | 4 | demonstrated (2 tests) | medium: partition by (session, Space) holds; the gap is relative to the ruling and to the... |
| `ledger-append-carries-unmasked-sql` | bypass | new | 4 | demonstrated (3 tests) | medium: an append-only ledger cannot be erased, but exposure needs a PII literal inside t... |
| `export-projections-no-provenance-tie` | bypass | new | 4 | demonstrated (1 test) | medium: caller can mint a workbook claiming L0 without any ask; there is no principal (DR... |
| `followup-unit-relabelled-rm` | bypass | new | 3 | demonstrated (2 tests) | medium: wrong unit on a green figure; arithmetic is on figures already shown. |
| `followup-stale-prior-after-failed-turn` | bypass | new | 3 | demonstrated (2 tests) | low: stale but real earlier figures; mislabelled recency rather than an invented number. |
| `bronze-sheet-trycast-drops-rows-silently` | bypass | new | 3 | demonstrated (1 test) | high: understated total with a green L0_CERTIFIED badge is the plausible-number-plus-gree... |
| `insights-routes-return-payload-unmasked` | bypass | new | 3 | demonstrated (1 test) | medium: depends on Cortex returning personal data, but DMS adds no control. |
| `xlsx-orch-cross-space-path-read` | bypass | new | 3 | demonstrated (1 test) | medium: any caller of a gated mutation route can read any xlsx under the data dir; no pri... |
| `xlsx-orch-unmasked-and-raw-persist` | bypass | new | 3 | demonstrated (1 test) | low-medium: aggregates and labels in an external-caller tool; raw persistence is by desig... |
| `stored-sql-no-fanout-guard` | bypass | new | 2 | demonstrated (1 test) | medium: requires a steward-authored fan-out join, but the figure ships with the top trust... |
| `bronze-sheet-l0-minted-locally` | bypass | new | 2 | demonstrated (1 test) | high: top trust tier minted locally; breaks hard rule 3 (one ledger) and F83. |
| `chunk-text-routes-unmasked` | bypass | new | 2 | unproven, no in-process test: DB path needs Postgres (tests/control_plane fixtures require it). A r... | medium: free-text PII exposure, but only with Postgres configured. |
| `drillthrough-failure-and-truncation-not-named` | bypass | new | 2 | demonstrated (2 tests) | low-medium: a failed drill reads as 'no rows' and a capped drill can export as complete. |
| `generative-sql-ships-over-failed-verify-ontology` | bypass | #258 | 1 | demonstrated (1 test) | high: wrong number under a confident badge on a known orphan; already ticketed. |
| `cortex-error-detail-returned-unmasked` | bypass | new | 1 | demonstrated (1 test) | low: needs an engine error that quotes data. |
| `rule12-aggregate-zero-row-green` | bypass | new | 1 | demonstrated (1 test) | high: CLAUDE.md rule 12 calls this the most dangerous single failure (plausible number, g... |
| `e9-bare-integer-uncited-in-doc-lane` | bypass | new | 1 | demonstrated (1 test) | low-medium: needs a model-composed integer total in prose; deliberate heuristic. |
| `generative-keep-gt-rows-not-in-ledgered-sql` | bypass | new | 1 | demonstrated (1 test) | low-medium: audit mismatch; shown rows are real rows. |
| `space-refusal-envelope-no-served-attribution` | bypass | #305 | 1 | demonstrated (1 test) | low: attribution gap on a refusal. |
| `served-pin-checked-only-in-offline-scorer` | systemic | #337 | 17 | no test written (class systemic, no runtime trigger a request can show) | medium: the gate exists only in the offline harness; no runtime enforcement. |
| `insights-routes-bypass-dms-gates` | systemic | new | 15 | no test written (class systemic, no runtime trigger a request can show) | medium: unknowable from this repo how much Cortex enforces by key; DMS adds nothing. |
| `browse-and-chunk-routes-not-answer-paths` | systemic | new | 14 | no test written (class systemic, no runtime trigger a request can show) | low: usability/honesty of non-answer routes. |
| `certified-lane-failure-not-named` | systemic | new | 8 | no test written (class systemic, no runtime trigger a request can show) | low-medium: source swap without a stated reason; confidence labels remain truthful. |
| `served-sql-never-reverified-against-ontology` | systemic | new | 7 | no test written (class systemic, no runtime trigger a request can show) | medium: design question for PRD (human certification vs ontology authority). |
| `cortex-lake-column-types-unread-by-dms` | systemic | new | 7 | unproven, no in-process test: Needs the real engine lake; no in-process fake models Cortex's column... | low-medium: unknowable here. |
| `read-routes-own-space-check-not-shared-grant` | systemic | new | 6 | no test written (class systemic, no runtime trigger a request can show) | low-medium: documented design vs audit definition; docstring bug is real. |
| `xlsx-orch-outside-governed-path` | systemic | new | 6 | no test written (class systemic, no runtime trigger a request can show) | medium. |
| `free-sql-lanes-no-fanout-guard` | systemic | new | 5 | no test written (class systemic, no runtime trigger a request can show) | medium: inflated figure at the L2 tier. |
| `contract-ask-model-call-unpinned-and-stamped-none` | systemic | new | 5 | no test written (class systemic, no runtime trigger a request can show) | medium: false no-model label on model-written SQL. |
| `drillthrough-token-is-the-only-control` | systemic | new | 5 | no test written (class systemic, no runtime trigger a request can show) | low-medium. |
| `contract-ask-answer-not-submitted-or-ledgered-by-dms` | systemic | new | 3 | no test written (class systemic, no runtime trigger a request can show) | medium. |
| `export-restate-badge-without-attribution` | systemic | new | 3 | no test written (class systemic, no runtime trigger a request can show) | low. |
| `verify-measured-on-dms-file-not-serving-lake` | systemic | new | 2 | no test written (class systemic, no runtime trigger a request can show) | medium. |
| `demo-lane-bypasses-all-gates` | dormant | new | 17 | demonstrated (4 tests) | low: opt-in demo mode. |
| `operator-cli-outside-governed-path` | dormant | new | 9 | unproven, no in-process test: brief.py testable with hand JSON (python-pptx dev extra); insights.py... | low: operator tooling. |
| `answer-user-sql-unwired` | dormant | new | 7 | no test written (class dormant, no runtime trigger a request can show) | low: unreachable until wired; becomes high if a route is added. |
| `demo-fallback-converts-live-refusal-to-demo-numbers` | dormant | new | 4 | demonstrated (5 tests) | low: default off, bannered; contradicts the in-code 'never mask policy refusals' rule. |
| `ui-demo-source-fixture-unreachable` | dormant | new | 3 | unproven, no in-process test: UI only. | low. |
| `ungranted-tick-dropped-not-refused` | dormant | #307 | 2 | demonstrated (2 tests) | low. |
| `cascade-certifies-spelling-not-executed-sql` | dormant | new | 2 | no test written (class dormant, no runtime trigger a request can show) | low: off by default. |
| `demo-answers-exported-and-shared-unlabelled` | dormant | new | 2 | demonstrated (1 test) | low. |
| `exclusion-auto-decline-answers-different-question` | dormant | new | 1 | unproven, no in-process test: UI effect timing; needs vitest. | low-medium. |

### Bypass gaps in detail

**`no-space-ask-widest-grant`** - With no space_id the ask lanes resolve the grant to every DEMO_TABLE (including alerts, which no Space grants) plus every Space's uploads, although the Library path was fixed to company_default_tables (A-0007) and the bronze-sheet lane abstains no_space.

- Cells: chat-ask:verified-query G1, chat-ask:governed-pack G1, chat-ask:harness-generative-miss G1, chat-ask:generative-named-abstain G1, chat-ask:cascade-abstain G1, chat-ask:cascade-attach G1, chat-ask:generative-multi-grain G1, chat-ask:generative-insights-sql G1, chat-ask:generative-ontology-plan G1, chat-ask:cortex-contract-ask G1, ui:exclusion-auto-confirm G1, mcp:ask G1
- Tracked by: new. Severity hint: high: an invariant the project records as closed (A-0007) is still open on the main ask path, and cross-Space uploads are reachable without naming a Space. Bounded by DR-0004 (no principal, so 'no Space' is company-wide by design).
- Repro: Default config (DMS_ASK_MODE=live, DMS_DEMO_FALLBACK=0, Cortex client present). (a) POST /v1/chat/ask {"question":"List all open alerts by severity"} with NO space_id. demo_acl(None) -> grantable_tables(None) = sorted(DEMO_TABLES + every upload) (executor/__init__.py:306-317), so the minted manifest names alerts and an engine honouring it answers AL-1..AL-5 under a normal badge. The same body with space_id=cccccccc-cccc-cccc-cccc-cccccccccccc or dddddddd-dddd-dddd-dddd-dddddddddddd is refused (neither Space grants alerts, demo_grants.py:39-48). (b) POST /v1/studio/ingest a CSV into cccccccc-cccc-cccc-cccc-cccccccccccc (-> bronze.<t>), then POST /v1/chat/ask {"question":"How many rows are there?","grounded_tables":["bronze.<t>"]} with no space_id: grantable_tables(None) includes every Space's uploads, so no GroundingRefused; with space_id=dddddddd-dddd-dddd-dddd-dddddddddddd the same body 403s grounding_not_grantable. Gated sibling: the bronze-sheet lane abstains no_space (executor/__init__.py:696-702) and library warehouse_tables(None) uses company_default_tables() (no alerts). Coherence: checked in code, holds. demo_acl's docstring calls no-Space 'the personal context' (intended), but STATUS.md says A-0007 is closed 'refused under all' - true for the Library only.
- What the user gets (from the test run): With no space_id, the 'List all open alerts by severity' ask is answered 200 L2_VALIDATED with the open alert rows AL-1, AL-2, AL-4 and AL-5 (given an engine that honours the manifest), because the manifest DMS minted for the session names alerts, which no Space grants; in FINANCE or WAREHOUSE_OPS the same ask is an ABSTAIN.
- Test: `tests/gate_matrix/test_gap_grants.py::test_ask_without_a_space_does_not_widen_to_a_table_no_space_grants`

**`audit-receipt-and-late-added-cards-skip-mask`** - audit_receipt (exclude.reasons[].detail, unsure.why) and source cards attached after the build sit outside the masker's walk, so literals the envelope masks elsewhere appear raw in the receipt, workbook, BI stub and share payload.

- Cells: chat-ask:generative-named-abstain G5, chat-ask:cascade-attach G5, chat-ask:cortex-contract-ask G5, chat-ask:cortex-doc-retrieval G5, export:xlsx G5, export:bi G5, ui:share-answer G5
- Tracked by: new. Severity hint: medium: exclude.reasons[].detail is predicate text that routinely carries the user's filter literal; reaches an artefact that leaves the product. Masker recall for names is a separate known gap.
- Repro: Default config, no Cortex needed beyond the compliance gate. POST /v1/chat/export.xlsx {"envelope":{"answer_id":"a1","badge":"L2_VALIDATED","text":"x","sql_used":"SELECT n FROM t WHERE ic_no = '900101-14-5678'","audit_receipt":{"include":{"status":"na","rows":[]},"exclude":{"status":"filters","reasons":[{"kind":"where","detail":"ic_no = '900101-14-5678'"}],"why":"w"},"unsure":{"status":"none","why":"w"}}}}. The Cover row for sql_used holds a DMSMASK token; the Cover row for audit_receipt holds the raw NRIC. Ask-path variant (synthetic): a Cortex abstain whose provenance/exclude_reasons quote the filter literal -> assumptions masked, audit_receipt.unsure.why raw. Coherence: verified - pii.py:201-211 puts audit_receipt in _HANDLED_KEYS (mask_unknown_keys skips it) and mask_envelope (pii.py:1068-1076) only rewrites include.rows.
- What the user gets (from the test run): An ABSTAIN answer shows the filter literal tokenised in the assumptions but in clear in the audit receipt's unsure reason; a CCA encoding source card prints a landed email address in clear while the constraint trace masks it; and the downloaded .xlsx Cover sheet and BI stub carry the raw IC number inside the audit_receipt cell while the sql_used row beside it is a mask token.
- Test: `tests/gate_matrix/test_gap_mask_envelope.py::test_ask_abstain_receipt_why_is_masked_like_assumptions`; `tests/gate_matrix/test_gap_mask_envelope.py::test_cascade_encoding_source_cards_are_masked_like_the_trace`; `tests/gate_matrix/test_gap_mask_envelope.py::test_xlsx_export_masks_the_audit_receipt_like_sql_used`; `tests/gate_matrix/test_gap_mask_envelope.py::test_bi_export_masks_the_audit_receipt_like_sql_used`

**`ui-derives-figures-and-labels-client-side`** - The Check-accuracy and Sources panels compute and label figures client-side (summed percentages, equal-value ties, synthesized MYR/percent contribution cards, a 'Certified answer' headline on any answer lacking cards) that are not in values[], not ontology-defined, not ledgered, and not tied to the badge.

- Cells: ui:check-accuracy G2, ui:source-panel-contribution G2, ui:check-accuracy G7, ui:source-panel-contribution G7, ui:check-accuracy G8, ui:source-panel-contribution G8
- Tracked by: new. Severity hint: medium: confident-looking UI claims ('Certified answer', fabricated RM/percent weights, false Mismatch) with no server provenance; UI-only, figures in the answer itself are unchanged.
- Repro: Default config, UI only. (a) In dddddddd-dddd-dddd-dddd-dddddddddddd ask the pack question 'Show warehouse capacity utilisation' (rows {location_code, pct_used}); click 'Check accuracy': answerDelivery.ts:83-89,111-134 sums the percentages and prints 'Match - N grouped values sum to X (= row sum)'. Tie case: 'Show shipment cost by destination' with two equal total_cost_myr - envelope.py:1296-1303 dedupes equal cells so values[] has one figure and the check prints a red 'Mismatch - row sum 2X vs stated X' over a correct, ledgered answer. (b) In cccccccc-cccc-cccc-cccc-cccccccccccc ask 'What is our total spend by supplier country?' (pack; grounded_tables inventory+suppliers, no contributing_sources): sourcePanel.ts:19-33 synthesizes two cards with contribution 1/n and row_count = answer rows, rendered 'N rows - RM 0.50 - 50.0%'; a refusal or ABSTAIN with no cards gets the headline 'Certified answer, no file card from Cortex. Open SQL.' (sourcePanel.ts:44 ignores badge). Coherence: answerDelivery.ts and sourcePanel.ts read; claims hold. Other-finding: the check is self-referential (values[] is harvested from the same rows), so a Match cannot detect fan-out.

**`xlsx-orch-coercion-and-false-ok`** - xlsx-orch reads untyped workbook cells with per-request coercion (text OnTime/cost silently dropped, 0.0 treated as missing, nearest-number fishing) and returns ok:true or a 500 instead of a named refusal.

- Cells: studio:xlsx-orch-crosscheck G4, studio:xlsx-orch-extract G4, studio:xlsx-orch-golden G4, studio:xlsx-orch-crosscheck G8, studio:xlsx-orch-extract G8, studio:xlsx-orch-golden G8
- Tracked by: new. Severity hint: medium: an oracle whose job is to catch Copilot errors answers ok:true on unreadable input; external-caller tool, not the customer answer path.
- Repro: Default config, compliance gate allowing (stub or fake Cortex). POST /v1/studio/xlsx-orch/crosscheck with a valid pack and workbook_path = a workbook under the warehouse parent dir (DMS_WAREHOUSE_DB in tmp) whose OnTime column holds 'On Time'/'Late' and cost holds 'RM 300.27': _truthy('On Time') is False and _as_float('RM 300.27') is None, every row is dropped, and the response is HTTP 200 {ok:true,status:'awaiting_pointer_receipt',reason:'live',source_oracle:null} with no named reason (core xlsx_orch.py:98-116,224-230; executor xlsx_orch.py:69-87). Golden variant: stored artifact whose Analysis sheet has ['Average Cost', 0] plus any other numeric cell equal to 300.27 -> ok:true reason frtr_golden (0.0 falsy, `or _nearest`, core :411-448) - needs a ~184005-row Export sheet unless the constants are patched. extract: unreadable/locked file or read-only space_docs -> bare 500. Coherence: tracer scenarios consistent with cited code; crosscheck is the cheap one.
- What the user gets (from the test run): POST /v1/studio/xlsx-orch/crosscheck on a workbook whose OnTime cells say 'On Time'/'Late' and whose cost cells say 'RM 300.27' gets HTTP 200 {ok:true, reason:'live', source_oracle:null} with no named refusal. POST /v1/studio/xlsx-orch/golden on a workbook whose labeled Average Cost is 0 gets ok:true / frtr_golden with avg_cost 300.27 read from an unrelated chart cell.
- Test: `tests/gate_matrix/test_gap_xlsx_orch.py::test_crosscheck_text_flags_and_costs_do_not_answer_ok_without_a_named_reason`; `tests/gate_matrix/test_gap_xlsx_orch.py::test_golden_zero_average_is_not_replaced_by_the_nearest_cell`

**`preview-and-drill-rows-unmasked`** - Library warehouse/bronze preview, the MCP preview twin and /v1/chat/drillthrough return raw cell values; the masker is wired only into live_ask / build_answer_envelope.

- Cells: library:warehouse-preview G5, library:bronze-preview G5, mcp:preview G5, chat:drillthrough G5, export:csv-client G5
- Tracked by: new. Severity hint: high: unauthenticated GETs (DR-0004 Option A) return the most likely place for personal data raw; PRD must decide whether previews are exempt.
- Repro: Default config, synthetic seed. POST /v1/studio/ingest (gate allowed) multipart file people.csv 'name,email,ic_no\nSiti,siti@example.com,900101-14-5678' with space_id=cccccccc-cccc-cccc-cccc-cccccccccccc -> table bronze.people. GET /v1/library/bronze/bronze.people/preview?space_id=cccccccc-cccc-cccc-cccc-cccccccccccc returns the raw email and IC in rows (library.py:321-327 returns bronze_preview() output; no mask call in any route file). The same table through POST /v1/chat/ask grounded_tables=['bronze.people'] has rows masked by mask_unknown_keys (executor/__init__.py:508-510). Drillthrough: chat.py:380-381 returns resp.model_dump(mode='json') unmasked; the UI Download CSV writes those rows. Coherence: grep finds no 'mask' in routes/library.py, studio.py, mcp.py, insights.py; holds.
- What the user gets (from the test run): A steward or MCP client calling the Library bronze preview, the warehouse preview, the MCP preview tool, or POST /v1/chat/drillthrough receives the stored email and NRIC verbatim (and the UI's Download CSV writes the drill rows), although the same values asked through /v1/chat/ask come back as DMSMASK_ tokens.
- Test: `tests/gate_matrix/test_gap_mask_surfaces.py::test_library_and_mcp_previews_return_personal_values_raw`; `tests/gate_matrix/test_gap_mask_surfaces.py::test_drillthrough_rows_come_back_unmasked`

**`followup-no-submit`** - Follow-up lanes ('average of them', 'add N') compute figures in local DuckDB from literals and return L2_VALIDATED with a made-up audit_id, never calling submit or the ledger.

- Cells: chat-ask:followup-average G7, chat-ask:followup-add G7, cache:session-turns G7, mcp:ask G7
- Tracked by: FOLLOWUP-CONTRACT-01. Severity hint: high: a green L2 figure whose audit_id resolves to nothing in the ledger; already ruled a gap (hard rule 3).
- Repro: Default config. POST /v1/chat/ask {"question":"What is our total spend by supplier country?","space_id":"cccccccc-cccc-cccc-cccc-cccccccccccc","session_id":"s1"} (pack lane: Cortex submit + ledger append), then {"question":"average of them","space_id":"cccccccc-cccc-cccc-cccc-cccccccccccc","session_id":"s1"}. Turn 2 returns 200 L2_VALIDATED, text 'Average of the prior N figures is RM x', audit_id 'ans_followup', sql_used 'SELECT ROUND((a + b)/N.0, 2) AS average_myr' executed by local DuckDB; the fake Cortex records no new submit and no new ledger append. A gated path would submit the SQL under the signed manifest and ledger it (or abstain). Coherence: verified - maybe_followup returns at executor/__init__.py:578-590 before any _submit_verified_sql/_ledger_verified_query.
- What the user gets (from the test run): The customer gets a green L2_VALIDATED follow-up figure whose audit_id 'ans_followup' resolves to no ledger entry, and Cortex never saw the follow-up SQL (the same holds for 'add 2000' and for the MCP ask tool).
- Test: `tests/gate_matrix/test_gap_followup.py::test_followup_figure_goes_through_cortex_submit_and_the_ledger[chat-average]`; `tests/gate_matrix/test_gap_followup.py::test_followup_figure_goes_through_cortex_submit_and_the_ledger[chat-add]`; `tests/gate_matrix/test_gap_followup.py::test_followup_figure_goes_through_cortex_submit_and_the_ledger[mcp-average]`

**`followup-no-grant-recheck`** - The follow-up lane reads the per-(session_id, space_id) turn cache with no shared grant check, no no_space abstain and no re-read of grants.

- Cells: chat-ask:followup-average G1, chat-ask:followup-add G1, chat-ask:followup-abstain G1, cache:session-turns G1
- Tracked by: FOLLOWUP-CONTRACT-01. Severity hint: medium: partition by (session, Space) holds; the gap is relative to the ruling and to the no_space precedent.
- Repro: Default config. POST /v1/chat/ask {question:'What is our total spend?', session_id:'s2'} with NO space_id (pack lane answers under the company default), then {question:'add 2000', session_id:'s2'} no space_id: 200 'Prior figure plus 2000 is RM ...' L2_VALIDATED under cache key ('s2',''), no grantable_tables call. The bronze lane in the same ladder abstains no_space for the same request shape. Tracer variant (grants revoked between turns; same session+Space still averages) is contrived for the seeded store and is downgraded to a secondary assertion. Coherence: session_followup.py:51-55 keys on caller strings only; executor/__init__.py:578-590 returns before grants; cross-Space reuse is correctly blocked by the key, so no cross-Space leak is shown.
- What the user gets (from the test run): A follow-up with no Space, or after the Space lost the grant to the upload the earlier figures came from, still returns a green L2_VALIDATED computed figure instead of a named abstain (no_space / ungranted_table).
- Test: `tests/gate_matrix/test_gap_followup.py::test_followup_without_a_space_abstains_no_space`; `tests/gate_matrix/test_gap_followup.py::test_followup_after_the_grant_is_revoked_abstains_ungranted_table`

**`ledger-append-carries-unmasked-sql`** - The ledger append for verified-query and generative answers carries the raw SQL text (filter literals included) while the envelope's sql_used is masked.

- Cells: chat-ask:verified-query G5, chat-ask:generative-insights-sql G5, chat-ask:generative-ontology-plan G5, mcp:ask G5
- Tracked by: new. Severity hint: medium: an append-only ledger cannot be erased, but exposure needs a PII literal inside the SQL.
- Repro: Default config, VQ variant. POST /v1/studio/verified-queries {space_id:cccccccc-cccc-cccc-cccc-cccccccccccc, question:'Payments for IC 900101-14-5555', sql:"SELECT txn_id FROM transactions WHERE txn_id = '900101-14-5555'"}; then POST /v1/chat/ask {question:'Payments for IC 900101-14-5555', space_id:cccccccc-cccc-cccc-cccc-cccccccccccc}. envelope.sql_used shows a DMSMASK token; fake Cortex ledger_append payload['sql'] contains the raw literal (executor/__init__.py:455-478 payload sql=asset_sql; generative_ask.py:845 ledger_append({'sql': sql,...}) shared by multi-grain :985, insights-sql :1284, ontology-plan :1454). Coherence: verified in code; steward/model must place a PII literal in the SQL.
- What the user gets (from the test run): The customer's answer shows sql_used with the IC-shaped literal replaced by a mask token, but the append-only Cortex ledger entry for that same answer holds the SQL with the literal in clear, so the audit record exposes what the answer hid and cannot be erased.
- Test: `tests/gate_matrix/test_gap_mask_envelope.py::test_verified_query_ledger_payload_masks_sql_like_the_envelope`; `tests/gate_matrix/test_gap_mask_envelope.py::test_mcp_ask_ledger_payload_masks_sql_like_the_envelope`; `tests/gate_matrix/test_gap_mask_envelope.py::test_generative_insights_sql_ledger_payload_masks_sql_like_the_envelope`

**`export-projections-no-provenance-tie`** - Export/BI/CSV/share outputs restate whatever envelope they are handed (shape-only check) with no tie to a server-issued, ledgered answer, so forged figures and badges are exported and follow-up/bronze/demo figures export without a marker.

- Cells: export:xlsx G7, export:bi G7, export:csv-client G7, ui:share-answer G7
- Tracked by: new. Severity hint: medium: caller can mint a workbook claiming L0 without any ask; there is no principal (DR-0004) so forging is self-service, but the artefact leaves the product.
- Repro: Default config, no prior ask. POST /v1/chat/export.xlsx {"envelope":{"answer_id":"x","badge":"L0_CERTIFIED","values":[{"id":"v1","value":9999999,"unit":"MYR","label":"Revenue"}],"rows":[{"region":"KL","revenue":9999999}]}} -> 200 workbook whose Cover reads badge L0_CERTIFIED and whose Values/Rows sheets hold the invented figure. POST /v1/chat/export.bi with badge L1_GOVERNED_METRIC returns the same in a Power Query stub. Space seed is incidental. Coherence: verified - refuse_envelope_export (xlsx_export.py:117-136) checks only answer_id non-empty, badge in ALLOWED_BADGES and row/value shapes; the route docstring says 'real ask envelope'. CSV/share legs are UI projections (vitest only).
- What the user gets (from the test run): A caller who never asked anything posts an invented envelope (badge L0_CERTIFIED, Revenue 9999999 MYR) and gets back a 200 Excel workbook whose Cover claims L0_CERTIFIED and whose Values/Rows sheets hold the invented figure, and a 200 Power Query stub embedding the same row, with zero Cortex calls.
- Test: `tests/gate_matrix/test_gap_exports.py::test_export_routes_restate_an_answer_this_app_never_issued`

**`followup-unit-relabelled-rm`** - Follow-up arithmetic hard-codes 'RM'/average_myr and ignores each prior figure's unit and measure, so percentages, counts and row_count placeholders are averaged or added and labelled ringgit under L2_VALIDATED.

- Cells: chat-ask:followup-average G2, chat-ask:followup-add G2, cache:session-turns G2
- Tracked by: new. Severity hint: medium: wrong unit on a green figure; arithmetic is on figures already shown.
- Repro: Default config. In dddddddd-dddd-dddd-dddd-dddddddddddd POST /v1/chat/ask {question:'Show warehouse capacity utilisation', session_id:'s3'} (pack; values are pct_used per location), then {question:'average of them', session_id:'s3', space_id:'dddddddd-dddd-dddd-dddd-dddddddddddd'}: 200 'Average of the prior N figures is RM x' L2_VALIDATED - a mean of percentages (distinct values only, envelope.py:1294-1301) labelled ringgit. Simpler: any text-only/doc answer stores the v_count=row_count placeholder (envelope.py:1313-1314); 'add 5' returns 'Prior figure plus 5 is RM 5.00'. Coherence: verified - session_followup.py:220,254 hard-code 'RM'; numeric_values (session_followup.py:39-48) drops unit and label.
- What the user gets (from the test run): The mean of five warehouse capacity percentages is shown as 'RM 69.72' (value label average_myr), and a one-row location listing plus 5 is shown as 'RM 6.00', both as green L2_VALIDATED figures.
- Test: `tests/gate_matrix/test_gap_followup.py::test_followup_does_not_label_a_non_ringgit_figure_as_ringgit[percent-average]`; `tests/gate_matrix/test_gap_followup.py::test_followup_does_not_label_a_non_ringgit_figure_as_ringgit[count-add]`

**`followup-stale-prior-after-failed-turn`** - A turn that fails by exception (Space refusal, timeout) or by a blocked cascade neither stores nor clears the cached prior, so a later follow-up silently computes over an older turn's figures; the cache is also unbounded.

- Cells: chat-ask:followup-average G8, chat-ask:followup-add G8, cache:session-turns G8
- Tracked by: new. Severity hint: low: stale but real earlier figures; mislabelled recency rather than an invented number.
- Repro: Default config. Session s4 in dddddddd-dddd-dddd-dddd-dddddddddddd: turn 1 pack ask 'Show warehouse capacity utilisation' (stored under ('s4','dddddddd-dddd-dddd-dddd-dddddddddddd')); turn 2 'Top 5 selling SKUs by revenue' (needs transactions, not granted) -> AskServiceError path_not_allowed -> route-built Space-refusal envelope (chat.py:317-325), never stored and not popped; turn 3 'average of them' -> 200 L2_VALIDATED computed from turn 1. Coherence: _store_turn (executor/__init__.py:240-247) runs only on returned envelopes; exceptions bypass it, chat.py does not touch executor._turns. Holds.
- What the user gets (from the test run): After a refused (or timed-out) question, 'average of them' silently returns a green L2_VALIDATED average of the older turn's figures, as if it were about the question that just failed.
- Test: `tests/gate_matrix/test_gap_followup.py::test_followup_after_a_failed_turn_does_not_reuse_the_older_figures[space-refusal]`; `tests/gate_matrix/test_gap_followup.py::test_followup_after_a_failed_turn_does_not_reuse_the_older_figures[timeout]`

**`bronze-sheet-trycast-drops-rows-silently`** - The bronze-sheet lane sums TRY_CAST(measure AS DOUBLE) over sniffed-VARCHAR columns and drops unparseable cells without a count, so a text-formatted cell understates the total under L0_CERTIFIED and sql_used omits the predicate that dropped it.

- Cells: chat-ask:bronze-sheet G4, chat-ask:cascade-attach G4, mcp:ask G4
- Tracked by: new. Severity hint: high: understated total with a green L0_CERTIFIED badge is the plausible-number-plus-green-badge failure CLAUDE.md rule 12 names.
- Repro: Default config, synthetic. Ingest granted.xlsx sheet Sales (category, sales_value_myr) into cccccccc-cccc-cccc-cccc-cccccccccccc as in tests/test_bronze_sheet_ask.py, with one sales_value_myr cell 'RM 1200' so the column lands VARCHAR; POST /v1/chat/ask {question:'In granted.xlsx sheet Sales, what are the top 3 categories by sales_value_myr?', space_id:'cccccccc-cccc-cccc-cccc-cccccccccccc'}. 200 L0_CERTIFIED 'Found 3 row(s)' with that category's total understated; the executed SQL has WHERE TRY_CAST(...) IS NOT NULL (bronze_sheet_ask.py:191-205) but sql_used does not (:219-222). Coherence: verified in code; the generic assumption wording ('hanging/blank') does not describe a non-blank 'RM 1200'.
- What the user gets (from the test run): POST /v1/chat/ask on granted.xlsx / Sales with one text cell 'RM 1200' returns 200 L0_CERTIFIED with Electronics 1800.0 instead of 3000.0, and the only note is the generic 'hanging/blank measure rows dropped' with no count; the audit receipt even says nothing was excluded.
- Test: `tests/gate_matrix/test_gap_lanes.py::test_bronze_sheet_sum_does_not_silently_drop_an_unparseable_cell`

**`insights-routes-return-payload-unmasked`** - POST /v1/insights and GET /v1/insights/ontology pass Cortex's payload (values, answer, SQL, error bodies, the user's question in the URL) through unmasked.

- Cells: insights:post-ask G5, insights:post-generate G5, insights:get-ontology-ranking G5
- Tracked by: new. Severity hint: medium: depends on Cortex returning personal data, but DMS adds no control.
- Repro: Default config with a fake Cortex whose insights_ask returns {status:'CERTIFIED', values:[{email:'siti@example.com'}], answer:'...'}. POST /v1/insights {"intent":"list customer emails","space_id":"cccccccc-cccc-cccc-cccc-cccccccccccc"} -> 200 with the raw address (routes/insights.py:183-193 returns honest_envelope(payload); no mask import in the file). The same values through POST /v1/chat/ask pass mask_unknown_keys. Coherence: holds; the real payload depends on Cortex.
- What the user gets (from the test run): A caller of POST /v1/insights (ask or generate) or GET /v1/insights/ontology gets Cortex's values and answer text back with the raw email and NRIC intact, where the ask path would have returned DMSMASK_ tokens.
- Test: `tests/gate_matrix/test_gap_mask_surfaces.py::test_insights_routes_return_cortex_payload_unmasked`

**`xlsx-orch-cross-space-path-read`** - xlsx-orch routes accept any space_id and any xlsx path under the warehouse parent directory (which holds every Space's space_docs), so one Space's stored workbook can be read, graded or copied into another Space.

- Cells: studio:xlsx-orch-crosscheck G1, studio:xlsx-orch-extract G1, studio:xlsx-orch-golden G1
- Tracked by: new. Severity hint: medium: any caller of a gated mutation route can read any xlsx under the data dir; no principal exists.
- Repro: Default config, DMS_WAREHOUSE_DB in a tmp dir so the allowlist root is that dir, gate allowing. POST /v1/studio/xlsx-orch/extract {pack_id:'p1', space_id:'cccccccc-cccc-cccc-cccc-cccccccccccc', producer:'pointer_copilot', result_path:<tmp>/fin.xlsx} stores <tmp>/space_docs/cccccccc-cccc-cccc-cccc-cccccccccccc/xlsx_orch/p1/result.xlsx. Then POST extract {pack_id:'p9', space_id:'dddddddd-dddd-dddd-dddd-dddddddddddd', producer:'pointer_copilot', result_path:<that FIN path>} -> ok:true; the FIN workbook is copied into dddddddd-dddd-dddd-dddd-dddddddddddd's space_docs and its Analysis figures returned. golden/crosscheck read the same paths. Coherence: reveal.py:25-45 roots = warehouse parent; xlsx_orch.py:104-126 has no Space ownership test. Holds; under DR-0004 any caller can name any space_id anyway.
- What the user gets (from the test run): A request addressed to WAREHOUSE_OPS that names FINANCE's stored workbook gets HTTP 200 ok:true: the file is copied byte-for-byte into WAREHOUSE_OPS's space_docs and FINANCE's Analysis labels and figures are returned; the golden route likewise grades it and returns FINANCE's labels.
- Test: `tests/gate_matrix/test_gap_xlsx_orch.py::test_one_space_cannot_read_or_copy_another_spaces_stored_workbook`

**`xlsx-orch-unmasked-and-raw-persist`** - xlsx-orch responses return workbook headers/labels/values unmasked, and extract persists the raw workbook under space_docs.

- Cells: studio:xlsx-orch-crosscheck G5, studio:xlsx-orch-extract G5, studio:xlsx-orch-golden G5
- Tracked by: new. Severity hint: low-medium: aggregates and labels in an external-caller tool; raw persistence is by design.
- Repro: Default config. POST /v1/studio/xlsx-orch/extract {pack_id:'p5', space_id:'cccccccc-cccc-cccc-cccc-cccccccccccc', producer:'pointer_copilot', result_path:<tmp xlsx>} where Analysis has ['IC 900101-14-5566', 4521.50]: response inspected.analysis_labeled carries the raw label; crosscheck returns source_oracle.cost_col verbatim from the header 'Cost - IC 900101-14-5566'. Coherence: nothing in studio.py or executor/xlsx_orch.py calls a masker; holds (names are not detected by the masker today, so use an IC/email in the test).
- What the user gets (from the test run): The extract and golden responses return the Analysis label 'ic 900101-14-5566' verbatim in inspected.analysis_labeled, and the crosscheck response returns the header 'Spend IC 900101-14-5566' verbatim as source_oracle.cost_col, with no masking.
- Test: `tests/gate_matrix/test_gap_xlsx_orch.py::test_workbook_labels_and_headers_are_masked_in_the_response`

**`stored-sql-no-fanout-guard`** - Steward-registered (VQ) and hard-coded pack SQL is served at L0/L1 with no join-rule or fan-out check at registration or serve time.

- Cells: chat-ask:verified-query G3, chat-ask:governed-pack G3
- Tracked by: new. Severity hint: medium: requires a steward-authored fan-out join, but the figure ships with the top trust badge.
- Repro: Default config. POST /v1/studio/verified-queries {space_id:'cccccccc-cccc-cccc-cccc-cccccccccccc', question:'Revenue by warehouse', sql:'SELECT t.location_id, ROUND(SUM(t.quantity_kg*t.unit_cost_myr),2) AS revenue_myr FROM transactions t JOIN inventory i ON i.location_id = t.location_id GROUP BY t.location_id'} is accepted (verified_queries.py:125-175 checks hostile SQL and grants only). POST /v1/chat/ask {question:'Revenue by warehouse', space_id:'cccccccc-cccc-cccc-cccc-cccccccccccc'} -> L0_CERTIFIED; WH-A and WH-B totals are doubled because the seed inventory has two rows for each (demo_warehouse.py:350-358). Coherence: holds; needs a steward-authored bad join and a DuckDB-backed fake submit so the rows are real.
- What the user gets (from the test run): A steward-registered transactions-JOIN-inventory VQ is accepted with 200, and 'Revenue by warehouse' then returns 200 L0_CERTIFIED with WH-A 14410.0 and WH-B 36365.0, exactly double the true 7205.0 and 18182.5.
- Test: `tests/gate_matrix/test_gap_lanes.py::test_steward_registered_fanout_join_is_not_served_as_a_certified_figure`

**`bronze-sheet-l0-minted-locally`** - The bronze-sheet lane computes rows in DMS's own DuckDB and ships L0_CERTIFIED with audit_id=answer_id, with no signed manifest, submit or ledger append.

- Cells: chat-ask:bronze-sheet G7, chat-ask:cascade-attach G7
- Tracked by: new. Severity hint: high: top trust tier minted locally; breaks hard rule 3 (one ledger) and F83.
- Repro: Default config. Ingest granted.xlsx / Sales into cccccccc-cccc-cccc-cccc-cccccccccccc (tests/test_bronze_sheet_ask.py), POST /v1/chat/ask {question:'In granted.xlsx sheet Sales, what are the top 3 categories by sales_value_myr?', space_id:'cccccccc-cccc-cccc-cccc-cccccccccccc'}: 200 L0_CERTIFIED with rows from bronze_sheet_ask._grouped_top_n (duckdb.connect read_only), audit_id 'ans_bronze_<ident>'; fake Cortex submits and ledger appends stay empty. Coherence: verified (bronze_sheet_ask.py:178-206, executor/__init__.py:693-727).
- What the user gets (from the test run): A clean granted.xlsx / Sales top-3 question returns 200 L0_CERTIFIED, the top trust badge, with audit_id 'ans_bronze_granted_Sales' that points at nothing in the ledger, because DMS computed the rows in its own DuckDB with no Cortex submit and no ledger append.
- Test: `tests/gate_matrix/test_gap_lanes.py::test_bronze_sheet_l0_is_certified_through_a_cortex_submit_and_ledger_entry`

**`chunk-text-routes-unmasked`** - GET /v1/library/chunks/search and GET /v1/studio/chunks return raw chunk text (free text where emails, phones, passports live) with no masker.

- Cells: library:chunks-search G5, studio:chunks-list G5
- Tracked by: new. Severity hint: medium: free-text PII exposure, but only with Postgres configured.
- Repro: Needs DATABASE_URL (default None -> wiring.py:64-65,83-84 return []). Ingest a note into cccccccc-cccc-cccc-cccc-cccccccccccc containing 'Contact siti@example.my 012-3456789 passport A1234567'; GET /v1/library/chunks/search?space_id=cccccccc-cccc-cccc-cccc-cccccccccccc&q=contact or GET /v1/studio/chunks?space_id=cccccccc-cccc-cccc-cccc-cccccccccccc returns it verbatim in content. Coherence: holds in code; not reproducible on default config.

**`drillthrough-failure-and-truncation-not-named`** - A Cortex 422 on drillthrough becomes an empty 200, the model drops truncation/approximate fields, and a refused or failed drill makes the CSV button silently download the one-cell summary.

- Cells: chat:drillthrough G8, export:csv-client G8
- Tracked by: new. Severity hint: low-medium: a failed drill reads as 'no rows' and a capped drill can export as complete.
- Repro: Default config. A Cortex drillthrough answering 422: client.py:268-275 model_validates parsed.to_dict() into an all-default DrillthroughResponse, so POST /v1/chat/drillthrough {token:'t'} returns HTTP 200 {rows:[],sql_used:null} and the UI prints 'No drill rows returned.'. If the gate refuses (403) or Cortex 502s, AnswerMessage.tsx:195-197 falls back and 'Download CSV' saves the summary cell. Coherence: holds for the 422 path; CSV leg is UI-only.
- What the user gets (from the test run): A Cortex 422 on drillthrough comes back as HTTP 200 with rows [] and null sql_used (the UI prints 'No drill rows returned.'), and a capped/approximate drill (contract fields approximate=true, total_count=84201) comes back with those fields dropped so it reads as the complete detail.
- Test: `tests/gate_matrix/test_gap_mask_surfaces.py::test_drillthrough_cortex_422_is_an_empty_success_not_a_named_failure`; `tests/gate_matrix/test_gap_mask_surfaces.py::test_drillthrough_drops_the_cap_and_approximate_flags`

**`generative-sql-ships-over-failed-verify-ontology`** - Insights-written SQL ships L2_VALIDATED over a lake whose default ontology failed Ontology.verify because live_ask passes no declared ontology, so the A2-02 guard is not wired on the live path.

- Cells: chat-ask:generative-insights-sql G2
- Tracked by: #258. Severity hint: high: wrong number under a confident badge on a known orphan; already ticketed.
- Repro: Armed config. Seed the DMS warehouse then add an inventory row with supplier_id 'SUP-99' (orphan FK) so demo_ontology fails verify; load_verified_ontology returns None (generative_ask.py:369-372). In cccccccc-cccc-cccc-cccc-cccccccccccc POST /v1/chat/ask {question:'revenue by supplier'} with a fake Insights returning SQL joining transactions->inventory->suppliers with SUM: validate_compiled_sql passes (all granted), submit and ledger succeed, 200 L2_VALIDATED with the orphan's value silently dropped by the INNER JOIN. Coherence: tests/test_a2_02 shows the guard works when a declared ontology is passed; maybe_generative_ask is called without one at executor/__init__.py:731-762. Holds.
- What the user gets (from the test run): A confident L2_VALIDATED answer, 'Found 4 row(s)' with per-supplier revenue totalling 25,654.5, when the lake's outbound value is 30,654.5 because the orphan-supplier lot was silently dropped by the INNER JOIN, instead of an ABSTAIN naming ontology_unverified / fk_intact on lot_from_supplier.
- Test: `tests/gate_matrix/test_gap_generative_doc.py::test_insights_sql_over_a_lake_whose_ontology_failed_verify_abstains`

**`cortex-error-detail-returned-unmasked`** - Engine error text raised as AskServiceError is returned verbatim as HTTP detail.message with no masker.

- Cells: ui:exclusion-auto-confirm G5
- Tracked by: new. Severity hint: low: needs an engine error that quotes data.
- Repro: Default config. FakeCortex.ask_error = AskServiceError('submit_failed', "cast failed for value 'siti@example.com'"); POST /v1/chat/ask {question:'Top 5 selling SKUs by revenue', space_id:'cccccccc-cccc-cccc-cccc-cccccccccccc'} -> HTTP 502 whose detail.message holds the raw address (chat.py:332-335). Coherence: holds.
- What the user gets (from the test run): When the engine fails on a cell, the chat shows an HTTP 502 whose detail.message contains the engine's text with the raw email address in clear, while the same address inside an answer is masked.
- Test: `tests/gate_matrix/test_gap_mask_envelope.py::test_engine_error_detail_is_masked_like_an_answer`

**`rule12-aggregate-zero-row-green`** - The hard-rule-12 net demotes only an empty result set; a Cortex ask whose zero-match filter returns one aggregate row (COUNT(*)=0, SUM NULL) keeps a confident badge.

- Cells: chat-ask:cortex-contract-ask G8
- Tracked by: new. Severity hint: high: CLAUDE.md rule 12 calls this the most dangerous single failure (plausible number, green badge).
- Repro: Default config. dddddddd-dddd-dddd-dddd-dddddddddddd POST /v1/chat/ask {question:'total quantity of BETA in stock', space_id:'dddddddd-dddd-dddd-dddd-dddddddddddd'} (generative lane misses on the default key); fake Cortex ask returns sql_used "SELECT SUM(quantity_kg) AS total FROM inventory WHERE sku = 'BETA'" and rows [{total: null}] (the seed holds 'SKU-BETA'). rows is non-empty, so envelope.py:1450-1460 does not fire; map_ask_response adds a v_count=1 row_count value; the envelope ships L2_VALIDATED. Coherence: net code verified; the final text depends on the engine answer, so confidence is medium.
- What the user gets (from the test run): In Warehouse Ops, 'total quantity of BETA in stock' (the seed stores SKU-BETA) returns 200 L2_VALIDATED, not abstained, with one row whose total is null and a synthetic row_count value of 1, instead of the 'No matching rows' abstention an empty result gets.
- Test: `tests/gate_matrix/test_gap_lanes.py::test_zero_match_filter_that_returns_one_null_aggregate_row_is_not_a_confident_answer`

**`e9-bare-integer-uncited-in-doc-lane`** - E9/E4 treat only decimal or thousand-separated literals as figures, so an uncited bare integer in a doc-retrieval answer keeps its badge, and an empty-answer abstain renders as a bare 'Abstained.'.

- Cells: chat-ask:cortex-doc-retrieval G8
- Tracked by: new. Severity hint: low-medium: needs a model-composed integer total in prose; deliberate heuristic.
- Repro: Default config. Fake Cortex ask returns answer 'The total is 12500', no sql_used, a doc route with one source snippet that does not contain 12500: _money_like skips bare integers, unbacked_numbers finds nothing, the badge is kept. No matching chunk -> text 'Abstained.' with no reason. Coherence: _money_like verified.
- What the user gets (from the test run): A doc-retrieval answer 'The total is 12500' under an L2_VALIDATED badge with its source card attached, although no query ran and the cited snippet does not contain 12500; separately, a no-hit doc ask with an empty engine answer renders only 'Abstained.' with 'live Cortex ask' as the sole assumption.
- Test: `tests/gate_matrix/test_gap_generative_doc.py::test_doc_answer_with_an_uncited_bare_integer_total_is_demoted`

**`generative-keep-gt-rows-not-in-ledgered-sql`** - On keep_gt plans DMS keeps rows in Python after Cortex executes, so displayed rows are a subset of the ledgered, displayed SQL's result.

- Cells: chat-ask:generative-ontology-plan G7
- Tracked by: new. Severity hint: low-medium: audit mismatch; shown rows are real rows.
- Repro: Armed config. A typed plan with keep_gt (existing fixtures: tests/test_gen_path_climb03.py, 04, test_gen02_bind_fallback.py): Cortex returns all rows, DMS keeps those over the threshold (generative_ask.py:830-842), and sql_used plus the ledger payload record the unfiltered query. Re-running sql_used returns more rows than were shown. Coherence: verified in code.
- What the user gets (from the test run): The answer says 'Found 3 row(s)' (WH-E 97.8, WH-C 96.7, WH-A 72.0), but the sql_used shown with it and the SQL appended to the ledger both return 5 rows (including WH-B 56.3 and WH-D 25.8), so an auditor re-running the cited query cannot reproduce what the customer saw.
- Test: `tests/gate_matrix/test_gap_generative_doc.py::test_keep_gt_rows_shown_are_what_the_displayed_and_ledgered_sql_returns`

**`space-refusal-envelope-no-served-attribution`** - The Space-refusal ABSTAIN envelope is built in routes/chat.py outside live_ask, so it carries no served_attribution although Insights or Cortex generation may have run before the refusal.

- Cells: chat-ask:space-refusal G6
- Tracked by: #305. Severity hint: low: attribution gap on a refusal.
- Repro: Default config. FakeCortex.ask_error = AskServiceError('path_not_allowed', "table 'transactions' is not named by this manifest"); POST /v1/chat/ask {question:'Top 5 selling SKUs by revenue', space_id:'dddddddd-dddd-dddd-dddd-dddddddddddd'} -> refusal envelope (chat.py:317-325) with no served_attribution key. Coherence: verified (executor/__init__.py:504-510 stamps only live_ask returns).
- What the user gets (from the test run): A 200 Space-refusal ABSTAIN envelope with no served_attribution key (and no generate_legs), although the Cortex ask (a model call) had already been attempted before the refusal; every other ask envelope says reported/missing/none.
- Test: `tests/gate_matrix/test_gap_generative_doc.py::test_space_refusal_envelope_carries_served_attribution`

## Entry points, cell by cell

Each line: gate, status, the first two citations (`path:line`), and the gap it belongs to. Full evidence, reasoning and bypass scenarios for every cell are in `gate-matrix-trace.json`.

<a id="ep-chat-ask-followup-average"></a>
### `chat-ask:followup-average`

Session follow-up: 'average of them'. Producer: packages/executor/dms_executor/session_followup.py:172 maybe_followup (average branch :191-225) -> _followup_execute :122 (execute_sql on local DuckDB :138) -> _pack_compute :89; first rung of the la.... Reachable: yes, on default config. POST /v1/chat/ask (apps/api/dms_api/routes/chat.py:214) with DMS_ASK_MODE=live by default (apps/api/dms_api/settings.py:40), demo fallback off (settings.py:43), and the Cortex client built in lif...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/__init__.py:578-590 - the follow-up branch returns before any grantable_tables / intersect_space_grants / table_is_gra...`<br>`packages/executor/dms_executor/session_followup.py:51-55 - the only restriction is the cache key (session_id, space_id), both caller-supplied strings...` | `followup-no-grant-recheck` |
| G2 ontology verification | **NO** (medium) | `packages/executor/dms_executor/session_followup.py:198-199 - the served figure is ROUND((a+b+...)/n,2) AS average_myr built from raw floats; the modu...`<br>`packages/executor/dms_executor/session_followup.py:58-64 - snapshot_turn keeps only float values and sql_used; labels, measure names, lane and badge ...` | `followup-unit-relabelled-rm` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/session_followup.py:198-199 - the only SQL is an f-string of floats with no FROM or JOIN`<br>`packages/executor/dms_executor/session_followup.py:22 - the question must match ^average of them$, so no question text can enter SQL` |  |
| G4 typed ingest | n/a (medium) | `packages/executor/dms_executor/session_followup.py:198-199 - the statement names no table`<br>`packages/executor/dms_executor/demo_warehouse.py:464-500 - execute_sql runs that literal statement; connect_readonly (:455-460) only opens the file` |  |
| G5 PII mask (envelope + record) | yes (medium) | `packages/executor/dms_executor/session_followup.py:99-116 - the envelope is built only through build_answer_envelope`<br>`packages/executor/dms_executor/envelope.py:1626-1637 - build_answer_envelope runs fail_closed_mask_payload over text, rows, values, sources, chart an...` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/session_followup.py:15-20 + __init__.py:578-590 - no model or Cortex client call anywhere on the lane, so (a) does not...`<br>`packages/executor/dms_executor/__init__.py:504-506 + packages/executor/dms_executor/generative_ask.py:228-229,258-277 - live_ask stamps served_attrib...` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/session_followup.py:138 - execute_sql(sql, path=warehouse, product=True): DMS's own DuckDB executes the answer SQL`<br>`packages/executor/dms_executor/session_followup.py:99-119 - an L2_VALIDATED envelope is built from those local rows; the module has no SubmitRequest,...` | `followup-no-submit` |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/session_followup.py:191-197 - no prior values gives a named ABSTAIN ('average of them: no prior numeric values')`<br>`packages/executor/dms_executor/session_followup.py:200-213 - a compute exception gives a named abstain` | `followup-stale-prior-after-failed-turn` |

Also on this path: packages/executor/dms_executor/session_followup.py:199,220 - every follow-up figure is labelled MYR (alias average_myr, 'RM') whatever the prior values are (percentages, counts, the synthetic v_count row_count)

Also on this path: packages/executor/dms_executor/envelope.py:1294-1305 + session_followup.py:220 - 'Average of the prior N figures': N is distinct numeric cells (dedupe, 50-row and 80-value cap), not rows

Also on this path: packages/executor/dms_executor/envelope.py:797-806 - _executed_query accepts any non-comment SQL text, so a table-less literal SELECT satisfies E3, E9 and hard rule 12 and ships L2_VALIDATED

<a id="ep-chat-ask-followup-add"></a>
### `chat-ask:followup-add`

Session follow-up: 'add N'. Producer: packages/executor/dms_executor/session_followup.py:172 maybe_followup (add branch :226-259) -> _pack_compute :89; called at packages/executor/dms_executor/__init__.py:578-590. Reachable: yes, on default config. Same route and gating as followup-average: POST /v1/chat/ask (apps/api/dms_api/routes/chat.py:214), DMS_ASK_MODE=live (settings.py:40), Cortex client from lifespan (app.py:99-105), ask_path None/...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/__init__.py:578-590 - the branch returns before the first grantable_tables call at :598`<br>`packages/executor/dms_executor/session_followup.py:51-55 - the key is (session_id, space_id), both caller-supplied` | `followup-no-grant-recheck` |
| G2 ontology verification | **NO** (medium) | `packages/executor/dms_executor/session_followup.py:233 - SELECT ROUND(<prior> + <delta>, 2) AS adjusted_myr from literals; no ontology call in the mo...`<br>`packages/executor/dms_executor/session_followup.py:58-64 - the snapshot drops label, measure and lane` | `followup-unit-relabelled-rm` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/session_followup.py:233 - the only SQL is ROUND(<float> + <float>, 2) with no FROM`<br>`packages/executor/dms_executor/session_followup.py:23 - the delta is a regex-restricted number; it is float()-ed and formatted :.10g before it reache...` |  |
| G4 typed ingest | n/a (medium) | `packages/executor/dms_executor/session_followup.py:233 - no table in the statement`<br>`packages/executor/dms_executor/session_followup.py:189-190 - prior values are float-filtered, not cast from strings` |  |
| G5 PII mask (envelope + record) | yes (medium) | `packages/executor/dms_executor/session_followup.py:99-116 - built through build_answer_envelope`<br>`packages/executor/dms_executor/envelope.py:1626-1637 - fail_closed_mask_payload over text, rows, values, sources, chart and sql_used` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/session_followup.py:15-20 - no model call, so (a) does not apply`<br>`packages/executor/dms_executor/__init__.py:504-506 + generative_ask.py:228-229,258-277 - served_attribution is stamped 'none' (truthful)` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/session_followup.py:233-251 - SELECT ROUND(a + delta) is run by _followup_execute -> execute_sql at :138 in DMS's own ...`<br>`packages/executor/dms_executor/session_followup.py:251-259 - the L2_VALIDATED envelope is built from the local rows; no manifest or ledger call exist...` | `followup-no-submit` |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/session_followup.py:227-232 - a non-scalar prior gives a named abstain`<br>`packages/executor/dms_executor/session_followup.py:242-247 - a compute exception gives a named abstain` | `followup-stale-prior-after-failed-turn` |

Also on this path: packages/executor/dms_executor/session_followup.py:254 - the text prints the delta with :g (6 significant digits) while the SQL uses :.10g (:233). A delta of 1234567 prints as 1.23457e+06, and 123456.789 prints as 123457 while the total used the full value

Also on this path: packages/executor/dms_executor/envelope.py:845-859,1648-1665 - decimal literals in text must appear in values[]. A decimal delta such as 'add 2.5' prints '2.5' in the text, which is not in values[], so E4 demotes it to an ABSTAIN with an unrelated 'cannot cite from the query result' message, and th...

Also on this path: packages/executor/dms_executor/__init__.py:240-247 - any abstained turn, including a refused 'add N', pops the prior, so a later valid follow-up abstains although the answer is still on screen

<a id="ep-chat-ask-followup-abstain"></a>
### `chat-ask:followup-abstain`

Session follow-up abstain. Producer: packages/executor/dms_executor/session_followup.py:67 _abstain (plus reserved_as_of_abstain packages/executor/dms_executor/envelope.py:1830 via session_followup.py:131-137). Reachable: yes, on default config. Same route and gating as followup-average (POST /v1/chat/ask, chat.py:214; settings.py:40; app.py:99-105; __init__.py:548). It is reached when the phrase matches (session_followup.py:22-23) and (...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/session_followup.py:189-197,227-232 - the abstain decision reads the cardinality of the cached prior, keyed only by (s...`<br>`packages/executor/dms_executor/session_followup.py:68-86 - the envelope itself carries only static text, rows=[], values=[], and the why string in as...` | `followup-no-grant-recheck` |
| G2 ontology verification | n/a (high) | `packages/executor/dms_executor/session_followup.py:82-83 - _abstain passes rows=[] and values=[]`<br>`packages/executor/dms_executor/envelope.py:1682-1686 - an abstained envelope forces values_out=[], rows_out=[], sources=[]` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/session_followup.py:68-84 - _abstain passes no sql_used`<br>`packages/executor/dms_executor/envelope.py:1697-1699 - sql_out is None for an abstained envelope; the stub applies only to non-abstained envelopes` |  |
| G4 typed ingest | n/a (high) | `packages/executor/dms_executor/session_followup.py:68-86 - the abstain branches read no table`<br>`packages/executor/dms_executor/session_followup.py:198-199,233 - the only statement ever built has no FROM` |  |
| G5 PII mask (envelope + record) | yes (high) | `packages/executor/dms_executor/session_followup.py:68-86 - built through build_answer_envelope; the text is static and the why strings are static`<br>`packages/executor/dms_executor/envelope.py:1626-1637 - the masker still runs over text, rows and values` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/session_followup.py:15-20 - no model call`<br>`packages/executor/dms_executor/__init__.py:504-506 + generative_ask.py:228-229,258-277 - served_attribution is stamped 'none' (truthful)` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | n/a (high) | `packages/executor/dms_executor/session_followup.py:82-83 - rows=[] and values=[]`<br>`packages/executor/dms_executor/envelope.py:1682-1686 - abstain forces empty values, rows and sources, and drillthrough_token None` |  |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/session_followup.py:68-86 - an ABSTAIN envelope with route 'followup' and the reason in assumptions[1], built through ...`<br>`packages/executor/dms_executor/session_followup.py:193-197,209-213,228-232,243-247 - the four in-lane branches each carry a distinct why string` | `certified-lane-failure-not-named` |

Also on this path: packages/executor/dms_executor/session_followup.py:70-73 - the abstain text says 'without a prior numeric answer' even for 'compute failed' and 'prior turn was not a single scalar'; only assumptions[1] has the true reason (a misattribution of cause)

Also on this path: packages/executor/dms_executor/__init__.py:240-247 - an abstained follow-up pops the prior, so one unsupported 'add N' destroys the state a later valid 'average of them' needs

Also on this path: packages/executor/dms_executor/envelope.py:1854 + session_followup.py:131-137 - reserved_as_of_abstain is dead on this lane because the SQL is literals only

<a id="ep-cache-session-turns"></a>
### `cache:session-turns`

In-process prior-turn values cache (feeds follow-up figures). Producer: packages/executor/dms_executor/__init__.py:237-247 Executor._store_turn -> packages/executor/dms_executor/session_followup.py:58-64 snapshot_turn; the store is the dict at __init__.py:193. Reachable: yes, on default config. The dict is Executor._turns (packages/executor/dms_executor/__init__.py:193) on the single Executor built in lifespan (apps/api/dms_api/app.py:105). It is written by _store_turn after each answer...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/session_followup.py:51-55 - key = (session_id, space_id), both caller-supplied; a missing Space is ''`<br>`packages/executor/dms_executor/__init__.py:240-247,578-582 - write and read use that key only; no grantable_tables, intersect_space_grants or table_i...` | `followup-no-grant-recheck` |
| G2 ontology verification | **NO** (medium) | `packages/executor/dms_executor/session_followup.py:58-64 - snapshot_turn keeps only float values and sql_used, dropping label, route, badge, sources ...`<br>`packages/executor/dms_executor/__init__.py:589-813 - every answering lane stores into it, whatever its provenance` | `followup-unit-relabelled-rm` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/session_followup.py:189-190 - the only reader of a cache entry uses prior['values'] and nothing else`<br>`packages/executor/dms_executor/session_followup.py:64 - sql_used is stored but never read back or executed` |  |
| G4 typed ingest | n/a (medium) | `packages/executor/dms_executor/__init__.py:237-247 - the store/read path touches no table`<br>`packages/executor/dms_executor/session_followup.py:189-190 - only isinstance int/float filtering, no casting` |  |
| G5 PII mask (envelope + record) | yes(2/3) (medium) | `packages/executor/dms_executor/envelope.py:1626-1637 - every stored envelope was built by build_answer_envelope, which masks text, rows, values, sour...`<br>`packages/executor/dms_executor/verified_queries.py:296 - VQ lane builds through it` |  |
| G6 strict pin + served fields | part (low) | `packages/executor/dms_executor/__init__.py:237-247 + session_followup.py:58-64 - the cache makes no model call and keeps no served_* field, so any fi...`<br>`packages/executor/dms_executor/__init__.py:504-506 + generative_ask.py:228-229,258-277 - the envelope the cache feeds is stamped served_attribution '...` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | **NO** (medium) | `packages/executor/dms_executor/session_followup.py:64 - the snapshot keeps no manifest, receipt, audit id or ledger reference, only values and sql_us...`<br>`packages/executor/dms_executor/session_followup.py:138 - figures leave the cache only through a local DuckDB execute` | `followup-no-submit` |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/__init__.py:244-246 - an abstained turn pops the key, so the next follow-up abstains by name`<br>`packages/executor/dms_executor/__init__.py:232-235 - close() or a restart empties it, so the next follow-up abstains by name` | `followup-stale-prior-after-failed-turn` |

Also on this path: packages/executor/dms_executor/__init__.py:193,240-247 - process-global dict with no eviction, TTL or lock; concurrent asks in one session race between the read at :582 and the write at :589

Also on this path: packages/executor/dms_executor/session_followup.py:39-48 vs 189-190 - numeric_values and the inline filter are two copies of the same rule; only the inline copy is used on the read side

Also on this path: packages/executor/dms_executor/__init__.py:589 - follow-up results are stored as the next turn, so derived figures chain and the original parent's provenance is lost after one hop

<a id="ep-chat-ask-verified-query"></a>
### `chat-ask:verified-query`

Steward verified question->SQL (VQ-02) via Cortex submit. Producer: packages/executor/dms_executor/verified_queries.py:319 maybe_verified_ask -> :275 envelope_from_verified_submit; called at packages/executor/dms_executor/__init__.py:592-612; submit via Executor._sub.... Reachable: yes. POST /v1/chat/ask on the default product ladder (apps/api/dms_api/routes/chat.py:214; ask_path None means product; DMS_ASK_MODE default live at apps/api/dms_api/settings.py:40; DMS_DEMO_FALLBACK default off at :43)...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/chat.py:240-241 - 404 space_not_found when space_id is not in SpaceStore (catalog check, not the shared grant)`<br>`packages/executor/dms_executor/__init__.py:593-612 - maybe_verified_ask is given grantable=set(self.grantable_tables(space_id=space_id)) at :598` | `no-space-ask-widest-grant` |
| G2 ontology verification | **NO** (high) | `packages/executor/dms_executor/verified_queries.py:125-175 - register_verified_query checks only reject_hostile_chat_sql (:144) and the DEMO_TABLES g...`<br>`packages/executor/dms_executor/generative_ask.py:1106-1108 - load_verified_ontology is called here, on the generative lane; no VQ code calls it` | `served-sql-never-reverified-against-ontology` |
| G3 join rule + fan-out guard | **NO** (high) | `packages/executor/dms_executor/verified_queries.py:125-175 - no join-rule or fan-out check at registration`<br>`packages/executor/dms_executor/verified_queries.py:214-251 - no check at lookup beyond grant scan and hostile patterns` | `stored-sql-no-fanout-guard` |
| G4 typed ingest | part (low) | `packages/executor/dms_executor/verified_queries.py:222-224,244-251 - lookup is limited to DEMO_TABLES manifests; with grounded tables selected it ret...`<br>`packages/executor/dms_executor/__init__.py:365-386 - default_readable = grantable intersect DEMO_TABLES, so served tables are the six demo tables` | `cortex-lake-column-types-unread-by-dms` |
| G5 PII mask (envelope + record) | part (low) | `packages/executor/dms_executor/envelope.py:1621-1640 - text, rows, values, sources, chart and sql_used pass fail_closed_mask_payload`<br>`packages/executor/dms_executor/envelope.py:1739 - mask_unknown_keys on the whole envelope` | `ledger-append-carries-unmasked-sql` |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/verified_queries.py:319-370 - maybe_verified_ask makes no model call; its only calls are submit and ledger_append`<br>`packages/executor/dms_executor/__init__.py:593-612 - this lane returns before any Insights call, so the seen list stays empty` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | yes (medium) | `packages/executor/dms_executor/__init__.py:441-453 - _submit_verified_sql: demo_acl, bind_session, then submit_sql`<br>`packages/executor/dms_executor/__init__.py:816-850 - submit_sql mints a manifest (:829) and calls cortex.submit (:837)` |  |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/chat.py:240-241,257-258,260-267 - named 404 space_not_found, 403 gate reason, 503 cortex_unavailable`<br>`apps/api/dms_api/routes/chat.py:291-335 - AskServiceError maps to Space-refusal ABSTAIN or a status from _status_for; chat.py:336-354 - any other exc...` | `certified-lane-failure-not-named` |

Also on this path: packages/executor/dms_executor/__init__.py:850 + apps/api/dms_api/routes/chat.py:291-354 - SubmitError (path_not_allowed, statement_not_allowed) from submit_sql is not an AskServiceError. On this lane a policy refusal becomes 503 live_ask_failed ('Cortex down') or, with DMS_DEMO_FALLBACK=1, demo nu...

Also on this path: packages/executor/dms_executor/verified_queries.py:83 - _connect uses raw duckdb.connect, bypassing the per-file lock that demo_warehouse.connect_file takes (demo_warehouse.py:170-173 ponytail comment says a second RW attach 500s). PLAUSIBLE: a concurrent Library list or lookup may raise and surfac...

Also on this path: packages/executor/dms_executor/verified_queries.py:125-175,299 - an asset is labelled L0_CERTIFIED with assumption 'verified question registered in Studio' although registration never executes or dry-runs the SQL. First use is its first execution.

<a id="ep-chat-ask-governed-pack"></a>
### `chat-ask:governed-pack`

Demo governed-metric pack (VQ-01/VQ-03) via Cortex submit. Producer: packages/executor/dms_executor/demo_pack.py:326 maybe_pack_ask -> :283 envelope_from_pack_submit; called at packages/executor/dms_executor/__init__.py:614-633. Reachable: yes. POST /v1/chat/ask on the default product ladder (apps/api/dms_api/routes/chat.py:214, DMS_ASK_MODE default live at apps/api/dms_api/settings.py:40, DMS_DEMO_FALLBACK default off at :43). No setup is needed beyond C...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/chat.py:240-241 - 404 space_not_found when space_id is not in SpaceStore`<br>`packages/executor/dms_executor/__init__.py:614-633 - maybe_pack_ask is given grantable=set(self.grantable_tables(space_id=space_id)) at :618` | `no-space-ask-widest-grant` |
| G2 ontology verification | **NO** (high) | `packages/executor/dms_executor/demo_pack.py:40-93 - the metric SQL constants (SUM(quantity_kg * unit_cost_myr), joins) are hard-coded`<br>`packages/executor/dms_executor/demo_pack.py:326-377 - maybe_pack_ask has no ontology step` | `served-sql-never-reverified-against-ontology` |
| G3 join rule + fan-out guard | **NO** (high) | `packages/executor/dms_executor/demo_pack.py:40-49 SPEND_BY_COUNTRY_SQL, :51-56 TOTAL_SPEND_SQL, :60-65 LOW_STOCK_WH_A_SQL, :66-71 SHIPMENT_COST_SQL -...`<br>`packages/executor/dms_executor/demo_pack.py:252-282 - lookup does grant, exact match and a hostile-pattern check; no join-rule or fan-out step` | `stored-sql-no-fanout-guard` |
| G4 typed ingest | part (low) | `packages/executor/dms_executor/demo_pack.py:78-81 - EXPIRED_ITEMS_SQL uses CAST(expiry_date AS DATE) at question time`<br>`packages/executor/dms_executor/demo_warehouse.py:350-358 - in the DMS seed inventory.expiry_date is declared DATE, so the cast is redundant there` | `cortex-lake-column-types-unread-by-dms` |
| G5 PII mask (envelope + record) | yes (medium) | `packages/executor/dms_executor/envelope.py:1621-1640 - text, rows, values, sources, chart and sql_used pass fail_closed_mask_payload`<br>`packages/executor/dms_executor/envelope.py:1739 - mask_unknown_keys on the whole envelope` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/demo_pack.py:326-377 - maybe_pack_ask makes no model call; its only calls are submit and ledger_append`<br>`packages/executor/dms_executor/__init__.py:614-633 - this lane returns before any Insights call, so the seen list stays empty` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | yes (medium) | `packages/executor/dms_executor/__init__.py:614-633 and :441-453 - pack submit goes through _submit_verified_sql`<br>`packages/executor/dms_executor/__init__.py:816-850 - submit_sql mints a manifest (:829) and calls cortex.submit (:837)` |  |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/chat.py:240-241,257-258,260-267 - named 404, 403 and 503 branches`<br>`apps/api/dms_api/routes/chat.py:291-354 - named mapping for AskServiceError and generic exceptions, with demo fallback only when the flag is on` | `certified-lane-failure-not-named` |

Also on this path: packages/executor/dms_executor/demo_pack.py:351-353 - swallowing every submit exception hides policy refusals (path_not_allowed, statement_not_allowed) and timeouts, with no log line from this lane.

Also on this path: packages/executor/dms_executor/demo_pack.py:320 - the envelope's grounded_tables is list(metric.tables), the metric's tables and not the manifest's granted set. envelope.py:1717-1720 says that count must come from the manifest (dms#5).

Also on this path: apps/api/dms_api/routes/chat.py:23,257-258 - compliance_gate decisions gate_unavailable and gate_task_unknown are treated as pass, so the F5 gate fails open on this route (all lanes).

<a id="ep-chat-ask-planted-refuse"></a>
### `chat-ask:planted-refuse`

Planted uncertified-paraphrase refuse trap (VQ-04). Producer: packages/executor/dms_executor/demo_pack.py:200 maybe_uncertified_refuse_ask; called at packages/executor/dms_executor/__init__.py:635-640. Reachable: yes, on default config. POST /v1/chat/ask (apps/api/dms_api/routes/chat.py:214,270) -> Executor.live_ask (packages/executor/dms_executor/__init__.py:480) -> _live_ask :635-640, on every ladder, when the normalized quest...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `packages/executor/dms_executor/demo_pack.py:200-238 - maybe_uncertified_refuse_ask builds a static ABSTAIN (values=[], rows=[], sql_used=None) from f...`<br>`packages/executor/dms_executor/__init__.py:593-640 - the only reads before it are grantable_tables (grant metadata), lookup_verified_query and lookup...` |  |
| G2 ontology verification | n/a (high) | `packages/executor/dms_executor/demo_pack.py:213-231 - badge ABSTAIN, values=[], rows=[], sql_used=None`<br>`packages/executor/dms_executor/envelope.py:1681-1686 - abstained envelopes clear values/rows/sources/chart` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/demo_pack.py:213-231 - sql_used=None, rows=[]` |  |
| G4 typed ingest | n/a (high) | `packages/executor/dms_executor/demo_pack.py:200-238 - no table or file is read to produce the envelope` |  |
| G5 PII mask (envelope + record) | yes (high) | `packages/executor/dms_executor/envelope.py:1626-1640 - build_answer_envelope runs fail_closed_mask_payload over text/rows/values/sources/chart/sql_us...`<br>`packages/executor/dms_executor/envelope.py:1739 - mask_unknown_keys over the remaining keys (assumptions, suggestions, grounded_tables)` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/__init__.py:504-510 - live_ask stamps served_attribution from the Insights payload seen; none was made, so payload is ...`<br>`packages/executor/dms_executor/generative_ask.py:216-246 - served_attribution(None) returns 'none'` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | n/a (high) | `packages/executor/dms_executor/demo_pack.py:213-231 - ABSTAIN with no figure and no rows` |  |
| G8 named abstain on failure | part(2/3) (medium) | `packages/executor/dms_executor/demo_pack.py:213-237 - ABSTAIN with refusal text, assumptions 'uncertified paraphrase...', route 'abstain', assert_env...`<br>`apps/api/dms_api/routes/chat.py:336-352 - any exception on this path (grant read, VQ registry, envelope assert) becomes 503 live_ask_failed or 504 li...` |  |

Also on this path: packages/executor/dms_executor/__init__.py:593-596,614-617 - set(self.grantable_tables(...)) is evaluated unguarded before the planted refuse. A grant-read exception turns a trap question into 503 live_ask_failed instead of the refusal (chat.py:336-352). Tracked by #307 / draft PR #331 (unread gran...

Also on this path: packages/executor/dms_executor/__init__.py:593-640 - VQ and pack run before the refusal, so a steward-registered VQ (or pack entry) with a trap phrase answers instead of refusing. Deliberate per the F83 comment, but the 'planted refuse' is not unconditional.

Also on this path: apps/api/dms_api/routes/chat.py:23,257-258 - compliance_gate soft-fails open for chat.ask (gate_unavailable, gate_task_unknown continue). Not a G1-G8 gate; noted.

<a id="ep-chat-ask-harness-exact-miss"></a>
### `chat-ask:harness-exact-miss`

GEN-03 isolated 'exact' ladder miss. Producer: packages/executor/dms_executor/generative_ask.py:915 path_miss_envelope -> _abstain :670; called at packages/executor/dms_executor/__init__.py:642-650. Reachable: flag-gated. Needs DMS_HARNESS_ASK_PATHS (settings.py:50, default False), otherwise 400 ask_path_not_allowed (apps/api/dms_api/routes/chat.py:227-238). The UI never sends ask_path (apps/ui/src/lib/api.ts:146-171) and MCP...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `packages/executor/dms_executor/__init__.py:642-650 - the exact-lane miss returns before grant narrowing (:657-666), cascade, bronze, generative and C...`<br>`packages/executor/dms_executor/__init__.py:545-549 - on exact: allow_gen, allow_cortex, allow_follow, allow_bronze are all False` |  |
| G2 ontology verification | n/a (high) | `packages/executor/dms_executor/generative_ask.py:679-695 - _abstain builds rows=[], sql_used=None` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/generative_ask.py:679-695 - sql_used=None, rows=[]` |  |
| G4 typed ingest | n/a (high) | `packages/executor/dms_executor/__init__.py:642-650 - returns before any table read` |  |
| G5 PII mask (envelope + record) | yes (high) | `packages/executor/dms_executor/envelope.py:1626-1640 and :1739 - sole constructor masks text/rows/values/sources/chart/sql_used and scans the other k...`<br>`packages/executor/dms_executor/__init__.py:508-510 - live_ask re-masks` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/__init__.py:504-510 - served_attribution stamped from payload None`<br>`packages/executor/dms_executor/generative_ask.py:216-246 - returns 'none' when no payload` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | n/a (high) | `packages/executor/dms_executor/generative_ask.py:679-695 - ABSTAIN, no figure/rows` |  |
| G8 named abstain on failure | part(2/3) (medium) | `packages/executor/dms_executor/__init__.py:642-649 - miss yields ABSTAIN with reason 'exact-match miss: not a certified VQ/pack hit'`<br>`packages/executor/dms_executor/generative_ask.py:679-695 - assumptions[0]='GEN-01: <reason>', assert_envelope_valid` |  |

Also on this path: packages/executor/dms_executor/gen_path_refuse.py:49-75 - a reason that is not in GAP_REASONS renders as 'I cannot certify an ontology-grounded query for that question', which misdescribes an exact-match miss. The real reason appears only in assumptions[0] and audit_receipt.unsure.why.

Also on this path: packages/executor/dms_executor/__init__.py:594-596,615-617 - unguarded grantable_tables before the miss (same unread-grant exception path as #307 / draft PR #331).

<a id="ep-chat-ask-cascade-abstain"></a>
### `chat-ask:cascade-abstain`

CCA-05 constraint cascade blocked abstain. Producer: packages/executor/dms_executor/cca/cascade.py:338 run_cascade -> :410 cascade_abstain_envelope; invoked at packages/executor/dms_executor/__init__.py:659-686. Reachable: flag-gated. Needs DMS_CCA_CASCADE in {1,true,yes,on} (packages/executor/dms_executor/cca/cascade.py:88-95; default '0', read from os.environ, not Settings, and no compose or env file in the worktree sets it). Also needs...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (high) | `apps/api/dms_api/routes/chat.py:117,240-241 — space_id is optional; an unknown Space is 404, but omitting it is accepted`<br>`packages/executor/dms_executor/__init__.py:659-666 — granted = grantable_tables(space_id); readable = (ticked ∩ granted) or (granted ∩ DEMO_TABLES); ...` | `no-space-ask-widest-grant` |
| G2 ontology verification | n/a (high) | `packages/executor/dms_executor/cca/cascade.py:424-427 — the blocked cascade returns badge ABSTAIN, values=[], sql_used=None`<br>`packages/executor/dms_executor/cca/cascade.py:37-39 — grain, ontology and sql are deliberately absent from the trace` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/cca/cascade.py:427 — sql_used=None on the abstain envelope`<br>`packages/executor/dms_executor/envelope.py:1681-1686 — abstain wipes values, rows, sources and chart` |  |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/cca/binder.py:228-231 — CAST(col AS VARCHAR) at question time; no persisted type or contract is consulted`<br>`packages/executor/dms_executor/bronze.py:406-413 — bronze tables come from read_csv auto_detect with no declared contract` | `cascade-certifies-spelling-not-executed-sql` |
| G5 PII mask (envelope + record) | yes (medium) | `packages/executor/dms_executor/envelope.py:1626-1640 — build_answer_envelope runs fail_closed_mask_payload over text, rows, values, sources, chart an...`<br>`packages/executor/dms_executor/envelope.py:1739 and packages/core/dms_core/pii.py:1032-1043 — mask_unknown_keys scans every top-level key not in SAFE...` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/__init__.py:659-686 — the only calls on this path are run_cascade (lexical proposers plus local DuckDB); no model call...`<br>`packages/executor/dms_executor/__init__.py:495-507 — live_ask stamps served_attribution on every result; `seen` is empty here so the payload is None` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | n/a (medium) | `packages/executor/dms_executor/cca/cascade.py:424-427 — ABSTAIN, values=[], sql_used=None`<br>`packages/executor/dms_executor/envelope.py:1681-1686,1884-1888 — an abstain wipes values and rows, and E2 asserts it` |  |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/cca/cascade.py:175-182,421-440 — a blocked stage yields ABSTAIN naming the stage and reason, plus 'CCA-05: <stage> did...`<br>`packages/executor/dms_executor/__init__.py:659-662 — a grant-read exception is swallowed to []; the cascade then abstains with 'no granted table to c...` | `ungranted-tick-dropped-not-refused` |

Also on this path: packages/executor/dms_executor/__init__.py:676-686 — the blocked-cascade return skips _store_turn, while every other abstain path calls it (:639,649,726,762,774,813) and clears the turn cache via snapshot_turn (session_followup.py:58-64). After Q1 (numeric) then a Q2 cascade abstain, 'add 10' or 'a...

Also on this path: packages/executor/dms_executor/cca/binder.py:256,302 (and cca/sense.py bind_sense table_list) — schema-qualified grant labels are reduced to the bare name via split('.')[-1]. I confirmed with an in-memory DuckDB that an unqualified name does not see the bronze schema. So real bronze.* uploads are n...

Also on this path: packages/executor/dms_executor/cca/binder.py:253 — raw duckdb.connect(read_only=True) bypasses demo_warehouse.connect_readonly, which comments that mixed read-only and RW opens on one file 500 DuckDB (demo_warehouse.py:458-461). A cascade ask concurrent with ingest can raise, surfacing as 503 live_...

<a id="ep-chat-ask-cascade-attach"></a>
### `chat-ask:cascade-attach`

Cascade trace attach / fail-closed demotion of a later lane's answer. Producer: packages/executor/dms_executor/cca/cascade.py:443 attach_cascade; applied at packages/executor/dms_executor/__init__.py:725, 761, 801-812. Reachable: flag-gated and mostly inert. Needs DMS_CCA_CASCADE on (cascade.py:88-95, default '0'), the cascade engaged and not blocked, and then one of three later lanes to answer: bronze (__init__.py:724-727, ladder product only),...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (high) | `packages/executor/dms_executor/__init__.py:693-723 — bronze sub-lane: no Space gives bronze_grant_abstain(no_space); otherwise table_is_granted(targe...`<br>`packages/executor/dms_executor/ontology.py:398-404 — table_is_granted, the shared predicate` | `no-space-ask-widest-grant` |
| G2 ontology verification | **NO** (medium) | `packages/executor/dms_executor/bronze_sheet_ask.py:194,262 — bronze sub-lane serves SUM(TRY_CAST(measure)) over a raw sheet with no ontology`<br>`packages/executor/dms_executor/generative_ask.py:1222-1252,1283-1297 — model-written SELECT goes to submit after validate_compiled_sql (hostile, gran...` | `served-sql-never-reverified-against-ontology` |
| G3 join rule + fan-out guard | **NO** (medium) | `packages/executor/dms_executor/generative_ask.py:1235-1297 — generated SELECT with joins is validated for hostility, grants and EXPLAIN, then submitt...`<br>`packages/executor/dms_executor/ontology.py:1056-1068 — fanout_refused exists only in the ontology compile path` | `free-sql-lanes-no-fanout-guard` |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/bronze_sheet_ask.py:194-196,262-265 — TRY_CAST(measure AS DOUBLE) at question time over bronze columns`<br>`packages/executor/dms_executor/cca/binder.py:228-231 — the cascade casts to VARCHAR ad hoc` | `bronze-sheet-trycast-drops-rows-silently` |
| G5 PII mask (envelope + record) | part (medium) | `packages/executor/dms_executor/cca/cascade.py:474-479 — encoding source cards are appended to contributing_sources after the lane built and masked th...`<br>`packages/core/dms_core/pii.py:201-211,1040 — contributing_sources is in _HANDLED_KEYS, so mask_unknown_keys skips it` | `audit-receipt-and-late-added-cards-skip-mask` |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/__init__.py:738-747 — the generative sub-lane's model call goes through _insights_compute_seam`<br>`packages/cortex_client/cortex_client/compute.py:838-840 — stamp_generate_body pins model and strict on that generate request` | `contract-ask-model-call-unpinned-and-stamped-none` |
| G7 Cortex submit (manifest + ledger) | part (medium) | `packages/executor/dms_executor/generative_ask.py:818-862 — generative sub-lane: submit(sql), then ledger_append, then entry_id and hash checks, else ...`<br>`packages/executor/dms_executor/__init__.py:441-478,816-839 — _submit_verified_sql mints a manifest via the minter and submits; ledger via Cortex` | `bronze-sheet-l0-minted-locally` |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/cca/cascade.py:451-462 — a trace that fails parse_trace demotes to ABSTAIN with named text, but leaves contributing_so...`<br>`packages/executor/dms_executor/cca/cascade.py:481-483 and apps/api/dms_api/routes/chat.py:336-354 — an E1-E9 failure raises a plain AssertionError, w...` | `cascade-certifies-spelling-not-executed-sql` |

Also on this path: packages/executor/dms_executor/cca/cascade.py:451-462 — the demotion branch sets badge, abstained, values, rows, sql_used and text but not contributing_sources, drillthrough_token, chart or audit_receipt (envelope.py:1156-1169 holds include.rows), and skips assert_envelope_valid. If ever reached, t...

Also on this path: The attached trace describes bindings over demo or main-schema columns (cascade.py:152-173,463-479), while the bronze lane's SQL is a fixed template over bronze.<ident> that ignores sense, class and geo terms entirely (bronze_sheet_ask.py:186-206). The CERTIFIED trace beside an L0_CERTIFIED bronze ...

Also on this path: The cascade scans the DMS ingest file (__init__.py:670, ensure_demo_warehouse(self._warehouse)), while the contract ask reads Cortex's serving file in a split layout (warehouse_identity.py:88-97). The trace attached to a contract-ask answer therefore reports landed values from a different file than...

<a id="ep-chat-ask-bronze-sheet"></a>
### `chat-ask:bronze-sheet`

Named workbook+sheet bronze ask (top-N category / filtered total). Producer: packages/executor/dms_executor/bronze_sheet_ask.py:105 maybe_bronze_sheet_ask (_grouped_top_n :170, _eq_filter_total :237); called at packages/executor/dms_executor/__init__.py:688-727. Reachable: yes on default config. POST /v1/chat/ask (apps/api/dms_api/routes/chat.py:214) with dms_ask_mode=live (apps/api/dms_api/settings.py:40), dms_demo_fallback=False (settings.py:43), no ask_path = product ladder (packages/e...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | yes (high) | `packages/executor/dms_executor/__init__.py:660 - granted = self.grantable_tables(space_id) is computed before the lane (Space grants via intersect_sp...`<br>`packages/executor/dms_executor/__init__.py:693-702 - target = bronze_lane_table(question); no space_id => bronze_grant_abstain(reason='no_space'), ma...` |  |
| G2 ontology verification | **NO** (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:191-206 - hard-coded SUM(TRY_CAST(measure AS DOUBLE)) GROUP BY category; measure picked by the _ME...`<br>`packages/executor/dms_executor/bronze_sheet_ask.py:14-24 - imports: no ontology, load_verified_ontology or Ontology.verify` | `served-sql-never-reverified-against-ontology` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:191-206 and :259-268 - the only SQL is two f-string templates with a single FROM bronze."{ident}" ...`<br>`packages/executor/dms_executor/bronze_sheet_ask.py:25,63-65,115-116 - _IDENT ^[A-Za-z0-9_]+$ guard on the table ident` |  |
| G4 typed ingest | **NO** (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:194,196,262,265 - TRY_CAST(measure AS DOUBLE) at question time; rows whose cast is NULL are droppe...`<br>`packages/executor/dms_executor/bronze_sheet_ask.py:192,261,264 - CAST(category/col AS VARCHAR) ad hoc` | `bronze-sheet-trycast-drops-rows-silently` |
| G5 PII mask (envelope + record) | yes (medium) | `packages/executor/dms_executor/bronze_sheet_ask.py:213,281,302 - every envelope on this lane is built only by build_answer_envelope`<br>`packages/executor/dms_executor/envelope.py:1626-1636 - fail_closed_mask_payload over text, rows, values, sources, chart and sql_used inside build_ans...` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/bronze_sheet_ask.py:14-24 - no model or HTTP client on this lane (DuckDB only), so (a) does not apply`<br>`packages/executor/dms_executor/__init__.py:504-510 - with_served_attribution(env, payload) stamps every live_ask result; payload is None here because...` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:186-206 and :254-272 - figures computed by DMS's own DuckDB (duckdb.connect read_only + con.execut...`<br>`packages/executor/dms_executor/bronze_sheet_ask.py:14-24 - no ManifestMinter, SubmitRequest, canonical_manifest_bytes or ledger import on this lane` | `bronze-sheet-l0-minted-locally` |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/bronze_sheet_ask.py:184-185 and :252-253 - table not found (db is None) returns None with no reason`<br>`packages/executor/dms_executor/bronze_sheet_ask.py:189-190 and :257-258 - category or measure column missing returns None with no reason` | `certified-lane-failure-not-named` |

Also on this path: packages/executor/dms_executor/bronze_sheet_ask.py:219-222 and :275-276 vs :191-205 and :259-268 - the sql_used shown to the user omits the WHERE predicates the executed SQL applies (TRY_CAST IS NOT NULL, category non-blank and non-null), so re-running the displayed SQL gives a different total than...

Also on this path: packages/executor/dms_executor/bronze_sheet_ask.py:213,281,302 - L0 bronze envelopes are never passed through assert_envelope_valid at serving time (only the grant abstain is, at :101; attach_cascade asserts only when DMS_CCA_CASCADE is on, cca/cascade.py:481-483, default off at :91-95). CLAUDE.md ...

Also on this path: apps/ui/src/components/AnswerMessage.tsx:51-55 - the L0_CERTIFIED badge explanation reads 'A human wrote this query... matched the certified library exactly', which is false for the bronze lane (hard-coded template, no verified-query registration).

<a id="ep-chat-ask-bronze-grant-abstain"></a>
### `chat-ask:bronze-grant-abstain`

Bronze-sheet lane grant/space abstain. Producer: packages/executor/dms_executor/bronze_sheet_ask.py:78 bronze_grant_abstain; called at packages/executor/dms_executor/__init__.py:696-723. Reachable: yes on default config. Same routes and defaults as the bronze-sheet row: POST /v1/chat/ask, ask_mode live (apps/api/dms_api/settings.py:40), product ladder (packages/executor/dms_executor/__init__.py:549). Reached when ...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part(2/3) (high) | `packages/executor/dms_executor/__init__.py:660-662 - grantable_tables(space_id) before the lane; an exception leaves granted=[] (fail closed)`<br>`packages/executor/dms_executor/__init__.py:696-702 - no space_id gives ABSTAIN no_space; no table is opened` |  |
| G2 ontology verification | n/a (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:86-98 - abstain envelope built with rows=[], values=[], sql_used=None and fixed text 'ABSTAIN <rea...` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:93 - sql_used=None; the abstain is returned at :101-102 before any SQL, and __init__.py:696-727 re...` |  |
| G4 typed ingest | n/a (medium) | `packages/executor/dms_executor/bronze_sheet_ask.py:86-98 - the answer holds no table content`<br>`packages/executor/dms_executor/bronze.py:526-545 and :570-572 - the grant check reads only catalog, registry and row counts, which are never placed o...` |  |
| G5 PII mask (envelope + record) | yes (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:86-101 - the abstain envelope is built through build_answer_envelope`<br>`packages/executor/dms_executor/envelope.py:1626-1636 and :1739 - mask_payload and mask_unknown_keys are applied inside build_answer_envelope` |  |
| G6 strict pin + served fields | part (medium) | `packages/executor/dms_executor/bronze_sheet_ask.py:78-102 - no model call on this branch, so (a) does not apply`<br>`packages/executor/dms_executor/__init__.py:504-510 - served_attribution stamped on the result; payload is None because the lane returns before the ge...` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | n/a (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:89-94 - badge ABSTAIN, rows=[], values=[], sql_used=None: a pure refusal with no figure or row` |  |
| G8 named abstain on failure | yes(2/3) (high) | `packages/executor/dms_executor/__init__.py:696-702 and :717-723 - both failure branches return a named ABSTAIN (no_space, ungranted_table:<t>)`<br>`packages/executor/dms_executor/__init__.py:724-727 - the abstain is returned immediately: no fall-through to the generative lane, the Cortex ask or d...` |  |

Also on this path: packages/executor/dms_executor/bronze_sheet_ask.py:87 - every refusal carries the same answer_id and audit_id 'ans_bronze_grant' (envelope.py:1712), which resolves to no ledger entry, so refusals cannot be told apart or audited.

Also on this path: packages/executor/dms_executor/__init__.py:660-662 and packages/executor/dms_executor/demo_grants.py:99-118 - a grant-lookup or registry-read failure is reported as 'ungranted_table:<t>' (the lookup failure is not surfaced as its own reason).

Also on this path: packages/executor/dms_executor/bronze.py:524-527 and packages/executor/dms_executor/demo_warehouse.py:264-289 - the grant check (list_bronze_tables) opens the ingest file read-write and runs ensure_lake_schemas and _ensure_registry DDL on every ask. The first call in a process re-seeds the demo tab...

<a id="ep-chat-ask-generative-pregate-abstain"></a>
### `chat-ask:generative-pregate-abstain`

Generative lane pre-gate abstains (vague / predictive / 2099). Producer: packages/executor/dms_executor/generative_ask.py:1020 maybe_generative_ask pre-gates :1072-1086 -> _abstain :670; called at packages/executor/dms_executor/__init__.py:728-763. Reachable: yes, on default config. POST /v1/chat/ask (product or generative ladder, no grounded_tables) -> _live_ask -> maybe_generative_ask pre-gates (generative_ask.py:1072-1086) via __init__.py:728-763. Follow-up, VQ, pack, pla...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `packages/executor/dms_executor/generative_ask.py:1062-1086 - pre-gates are lexical regex/year checks and return before lake open (:1088-1124), retrie...`<br>`packages/executor/dms_executor/__init__.py:657-666 - the only data-adjacent step before is the grant read` |  |
| G2 ontology verification | n/a (high) | `packages/executor/dms_executor/generative_ask.py:679-695 - ABSTAIN with rows=[], sql_used=None`<br>`packages/executor/dms_executor/generative_ask.py:1072-1086 - fires before load_verified_ontology (:1106)` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/generative_ask.py:679-695 - sql_used=None` |  |
| G4 typed ingest | n/a (high) | `packages/executor/dms_executor/generative_ask.py:1072-1086 - returns before any table is read` |  |
| G5 PII mask (envelope + record) | yes (high) | `packages/executor/dms_executor/generative_ask.py:679-695 - _abstain via build_answer_envelope (masking at envelope.py:1626-1640,1739)`<br>`packages/executor/dms_executor/generative_ask.py:1072-1086 - reason strings are fixed text` |  |
| G6 strict pin + served fields | part (medium) | `tests/test_served_attr_01.py:475-487 - POST /v1/chat/ask 'Predict how much revenue we will make' makes zero Insights calls and carries served_attribu...`<br>`packages/executor/dms_executor/generative_ask.py:1072-1086 vs :1131 - gate returns before compute` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | n/a (high) | `packages/executor/dms_executor/generative_ask.py:679-695 - ABSTAIN, no figure/rows` |  |
| G8 named abstain on failure | yes (medium) | `packages/executor/dms_executor/generative_ask.py:1072-1086 - three pre-gates each return a named ABSTAIN reason ('question is too vague or time-unbou...`<br>`packages/executor/dms_executor/generative_ask.py:679-695 - reason in assumptions[0]` |  |

Also on this path: packages/executor/dms_executor/generative_ask.py:1062 - pre-gates are skipped for any grounded ask (tables set returns None), and they sit after follow-up, VQ, pack and the bronze lane (__init__.py:578-727). A predictive/2099 ask that matches those lanes or is file-grounded is not pre-gated; the on...

Also on this path: packages/executor/dms_executor/gen_path_refuse.py:61-75 - the customer text for a vague/predictive/2099 refusal is the generic 'ontology-grounded query' sentence, not the cause.

Also on this path: Dead code confirmed: the paraphrase gate at generative_ask.py:1067-1071 is shadowed by the planted refuse at __init__.py:635.

<a id="ep-chat-ask-generative-multi-grain"></a>
### `chat-ask:generative-multi-grain`

GEN-01 multi-grain where-path compile (L2) via Cortex submit. Producer: packages/executor/dms_executor/generative_ask.py:932 _try_multi_grain_envelope -> :766 _submit_validated -> :698 _l2_envelope (invoked :1209-1221). Reachable: yes on default config, no model key needed. POST /v1/chat/ask -> chat_ask (apps/api/dms_api/routes/chat.py:213-276) -> Executor.live_ask -> _live_ask generative lane (packages/executor/dms_executor/__init__.py:728-763)....

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/chat.py:240-241 - a supplied but unknown space_id is 404 before any lane runs (no space_id skips the check)`<br>`packages/executor/dms_executor/__init__.py:297-328 - grantable_tables: Space path goes through intersect_space_grants -> resolve_session_acl (shared ...` | `no-space-ask-widest-grant` |
| G2 ontology verification | part (medium) | `packages/executor/dms_executor/generative_ask.py:1105-1106,345-373 - load_verified_ontology(lake) runs Ontology.verify on every ask and returns None ...`<br>`packages/executor/dms_executor/ontology.py:1751 - try_compile_multi_grain returns None unless onto.verified; :1607 compile_grains and :1372 compile a...` | `verify-measured-on-dms-file-not-serving-lake` |
| G3 join rule + fan-out guard | yes (high) | `packages/executor/dms_executor/ontology.py:1702-1709 - compile_grains ends in compile(), the only SQL producer on this path`<br>`packages/executor/dms_executor/ontology.py:1432 - compile resolves every group_by through _resolve_path; :1061-1071 raises fanout_refused for a non-m...` |  |
| G4 typed ingest | part (low) | `packages/executor/dms_executor/__init__.py:665 - readable is granted intersect DEMO_TABLES; this lane reads no uploaded or ingested table (a ticked t...`<br>`packages/executor/dms_executor/demo_warehouse.py:292-430 - the demo tables come from _seed with declared DDL types (DOUBLE, DATE, TIMESTAMP...), not ...` | `cortex-lake-column-types-unread-by-dms` |
| G5 PII mask (envelope + record) | yes (medium) | `packages/executor/dms_executor/envelope.py:1626-1639 - build_answer_envelope runs fail_closed_mask_payload over text, rows, values, sources, chart an...`<br>`packages/executor/dms_executor/envelope.py:1739 and packages/executor/dms_executor/__init__.py:508-510 - mask_unknown_keys runs over the whole envelo...` |  |
| G6 strict pin + served fields | part (medium) | `packages/cortex_client/cortex_client/compute.py:838-840 and packages/cortex_client/cortex_client/strict_pin.py:118-127 - the Insights generate body i...`<br>`packages/cortex_client/cortex_client/compute.py:745-752 - interpret() classifies a mismatching 200 as mismatch (strict_pin.py:292-317) but only kind=...` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | yes (medium) | `packages/executor/dms_executor/generative_ask.py:985-998,818-824 - the compiled SQL goes to the submit callable; an exception becomes a named abstain`<br>`packages/executor/dms_executor/__init__.py:748-750,441-453 - submit lambda -> _submit_verified_sql -> demo_acl, bind_session once, submit_sql` |  |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/generative_ask.py:954-998 - refusal, existential, as_of, validate, submit, no-rows and ledger failures are all named a...`<br>`packages/executor/dms_executor/envelope.py:1450-1460 - zero rows after executed SQL is demoted to ABSTAIN (hard rule 12)` | `certified-lane-failure-not-named` |

Also on this path: apps/api/dms_api/routes/chat.py:23,257-258 - gate_unavailable and gate_task_unknown are soft: if the F5 gate is down or does not know the task, the ask proceeds ungated (fail-open) on every lane

Also on this path: packages/executor/dms_executor/generative_ask.py:1131 vs :1209 - when armed, a model call to Cortex Insights (question, ontology slice and DISTINCT samples) is made on every ask before the multi-grain compile that usually does not need it; wasted model call and data egress

Also on this path: packages/executor/dms_executor/generative_ask.py:818-824 - every submit exception collapses to abstain 'submit_failed', losing the chat.py:67-112 distinction between a policy refusal (403), a session error (409) and a transient timeout (504)

<a id="ep-chat-ask-generative-insights-sql"></a>
### `chat-ask:generative-insights-sql`

GEN-01 Cortex Insights-generated SQL, validated then Cortex submit (L2). Producer: packages/executor/dms_executor/generative_ask.py:1222-1297 (kind=='sql' branch; submit :1283-1297) -> _submit_validated :766 -> _l2_envelope :698. Planner: packages/executor/dms_executor/__init__.py:.... Reachable: config-gated. Needs a non-demo CORTEX_API_KEY: the default 'dms-demo-viewer-key' (apps/api/dms_api/settings.py:36) is refused locally by generate_bearer_refuse (packages/cortex_client/cortex_client/compute.py:997-999; p...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/__init__.py:297-328 - grantable_tables uses intersect_space_grants for a Space; :306-317 with no space_id returns all ...`<br>`packages/executor/dms_executor/__init__.py:659-666,736 - readable set handed to the lane as grantable` | `no-space-ask-widest-grant` |
| G2 ontology verification | **NO** (high) | `packages/executor/dms_executor/generative_ask.py:1235-1252 - the only ontology check on model SQL is violations_cited_by_sql, guarded by 'declared is...`<br>`packages/executor/dms_executor/__init__.py:731-759 - live_ask never passes ontology= to maybe_generative_ask, so declared stays None on the live path` | `generative-sql-ships-over-failed-verify-ontology` |
| G3 join rule + fan-out guard | **NO** (high) | `packages/executor/dms_executor/generative_ask.py:637-663 - validate_compiled_sql is as_of + hostile regexes + grant check + EXPLAIN; no join-rule res...`<br>`packages/executor/dms_executor/generative_ask.py:1235,1283-1297 - model SQL that passes goes straight to _submit_validated` | `free-sql-lanes-no-fanout-guard` |
| G4 typed ingest | part (low) | `packages/executor/dms_executor/__init__.py:665 - readable is granted intersect DEMO_TABLES; no ingested upload is read on this lane`<br>`packages/executor/dms_executor/demo_warehouse.py:292-430 - declared DDL on a seeded fixture, not typed ingest output` | `cortex-lake-column-types-unread-by-dms` |
| G5 PII mask (envelope + record) | part (medium) | `packages/executor/dms_executor/envelope.py:1626-1639,1739 and packages/executor/dms_executor/__init__.py:508-510 - envelope text, rows, values, chart...`<br>`packages/executor/dms_executor/generative_ask.py:845 and packages/executor/dms_executor/__init__.py:467-477 - the ledger payload gets the raw model S...` | `ledger-append-carries-unmasked-sql` |
| G6 strict pin + served fields | part (medium) | `packages/cortex_client/cortex_client/compute.py:838-840,728-730 and packages/cortex_client/cortex_client/strict_pin.py:118-127 - body stamped with pi...`<br>`packages/cortex_client/cortex_client/compute.py:940-949 - the ranked retry leg copies insights_body, so it is pinned too` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | yes (medium) | `packages/executor/dms_executor/generative_ask.py:1283-1297,818-829 - validated SQL is submitted through the submit callable; failure or no output abs...`<br>`packages/executor/dms_executor/__init__.py:748-750,441-453,816-837 - _submit_verified_sql -> submit_sql builds SubmitRequest with a minted Manifest a...` |  |
| G8 named abstain on failure | part(2/3) (medium) | `packages/executor/dms_executor/generative_ask.py:1228-1234,1253-1270 - empty SQL, as_of, hostile SQL and invalid SQL with no ranking are named abstai...`<br>`packages/executor/dms_executor/generative_ask.py:1271-1281 - an invalid SELECT with a ranking climbs to the typed plan and records the reject reason ...` |  |

Also on this path: packages/executor/dms_executor/generative_ask.py:818-824 - every submit exception becomes 'submit_failed'; a cold-engine statement_timeout (retryable 504 in chat.py:87) and a path_not_allowed policy refusal (403) are indistinguishable in the ABSTAIN

Also on this path: packages/executor/dms_executor/gen_path_refuse.py:62-75 - customer_abstain_text prints the generic sentence for submit_failed, ledger_*, validate:* and hostile_sql; the reason code is only in assumptions (hard rule 10 is about the rendered text)

Also on this path: packages/executor/dms_executor/envelope.py:62,360-372 - the grant pre-check on model SQL uses a regex that also strips schema qualifiers (_relation_bare, envelope.py:108-110), so 'bronze.transactions' reads as the granted demo 'transactions' at the DMS check; only the Cortex manifest could distingu...

<a id="ep-chat-ask-generative-ontology-plan"></a>
### `chat-ask:generative-ontology-plan`

GEN-01 typed plan / Cortex-ranked metric compiled on the DMS ontology (L2). Producer: packages/executor/dms_executor/generative_ask.py:1359-1470 (plan_from_payload -> _compile_maybe_unverified :1001 -> _submit_validated :766); ranking overlay ontology_plan_from_ranking :878-912. Reachable: config-gated. The plan comes only from a Cortex Insights typed query_plan, an Insights ontology ranking resolved onto a DMS measure, or an invalid-SQL climb (generative_ask.py:1163-1170,1271-1281), and all three need a ...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/__init__.py:297-328 - Space grants via intersect_space_grants; :306-317 no space_id returns all six DEMO_TABLES includ...`<br>`packages/executor/dms_executor/__init__.py:659-666,736 - readable passed as grantable; a ticked table skips the lane (generative_ask.py:1062)` | `no-space-ask-widest-grant` |
| G2 ontology verification | part (medium) | `packages/executor/dms_executor/generative_ask.py:1105-1106,345-373 - verify per ask; unverified default ontology becomes None`<br>`packages/executor/dms_executor/generative_ask.py:1384-1393 - no ontology (or unverified without declared violations) means named abstain ontology_unv...` | `verify-measured-on-dms-file-not-serving-lake` |
| G3 join rule + fan-out guard | yes (high) | `packages/executor/dms_executor/generative_ask.py:1407-1428 - plan is compiled by _compile_maybe_unverified -> Ontology.compile; a Refusal, a non-Comp...`<br>`packages/executor/dms_executor/ontology.py:1432,1488 - group_by and filters resolve through _resolve_path; :1061-1071 fanout_refused (group_by); :152...` |  |
| G4 typed ingest | part (low) | `packages/executor/dms_executor/__init__.py:665 - reads only granted intersect DEMO_TABLES`<br>`packages/executor/dms_executor/demo_warehouse.py:292-430 - declared DDL on a seeded fixture, not typed ingest` | `cortex-lake-column-types-unread-by-dms` |
| G5 PII mask (envelope + record) | part (low) | `packages/executor/dms_executor/envelope.py:1626-1639,1739 and packages/executor/dms_executor/__init__.py:508-510 - envelope fields masked at build an...`<br>`packages/executor/dms_executor/generative_ask.py:845,1453-1470 and packages/executor/dms_executor/__init__.py:467-477 - ledger payload carries the ra...` | `ledger-append-carries-unmasked-sql` |
| G6 strict pin + served fields | part (medium) | `packages/cortex_client/cortex_client/compute.py:838-840,728-730 and packages/cortex_client/cortex_client/strict_pin.py:118-127 - pinned model and str...`<br>`packages/cortex_client/cortex_client/compute.py:940-949 - ranked retry leg is pinned (copies insights_body)` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | part (medium) | `packages/executor/dms_executor/generative_ask.py:818-829 and packages/executor/dms_executor/__init__.py:441-453,816-837 - SQL executed by Cortex /v1/...`<br>`packages/executor/dms_executor/generative_ask.py:843-862 and packages/executor/dms_executor/__init__.py:455-478 - ledger append through Cortex with e...` | `generative-keep-gt-rows-not-in-ledgered-sql` |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/generative_ask.py:1360-1446 - untyped plan, unhonored qualifier, ontology_unverified, touched failed subject, compile ...`<br>`packages/executor/dms_executor/generative_ask.py:830-862 - submit failure, keep_gt_empty and ledger failures are named` | `certified-lane-failure-not-named` |

Also on this path: packages/executor/dms_executor/semantic_retrieve.py:735-790 - the 'ontology_plan from ranking' slots are produced by calling bind_plan (:754-755), the keyword binder; plan_source is stamped ontology_plan while the filters and group_by are keyword-bound

Also on this path: packages/executor/dms_executor/generative_ask.py:830-842 - after keep_gt the result is a SimpleNamespace with only the kept rows; the Cortex row count is lost from the envelope

Also on this path: packages/executor/dms_executor/semantic_retrieve.py:291-357,514-536 - ontology slice and extra column names sent to Insights are whole-lake metadata, not grant-limited

<a id="ep-chat-ask-generative-named-abstain"></a>
### `chat-ask:generative-named-abstain`

GEN-01 named fail-closed abstains after the Insights call. Producer: packages/executor/dms_executor/generative_ask.py:670 _abstain and packages/executor/dms_executor/envelope.py:1830 reserved_as_of_abstain, reached from maybe_generative_ask :1149-1446, _submit_validat.... Reachable: yes, on default config, but narrowly. Product or generative ladder, no grounded_tables: _live_ask -> maybe_generative_ask (__init__.py:728-763). With the default cortex_api_key 'dms-demo-viewer-key' (settings.py:36) com...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/__init__.py:657-666 - grantable_tables (shared grant logic) with except -> []; readable = requested or default_readable`<br>`packages/executor/dms_executor/__init__.py:731-742 - grantable=set(readable) passed in` | `no-space-ask-widest-grant` |
| G2 ontology verification | n/a (medium) | `packages/executor/dms_executor/generative_ask.py:679-695 - ABSTAIN rows=[], sql_used=None`<br>`packages/executor/dms_executor/generative_ask.py:1106,1360-1366,1384-1396 - ontology verified, and an unverified ontology or compile refusal abstains` |  |
| G3 join rule + fan-out guard | n/a (medium) | `packages/executor/dms_executor/generative_ask.py:679-695 - sql_used=None, rows=[]`<br>`packages/executor/dms_executor/envelope.py:1681-1686 - demotions clear rows/values; SQL that ran is shown as text only` |  |
| G4 typed ingest | n/a (medium) | `packages/executor/dms_executor/__init__.py:665 - readable is granted intersect DEMO_TABLES (uploads excluded)`<br>`packages/executor/dms_executor/generative_ask.py:1062 - grounded tables short-circuit before any read` |  |
| G5 PII mask (envelope + record) | part (medium) | `packages/executor/dms_executor/envelope.py:1626-1640,1739 - text, assumptions and other keys are masked`<br>`packages/core/dms_core/pii.py:201-209,1032-1044 - audit_receipt is in _HANDLED_KEYS, so mask_unknown_keys skips it` | `audit-receipt-and-late-added-cards-skip-mask` |
| G6 strict pin + served fields | part (medium) | `packages/cortex_client/cortex_client/compute.py:814-840,710-757 - strict model+header stamped on generate; only pin_unavailable is turned into a name...`<br>`packages/cortex_client/cortex_client/compute.py:755-767 and generative_ask.py:1298-1311 - pin_unavailable:/pin_caller_error: reach the envelope as a ...` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | n/a (medium) | `packages/executor/dms_executor/generative_ask.py:679-695 - ABSTAIN, no figure/rows`<br>`packages/executor/dms_executor/generative_ask.py:818-862 - submit failure, no output, keep_gt empty, ledger error, missing entry id or hash each abst...` |  |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/generative_ask.py:1149-1446 and :783-862 - every listed branch returns _abstain with a reason in assumptions[0]; empty...`<br>`packages/executor/dms_executor/gen_path_refuse.py:24-75 - GAP_REASONS appear in the sentence; others render a generic sentence` | `certified-lane-failure-not-named` |

Also on this path: packages/executor/dms_executor/generative_ask.py:637-663,1228-1301 - the kind=='sql' branch serves model-written SQL after validate_compiled_sql (hostile + grant + EXPLAIN only). There is no join-rule or fan-out check (fanout_refused exists only in ontology.py/Ontology.compile), and no ontology ver...

Also on this path: packages/executor/dms_executor/generative_ask.py:1106 - ontology verified against DMS's own thin demo file, answers execute on Cortex's warehouse; split-layout mismatch risk (BRONZE-WAREHOUSE-01 area).

Also on this path: packages/executor/dms_executor/semantic_retrieve.py:228-262,470-483 - DISTINCT sample values and bound_values are sent to Cortex/model before any PII control beyond a name regex and column_is_pii; open #272.

<a id="ep-chat-ask-harness-generative-miss"></a>
### `chat-ask:harness-generative-miss`

GEN-03 isolated 'generative' ladder miss. Producer: packages/executor/dms_executor/generative_ask.py:915 path_miss_envelope; called at packages/executor/dms_executor/__init__.py:764-775. Reachable: flag-gated. Needs DMS_HARNESS_ASK_PATHS=1 (settings.py:50), otherwise 400 (chat.py:227-238). UI and MCP cannot send ask_path. Path: _live_ask ladder 'generative' (certified_first=False, allow_gen only, __init__.py:545-5...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/__init__.py:657-666 - granted via grantable_tables inside try/except (failure -> []), readable = requested or default_...`<br>`packages/executor/dms_executor/__init__.py:731-742 - maybe_generative_ask gets grantable=set(readable)` | `no-space-ask-widest-grant` |
| G2 ontology verification | n/a (high) | `packages/executor/dms_executor/__init__.py:764-774 - the miss envelope is a static ABSTAIN`<br>`packages/executor/dms_executor/generative_ask.py:679-695 - rows=[], sql_used=None` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/__init__.py:764-774 - miss returns an ABSTAIN with no SQL`<br>`packages/executor/dms_executor/generative_ask.py:679-695 - sql_used=None` |  |
| G4 typed ingest | n/a (medium) | `packages/executor/dms_executor/generative_ask.py:1062,1106,1127 - reads on this lane are verify/EXPLAIN/DISTINCT samples over DEMO_TABLES only (decla...` |  |
| G5 PII mask (envelope + record) | yes (medium) | `packages/executor/dms_executor/__init__.py:765-774 - fixed reason string, stored via _store_turn (pops on abstain, :237-247)`<br>`packages/executor/dms_executor/envelope.py:1626-1640,1739 - masking in the constructor` |  |
| G6 strict pin + served fields | part (medium) | `packages/cortex_client/cortex_client/compute.py:814-840 - _insights_body stamps model+strict via stamp_generate_body`<br>`packages/cortex_client/cortex_client/compute.py:710-757 - _insights_generate_post sends strict header; only a 503 pin_unavailable (kind 'unavailable'...` | `served-pin-checked-only-in-offline-scorer` |
| G7 Cortex submit (manifest + ledger) | n/a (high) | `packages/executor/dms_executor/__init__.py:764-774 - ABSTAIN, no figure or rows` |  |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/__init__.py:764-774 - miss is a named ABSTAIN, reason carried in assumptions`<br>`packages/executor/dms_executor/generative_ask.py:1062 - tables set returns None, so no Insights call happens, yet the miss text says 'Insights return...` | `ungranted-tick-dropped-not-refused` |

Also on this path: packages/executor/dms_executor/__init__.py:765-770 - miss reason text 'Insights returned no SQL and no ranking' is asserted even when no Insights call was made (grounded tables, bearer refuse, pre-call). The miss reason is not truthful for those cases.

Also on this path: packages/executor/dms_executor/semantic_retrieve.py:228-262,470-483 - DISTINCT sample values (non-PII-named columns) and bound_values leave for Cortex/model in the Insights context. Name-regex plus column_is_pii only; tracked by open #272 (PII-01).

Also on this path: packages/executor/dms_executor/generative_ask.py:1106 - ontology is verified against self._warehouse (DMS thin demo file, demo_warehouse.py:264-289), while answers execute on Cortex's own warehouse; verify and execution target different databases (relevant to BRONZE-WAREHOUSE-01 split layout).

<a id="ep-chat-ask-cortex-contract-ask"></a>
### `chat-ask:cortex-contract-ask`

Cortex /v1/contract/ask fallthrough mapped to the DMS envelope. Producer: packages/executor/dms_executor/__init__.py:776-814 (demo_acl + bind_session + packages/cortex_client/cortex_client/client.py:114-121 ask -> generated/api/contract/ask.py:28 '/v1/contract/ask') -> map.... Reachable: yes - default config. POST /v1/chat/ask (apps/api/dms_api/routes/chat.py:213) and POST /v1/mcp/call name=ask (apps/api/dms_api/routes/mcp.py:100-133 calls chat_ask) with dms_ask_mode='live', dms_demo_fallback=False, har...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (high) | `packages/executor/dms_executor/__init__.py:776-778 - demo_acl then bind_session run on this path before the Cortex ask`<br>`packages/executor/dms_executor/__init__.py:297-328 - grantable_tables: a Space goes through intersect_space_grants (318-328); NO Space returns DEMO_T...` | `no-space-ask-widest-grant` |
| G2 ontology verification | **NO** (high) | `packages/executor/dms_executor/__init__.py:776-814 - the lane calls bind_session, cortex.ask, map_ask_response_to_envelope; no load_verified_ontology...`<br>`packages/executor/dms_executor/__init__.py:894-1047 - map_ask_response_to_envelope passes Cortex sql_used/rows/values through; no ontology argument` | `served-sql-never-reverified-against-ontology` |
| G3 join rule + fan-out guard | **NO** (medium) | `packages/executor/dms_executor/ontology.py:1063 and packages/executor/dms_executor/gen_path_refuse.py:34 - the only fanout_refused sites; used by the...`<br>`packages/executor/dms_executor/__init__.py:1007-1012,1022-1039 - Cortex sql_used is carried to the envelope unchanged` | `free-sql-lanes-no-fanout-guard` |
| G4 typed ingest | part (medium) | `packages/executor/dms_executor/demo_warehouse.py:302-345,347-360 - seeded demo tables carry declared column types (DOUBLE, DATE, BOOLEAN), the defaul...`<br>`packages/executor/dms_executor/bronze.py:401-410 - CSV bronze uses DuckDB read_csv auto_detect` | `cortex-lake-column-types-unread-by-dms` |
| G5 PII mask (envelope + record) | part (medium) | `packages/executor/dms_executor/envelope.py:1611-1640 - text, rows, values, sources, chart, sql_used pass fail_closed_mask_payload`<br>`packages/executor/dms_executor/envelope.py:1739 and packages/executor/dms_executor/__init__.py:508-510 - mask_unknown_keys scans assumptions, suggest...` | `audit-receipt-and-late-added-cards-skip-mask` |
| G6 strict pin + served fields | **NO** (high) | `packages/cortex_client/cortex_client/client.py:114-121 and packages/cortex_client/cortex_client/models.py:14-18 - ask() sends only question/session_i...`<br>`packages/cortex_client/cortex_client/insights.py:257-259 - strict pin is stamped only on the Insights generate call; grep shows strict_pin used only ...` | `contract-ask-model-call-unpinned-and-stamped-none` |
| G7 Cortex submit (manifest + ledger) | part (medium) | `packages/executor/dms_executor/manifest.py:165-196 and packages/executor/dms_executor/__init__.py:392-439 - signed manifest minted (canonical_manifes...`<br>`packages/executor/dms_executor/__init__.py:780-800 - the answer itself is cortex.ask (/v1/contract/ask, contract/openapi-1.2.0.json 'Requires a prior...` | `contract-ask-answer-not-submitted-or-ledgered-by-dms` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/chat.py:277-311,317-335,336-354 - GroundingRefused 403, outside_grounded_scope 403, Space refusal ABSTAIN envelope, status-ma...`<br>`packages/executor/dms_executor/__init__.py:913-961 - refused/unsure/unknown badge forced to ABSTAIN with named text for unknown badge` | `rule12-aggregate-zero-row-green` |

Also on this path: packages/executor/dms_executor/envelope.py:1313-1314 + packages/executor/dms_executor/session_followup.py:39-64 - row_count placeholder values are numeric and enter the follow-up turn cache (numeric_values does not filter label row_count), so 'average of them' / 'add N' after a text-only answer com...

Also on this path: packages/executor/dms_executor/__init__.py:193 - _turns cache is an unbounded dict keyed by client-supplied session_id, never evicted until close()

Also on this path: apps/api/dms_api/routes/chat.py:336-340 - generic-exception demo fallback also catches SubmitError path_not_allowed from the VQ lane (__init__.py:850), so with DMS_DEMO_FALLBACK=1 a Space-boundary refusal is answered with demo numbers (bannered); with the flag off it is a 503 live_ask_failed that r...

<a id="ep-chat-ask-cortex-doc-retrieval"></a>
### `chat-ask:cortex-doc-retrieval`

Cortex contract-ask document-retrieval (L3 quote) answer. Producer: packages/executor/dms_executor/__init__.py:894 map_ask_response_to_envelope (doc stub :1006-1012) -> packages/executor/dms_executor/envelope.py:1340 build_answer_envelope E9 branch :1423-1445 (_cited.... Reachable: yes in DMS code under default config (same outer routes as the contract ask: chat.py:213, mcp.py:100-133). It fires only when Cortex returns a non-refused answer with no sql_used and a route in _DOC_ROUTE_KINDS (envelop...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (low) | `packages/executor/dms_executor/__init__.py:776-778 - same demo_acl/bind; manifest is table-only (row_predicates, allowed_paths=[] at :386-387), it na...`<br>`packages/executor/dms_executor/__init__.py:780-786 - the only doc scoping DMS sends is the space_id string in AskRequest` | `read-routes-own-space-check-not-shared-grant` |
| G2 ontology verification | n/a (medium) | `packages/executor/dms_executor/__init__.py:1006-1012 - no sql_used becomes the stub '-- document retrieval (no SQL)'`<br>`packages/executor/dms_executor/envelope.py:942-977,1426-1445 - figures survive only as literals found in cited snippets ('quoted'); uncited ones demo...` |  |
| G3 join rule + fan-out guard | n/a (medium) | `packages/executor/dms_executor/__init__.py:1008-1010 - the lane exists only when sql_used is empty and is stamped with the no-SQL comment`<br>`packages/executor/dms_executor/envelope.py:797-806,1426 - _executed_query is False for the stub; E9 treats it as no executed query` |  |
| G4 typed ingest | n/a (medium) | `packages/executor/dms_executor/__init__.py:1008-1010 - no SQL, stub only`<br>`packages/executor/dms_executor/document_chunks.py:1-50 - documents are text extracted and chunked, no column typing` |  |
| G5 PII mask (envelope + record) | part (medium) | `packages/executor/dms_executor/envelope.py:1384-1388,1626-1640 - text, rows, values and sources (snippet, container, origin_uri) pass fail_closed_mas...`<br>`packages/core/dms_core/pii.py:201-212,1032-1043 - audit_receipt is skipped by mask_unknown_keys` | `audit-receipt-and-late-added-cards-skip-mask` |
| G6 strict pin + served fields | **NO** (medium) | `packages/cortex_client/cortex_client/client.py:114-121 and models.py:14-18 - ask() carries no pin`<br>`packages/cortex_client/cortex_client/models.py:21-44 - AskResponse has no served_* fields` | `contract-ask-model-call-unpinned-and-stamped-none` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/__init__.py:1006-1012 - stub '-- document retrieval (no SQL)': no query executed, no submit`<br>`packages/executor/dms_executor/envelope.py:942-977 - values[] entries labelled 'quoted' come from prose matched to snippets, not from SQL` | `contract-ask-answer-not-submitted-or-ledgered-by-dms` |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/envelope.py:1426-1445 - uncited figures give named ABSTAIN`<br>`packages/executor/dms_executor/envelope.py:1467-1482 - no sources/token or non-doc route with no rows gives ABSTAIN (E9-03) but its text says 'Name a...` | `e9-bare-integer-uncited-in-doc-lane` |

Also on this path: packages/executor/dms_executor/envelope.py:845-859,995 - E9/E4 only treat figures containing ',' or '.' as figures; bare integers (12500, 47) in doc prose are never checked against snippets, so a computed integer total can be served

Also on this path: packages/executor/dms_executor/envelope.py:31-43 and apps/ui/src/components/AnswerMessage.tsx:61-62 - doc answers map to L2_VALIDATED, whose UI headline is 'This SQL was generated, then checked.' although no SQL ran

Also on this path: packages/executor/dms_executor/envelope.py:1313-1314 + session_followup.py:39-64 - doc answers carry a v_count=0 row_count placeholder that enters the follow-up turn cache, so 'add 5' / 'average of them' computes over the placeholder

<a id="ep-chat-ask-space-refusal"></a>
### `chat-ask:space-refusal`

Space-boundary refusal rendered as an ABSTAIN envelope. Producer: apps/api/dms_api/routes/chat.py:153 _space_refusal_envelope -> apps/api/dms_api/wiring.py:118-122 build_validated_envelope. Reachable: yes - default config. Requires body.space_id set (and present in the SpaceStore, chat.py:240), grounded_tables empty, and live_ask raising AskServiceError('path_not_allowed') from bind_session (packages/executor/dms_exe...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | yes(2/3) (medium) | `packages/executor/dms_executor/__init__.py:776-778 (and :450-452 for the VQ bind) - demo_acl then bind_session run before the call that raises`<br>`packages/executor/dms_executor/__init__.py:318-328,360 - Space grants resolved through intersect_space_grants/resolve_session_acl; the manifest row_p...` |  |
| G2 ontology verification | n/a (high) | `apps/api/dms_api/routes/chat.py:170-182 - ABSTAIN envelope built from constants, no values, rows or SQL`<br>`packages/executor/dms_executor/envelope.py:1681-1686,1884-1888 - abstained forces values, rows, sources and token empty (E2)` |  |
| G3 join rule + fan-out guard | n/a (high) | `apps/api/dms_api/routes/chat.py:170-182 - no sql_used passed`<br>`packages/executor/dms_executor/envelope.py:1694-1699,1707 - abstained envelope carries sql_used None; no SQL can be served` |  |
| G4 typed ingest | n/a (medium) | `apps/api/dms_api/routes/chat.py:166-182 - the answer is built from constants plus the Space name and a table name parsed from the error`<br>`packages/executor/dms_executor/bronze.py:259-269 - the only read in envelope build is the ingest registry for source watermarks (metadata, sources is...` |  |
| G5 PII mask (envelope + record) | yes (high) | `apps/api/dms_api/wiring.py:118-122 - build_validated_envelope -> build_answer_envelope`<br>`packages/executor/dms_executor/envelope.py:1626-1640,1739 - text and every other key pass the masker, including the Space name and table name taken f...` |  |
| G6 strict pin + served fields | **NO** (medium) | `apps/api/dms_api/routes/chat.py:170-182,320-325 - envelope built outside live_ask`<br>`packages/executor/dms_executor/__init__.py:504-510 - served_attribution is stamped only on live_ask's return, which the exception skips` | `space-refusal-envelope-no-served-attribution` |
| G7 Cortex submit (manifest + ledger) | n/a (high) | `apps/api/dms_api/routes/chat.py:176-182 - badge ABSTAIN, abstained=True, no values or rows`<br>`packages/executor/dms_executor/envelope.py:1681-1686,1884-1888 - abstain empties values, rows, sources and token` |  |
| G8 named abstain on failure | yes (medium) | `apps/api/dms_api/routes/chat.py:317-325 - path_not_allowed with a Space returns an ABSTAIN envelope whose reason is named in text and in assumptions[...`<br>`apps/api/dms_api/routes/chat.py:296-311 - with grounded_tables the same error is a named 403 outside_grounded_scope` |  |

Also on this path: packages/executor/dms_executor/__init__.py:850 vs apps/api/dms_api/routes/chat.py:291-311 - the same Space-boundary refusal arriving as SubmitError (VQ lane _submit_verified_sql, verified_queries.py:339) is not rendered as this envelope: 503 live_ask_failed (reads as an outage) or, with DMS_DEMO_FA...

Also on this path: packages/executor/dms_executor/demo_grants.py:143-146 with apps/api/dms_api/store/memory.py:55-57 - a Space created via POST /v1/spaces is unknown to the grant seed, so every ask is refused as 'needs <table> data, which this Space has no access to' even for a granted table; fails closed but misattr...

Also on this path: apps/api/dms_api/routes/chat.py:170-171 and envelope.py:1712 - answer_id is 'ans_refused_<space[:8]>' and audit_id falls back to it: every refusal in a Space shares one audit id and writes no ledger event

<a id="ep-chat-ask-demo-mode"></a>
### `chat-ask:demo-mode`

Demo ask mode (DMS_ASK_MODE=demo) keyword router. Producer: packages/executor/dms_executor/demo_ask.py:281 answer_demo_question via packages/executor/dms_executor/__init__.py:288-290 Executor.demo_ask. Reachable: flag-gated: DMS_ASK_MODE=demo (apps/api/dms_api/settings.py:40, default 'live'); branch at apps/api/dms_api/routes/chat.py:249-255. The MCP twin POST /v1/mcp/call name=ask (apps/api/dms_api/routes/mcp.py:100,132) is rea...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | **NO** (high) | `apps/api/dms_api/routes/chat.py:240-241 - the only Space interaction is store.get(space_id) is None -> 404; no table-level grant`<br>`apps/api/dms_api/routes/chat.py:249-255 - the demo branch returns ask.demo_ask(question, space_id=...) before any grounding or grant code; grounded_t...` | `demo-lane-bypasses-all-gates` |
| G2 ontology verification | **NO** (high) | `packages/executor/dms_executor/demo_ask.py:337-343 - revenue is a literal SUM(quantity_kg * unit_cost_myr) WHERE txn_type='outbound'`<br>`packages/executor/dms_executor/demo_ask.py:368-375 - the 'divide by N' figure is Python total / factor over the same literal` | `demo-lane-bypasses-all-gates` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/demo_ask.py:338-341, 371-374, 424-432, 476-481, 527-532, 569-575 - every statement is a hard-coded template over exact...`<br>`packages/executor/dms_executor/demo_ask.py:431 - the only user-derived parts are int(n) and int(offset); :101-110 a float factor; :218-249 and :422 S...` |  |
| G4 typed ingest | yes (medium) | `packages/executor/dms_executor/demo_warehouse.py:422-430 - transactions DDL declares quantity_kg DOUBLE, unit_cost_myr DOUBLE, ts TIMESTAMP`<br>`packages/executor/dms_executor/demo_warehouse.py:350-359, 400-406 - inventory and alerts declare DOUBLE and BOOLEAN columns` |  |
| G5 PII mask (envelope + record) | yes (high) | `packages/executor/dms_executor/demo_ask.py:53-61 - _pack builds every demo envelope through build_answer_envelope`<br>`packages/executor/dms_executor/envelope.py:1626-1640 - fail_closed_mask_payload over text, rows, values, sources, chart and sql_used` |  |
| G6 strict pin + served fields | **NO** (high) | `packages/executor/dms_executor/__init__.py:288-290 - demo_ask path; it does not go through live_ask`<br>`packages/executor/dms_executor/__init__.py:480-510 - with_served_attribution is applied only in live_ask (:506)` | `demo-lane-bypasses-all-gates` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/demo_warehouse.py:493 - the figure is computed by con.execute inside DMS (execute_sql)`<br>`packages/executor/dms_executor/demo_ask.py:342, 370 - total_outbound_revenue runs locally and the 'divided by N' figure is Python arithmetic` | `demo-lane-bypasses-all-gates` |
| G8 named abstain on failure | part (high) | `packages/executor/dms_executor/demo_ask.py:287-288, 334, 434-435, 629-668 - predictive, unrecognised and empty-rank asks return ABSTAIN with an assum...`<br>`packages/executor/dms_executor/envelope.py:1447-1461 - hard rule 12 demotes an executed-but-empty result to ABSTAIN` | `demo-lane-bypasses-all-gates` |

Also on this path: packages/executor/dms_executor/demo_ask.py:34,41 - _SOURCES hard-codes row_count 10 for transactions and 3 for locations, but the seed holds 15 transactions rows (demo_warehouse.py:422+) and 5 locations rows (:304+). contribution 1.0 and 0.0 are fixed regardless of the tables read; the Sources pane...

Also on this path: packages/executor/dms_executor/demo_warehouse.py:280-294 - ensure_demo_warehouse DROPs the six demo-named tables on the first call per process (also from Executor.startup, __init__.py:209). A DMS_WAREHOUSE_DB holding real transactions, inventory or similar tables would lose them. The guard is a doc...

Also on this path: apps/api/dms_api/routes/chat.py:253-255 - the demo-mode envelope sets neither demo_fallback_used nor demo_fallback_banner (E6 not engaged). The banner depends on the UI reading /health (DemoFallbackBanner.tsx:5-6); an API or MCP caller sees badge L2_VALIDATED and must read ask_mode.

<a id="ep-chat-ask-demo-fallback-no-cortex"></a>
### `chat-ask:demo-fallback-no-cortex`

DMS_DEMO_FALLBACK when the Cortex client is missing. Producer: packages/executor/dms_executor/demo_ask.py:281 answer_demo_question, restamped by apps/api/dms_api/routes/chat.py:185 _stamp_demo_fallback. Reachable: flag-gated and not reachable under a normal lifespan: needs DMS_DEMO_FALLBACK=1 (settings.py:43, default False) AND app.state.cortex None. create_app sets None (app.py:123) but lifespan always installs a CortexClient (a...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | **NO** (high) | `apps/api/dms_api/routes/chat.py:240-241 - only a Space-exists 404`<br>`apps/api/dms_api/routes/chat.py:260-263 - cortex None -> ask.demo_ask(question, space_id) with no grounding or grant step` | `demo-lane-bypasses-all-gates` |
| G2 ontology verification | **NO** (high) | `packages/executor/dms_executor/demo_ask.py:337-343 - literal revenue measure`<br>`packages/executor/dms_executor/demo_ask.py:475-481 - literal utilisation ratio` | `demo-lane-bypasses-all-gates` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/demo_ask.py:338-341, 371-374, 424-432, 476-481, 527-532, 569-575 - the only SQL this path can serve is these single-ta...`<br>`apps/api/dms_api/routes/chat.py:185-210 - _stamp_demo_fallback copies sql_used from the demo envelope and adds none of its own` |  |
| G4 typed ingest | yes (medium) | `packages/executor/dms_executor/demo_warehouse.py:422-430 - declared DOUBLE and TIMESTAMP types on transactions`<br>`packages/executor/dms_executor/demo_ask.py:338-341 - SQL relies on them with no repair cast` |  |
| G5 PII mask (envelope + record) | yes (high) | `apps/api/dms_api/routes/chat.py:189-210 - _stamp_demo_fallback rebuilds the envelope through build_validated_envelope`<br>`apps/api/dms_api/wiring.py:118-122 - build_validated_envelope calls dms_executor.build_answer_envelope` |  |
| G6 strict pin + served fields | **NO** (high) | `apps/api/dms_api/routes/chat.py:189-210 - the rebuilt fallback envelope carries no served_attribution, served_provider or served_model`<br>`packages/executor/dms_executor/__init__.py:506 - with_served_attribution runs only inside live_ask, which this branch never calls` | `demo-lane-bypasses-all-gates` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/demo_warehouse.py:493 - local DuckDB execute`<br>`apps/api/dms_api/routes/chat.py:260-263 - cortex is None, so no submit or ledger_append is possible on this branch` | `demo-lane-bypasses-all-gates` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/chat.py:264-267 - flag off: named 503 cortex_unavailable`<br>`apps/api/dms_api/routes/chat.py:263 - flag on: demo numbers with assumption 'fallback - Cortex client missing', ask_mode 'demo', demo_fallback_banner...` | `demo-lane-bypasses-all-gates` |

Also on this path: apps/api/dms_api/routes/chat.py:262 - the fallback passes only question and space_id. body.grounded_tables and session_id are dropped, so a question grounded in a user file is answered from the demo warehouse, and the envelope reports session_id None and grounded_tables [] (_stamp_demo_fallback, :1...

Also on this path: apps/api/dms_api/routes/chat.py:257-258 - with cortex None the gate returns gate_unavailable, a soft reason; the path proceeds with no compliance decision at all.

<a id="ep-chat-ask-demo-fallback-ask-error"></a>
### `chat-ask:demo-fallback-ask-error`

DMS_DEMO_FALLBACK after a non-policy AskServiceError. Producer: packages/executor/dms_executor/demo_ask.py:281 + apps/api/dms_api/routes/chat.py:185 _stamp_demo_fallback. Reachable: flag-gated: DMS_DEMO_FALLBACK=1 (settings.py:43, default False) and live_ask raising AskServiceError whose code is outside _POLICY_CODES and was not handled as the grounded 403 or the Space refusal (chat.py:291-331). De...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/__init__.py:776 - the live attempt resolves the Space grants via demo_acl (:330) -> grantable_tables (:297) before bin...`<br>`apps/api/dms_api/routes/chat.py:277-290 - GroundingRefused is handled before the fallback and never reaches it` | `demo-fallback-converts-live-refusal-to-demo-numbers` |
| G2 ontology verification | **NO** (high) | `apps/api/dms_api/routes/chat.py:330 - the served figure comes from the demo router`<br>`packages/executor/dms_executor/demo_ask.py:337-343 - literal measure` | `demo-lane-bypasses-all-gates` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/demo_ask.py:338-341, 371-374, 424-432, 476-481, 527-532, 569-575 - single-table templates are the only SQL the fallbac...`<br>`apps/api/dms_api/routes/chat.py:185-210 - the stamp copies sql_used and adds none` |  |
| G4 typed ingest | yes (medium) | `packages/executor/dms_executor/demo_warehouse.py:422-430 - declared column types`<br>`packages/executor/dms_executor/demo_ask.py:338-341 - relied on without casts` |  |
| G5 PII mask (envelope + record) | yes (medium) | `apps/api/dms_api/routes/chat.py:330-331 - fallback envelope built by _stamp_demo_fallback -> build_validated_envelope`<br>`packages/executor/dms_executor/envelope.py:1626-1640, 1739 - masking of text/rows/values/sources/chart/sql_used and unknown keys` |  |
| G6 strict pin + served fields | **NO** (high) | `apps/api/dms_api/routes/chat.py:189-210 - the fallback envelope has no served_* fields`<br>`packages/executor/dms_executor/__init__.py:506 - with_served_attribution is skipped because live_ask raised before returning` | `demo-lane-bypasses-all-gates` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/demo_warehouse.py:493 - local DuckDB execute`<br>`apps/api/dms_api/routes/chat.py:330-331 - the live Cortex path that failed is replaced by a local demo answer` | `demo-lane-bypasses-all-gates` |
| G8 named abstain on failure | **NO** (high) | `apps/api/dms_api/routes/chat.py:34-49 vs 71-77 - _POLICY_CODES omits sql_not_analyzable, manifest_malformed and manifest_not_yet_valid, which _STATUS...`<br>`apps/api/dms_api/routes/chat.py:328-331 - any code outside _POLICY_CODES is answered with demo numbers; the note names the code` | `demo-fallback-converts-live-refusal-to-demo-numbers` |

Also on this path: apps/api/dms_api/routes/chat.py:34-49 vs 71-77 - three 403 codes missing from _POLICY_CODES (sql_not_analyzable, manifest_malformed, manifest_not_yet_valid). Same defect as the G8 cell; recorded once for the fix.

Also on this path: apps/api/dms_api/routes/chat.py:330 - body.grounded_tables is not passed to demo_ask and _stamp_demo_fallback (:189-210) does not carry grounded_tables, so a question grounded in a user's file is silently answered from the demo warehouse.

Also on this path: apps/api/dms_api/routes/chat.py:334 - when the fallback is not taken, exc.detail (raw engine text) is returned to the client unmasked in HTTPException detail.message.

<a id="ep-chat-ask-demo-fallback-exception"></a>
### `chat-ask:demo-fallback-exception`

DMS_DEMO_FALLBACK after any other live_ask exception. Producer: packages/executor/dms_executor/demo_ask.py:281 + apps/api/dms_api/routes/chat.py:185 _stamp_demo_fallback. Reachable: flag-gated: DMS_DEMO_FALLBACK=1 (settings.py:43, default False) and live_ask raising a non-AskServiceError, non-GroundingRefused exception (chat.py:336-340). Default config returns 503 live_ask_failed or 504 live_ask_ti...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `packages/executor/dms_executor/__init__.py:776 - the live path resolves Space grants via demo_acl when it reaches the contract ask`<br>`packages/executor/dms_executor/__init__.py:539-540 - RuntimeError 'CortexClient required for live_ask' is raised before any grant resolution` | `demo-fallback-converts-live-refusal-to-demo-numbers` |
| G2 ontology verification | **NO** (high) | `apps/api/dms_api/routes/chat.py:339 - served figure comes from the demo router`<br>`packages/executor/dms_executor/demo_ask.py:337-343 - literal measure semantics, no ontology load` | `demo-lane-bypasses-all-gates` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/demo_ask.py:338-341, 371-374, 424-432, 476-481, 527-532, 569-575 - only single-table templates can be served`<br>`apps/api/dms_api/routes/chat.py:185-210 - the stamp adds no SQL` |  |
| G4 typed ingest | yes (medium) | `packages/executor/dms_executor/demo_warehouse.py:422-430 - declared column types`<br>`packages/executor/dms_executor/demo_ask.py:338-341 - relied on without casts` |  |
| G5 PII mask (envelope + record) | part (medium) | `apps/api/dms_api/routes/chat.py:339-340 - fallback envelope is built via _stamp_demo_fallback -> build_answer_envelope, masked at packages/executor/d...`<br>`apps/api/dms_api/routes/chat.py:338 - logger.warning('live ask failed: %s; demo fallback', exc) writes str(exc) of an arbitrary exception, unmasked` | `demo-lane-bypasses-all-gates` |
| G6 strict pin + served fields | **NO** (high) | `apps/api/dms_api/routes/chat.py:339-340 - the fallback envelope has no served_* fields`<br>`packages/executor/dms_executor/__init__.py:506 - with_served_attribution is skipped because live_ask raised` | `demo-lane-bypasses-all-gates` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/demo_warehouse.py:493 - local DuckDB execute`<br>`apps/api/dms_api/routes/chat.py:339-340 - the failed live path is replaced by a local demo answer` | `demo-lane-bypasses-all-gates` |
| G8 named abstain on failure | **NO** (high) | `apps/api/dms_api/routes/chat.py:336-340 - every non-AskServiceError is answered with L2 demo numbers; the note 'fallback - live ask failed' names no ...`<br>`packages/executor/dms_executor/verified_queries.py:347 - submit() raises SubmitError (manifest.py:227, a plain Exception), uncaught, so a policy refu...` | `demo-fallback-converts-live-refusal-to-demo-numbers` |

Also on this path: packages/executor/dms_executor/verified_queries.py:347-355 - submit() and ledger_append() are uncaught: a SubmitError (plain Exception) or ledger error leaves live_ask as a non-AskServiceError. With the fallback off, a VQ-lane Space-boundary refusal (path_not_allowed) therefore arrives as 503 live_...

Also on this path: apps/api/dms_api/routes/chat.py:351 - flag off: str(exc)[:400] is returned in the 503 body unmasked (raw exception text to the client).

Also on this path: apps/api/dms_api/routes/chat.py:339 - grounded_tables and session_id are dropped by the fallback, same as the other two fallback paths.

<a id="ep-chat-drillthrough"></a>
### `chat:drillthrough`

Drillthrough contributing rows. Producer: apps/api/dms_api/routes/chat.py:377-381 -> packages/cortex_client/cortex_client/client.py:268-275 drillthrough (generated/api/contract/drillthrough.py:28 '/v1/contract/drillthrough'). Reachable: yes - POST /v1/chat/drillthrough; chat router mounted unconditionally (apps/api/dms_api/app.py:146). Needs Cortex configured (else 503 cortex_unavailable, chat.py:372-376) and a Cortex-minted drillthrough_token (executo...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/chat.py:136-137 - DrillthroughBody carries only `token`; no space_id or session.`<br>`apps/api/dms_api/routes/chat.py:377-381 - cortex.drillthrough(DrillthroughRequest(token=...)); no grantable_tables, intersect_space_grants, table_is_...` | `drillthrough-token-is-the-only-control` |
| G2 ontology verification | n/a (low) | `packages/cortex_client/cortex_client/models.py:116-122 - response is rows, sql_used, source_table, answer_id: detail rows, no computed figure.`<br>`DMS_TECHNICAL_ARCHITECTURE.md:236-239 - design strips aggregates and keeps their arguments, so no measure is computed on this path.` |  |
| G3 join rule + fan-out guard | **NO** (low) | `DMS_TECHNICAL_ARCHITECTURE.md:239 - design: drill-through preserves 'every join - unchanged'; so multi-table SQL is re-run.`<br>`apps/api/dms_api/routes/chat.py:377-381 - token only; no fanout_refused or Ontology._resolve_path call on this path.` | `drillthrough-token-is-the-only-control` |
| G4 typed ingest | **NO** (low) | `packages/executor/dms_executor/__init__.py:360-367 - demo_acl admits selected uploaded bronze tables into the manifest, so a token can cover them.`<br>`packages/executor/dms_executor/bronze.py:491 - SQL-pull bronze copies are all-VARCHAR.` | `drillthrough-token-is-the-only-control` |
| G5 PII mask (envelope + record) | part (medium) | `apps/api/dms_api/routes/chat.py:380-381 - resp.model_dump(mode='json') returned raw: rows and sql_used unmasked, no envelope, no mask_payload.`<br>`packages/core/dms_core/pii.py:182 - drillthrough_token is on the keep list; no masker on this route.` | `preview-and-drill-rows-unmasked` |
| G6 strict pin + served fields | n/a (medium) | `apps/api/dms_api/routes/chat.py:380-381 - returns the Cortex row dump, not an envelope.`<br>`packages/cortex_client/cortex_client/models.py:116-122 - the response model has no served_* fields.` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (medium) | `packages/cortex_client/cortex_client/client.py:268-275 - POST /v1/contract/drillthrough (generated/api/contract/drillthrough.py:28), not /v1/contract...`<br>`apps/api/dms_api/routes/chat.py:357-387 - no ManifestMinter.mint_manifest, canonical_manifest_bytes or /v1/contract/ledger/append on this path; only ...` | `drillthrough-token-is-the-only-control` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/chat.py:369-370 - gate refusal -> 403 with the reason (named); chat.py:23 _SOFT_GATE lets gate_unavailable/gate_task_unknown ...`<br>`apps/api/dms_api/routes/chat.py:372-376 - Cortex missing -> 503 cortex_unavailable.` | `drillthrough-failure-and-truncation-not-named` |

Also on this path: packages/cortex_client/cortex_client/models.py:116-122 - DrillthroughResponse (extra='ignore') drops approximate, total_count, row_count, session_id that the contract returns (generated/models/drillthrough_response.py:22-31); the UI cannot show 'showing N of M' and exports of drill rows (AnswerMess...

Also on this path: packages/cortex_client/cortex_client/client.py:268-275 - a Cortex 422 is converted into an all-default success object instead of an error.

Also on this path: apps/api/dms_api/routes/chat.py:386 - str(exc)[:400] is returned to the caller; for UnexpectedStatus this includes the Cortex response body, a minor internals leak.

<a id="ep-export-xlsx"></a>
### `export:xlsx`

Excel export of a caller-supplied ask envelope (INSIGHTS-EXPORT-01). Producer: packages/core/dms_core/xlsx_export.py:186 export_envelope_xlsx. Reachable: yes - POST /v1/chat/export.xlsx is always mounted (apps/api/dms_api/app.py:146 include_router(chat.router); routes/chat.py:390); the UI 'Download Excel' button renders whenever envelope.answer_id is truthy (apps/ui/src/...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `apps/api/dms_api/routes/chat.py:397-408 - the handler's only calls are compliance_gate, enforce and export_envelope_xlsx(body.envelope); no SpaceStor...`<br>`packages/core/dms_core/xlsx_export.py:186-198 - export_envelope_xlsx is a pure function of the dict it is handed; module imports at :10-14 are json, ...` |  |
| G2 ontology verification | n/a (medium) | `packages/core/dms_core/xlsx_export.py:1-6 - docstring: copies envelope fields and rows, does not ask Cortex, does not query DuckDB, does not add tota...`<br>`packages/core/dms_core/xlsx_export.py:175-183 - envelope_sheets only reshapes values[] and rows[] into grids; no measure, join or ontology import any...` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/core/dms_core/xlsx_export.py:10-14 - imports are json, dms_core.pii, xlsx_ooxml; no duckdb, sqlglot, ontology or Cortex client`<br>`packages/core/dms_core/xlsx_export.py:26-36 - sql_used is only a Cover text key (COVER_KEYS), printed, never executed` |  |
| G4 typed ingest | n/a (high) | `packages/core/dms_core/xlsx_export.py:186-198 - input is a request-body dict, output is bytes; no table is opened`<br>`packages/core/dms_core/xlsx_ooxml.py:118-124 - cells are written as inlineStr or numbers from whatever Python type the JSON body had; no schema or ty...` |  |
| G5 PII mask (envelope + record) | part (medium) | `packages/core/dms_core/xlsx_export.py:196 - fail_closed_mask_envelope(envelope) runs before any sheet is built, so rows, values and text in the file ...`<br>`packages/core/dms_core/pii.py:1046-1077 - mask_envelope masks text, rows, values, contributing_sources, chart and sql_used, but for audit_receipt onl...` | `audit-receipt-and-late-added-cards-skip-mask` |
| G6 strict pin + served fields | **NO** (medium) | `packages/core/dms_core/xlsx_export.py:26-36 - COVER_KEYS has no served_attribution, served_provider or served_model; the file drops the stamp`<br>`packages/executor/dms_executor/generative_ask.py:276 and packages/executor/dms_executor/__init__.py:504-510 - the ask envelope is stamped served_attr...` | `export-restate-badge-without-attribution` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/core/dms_core/xlsx_export.py:117-136 - the only gate is a shape check: non-empty answer_id, badge in ALLOWED_BADGES, rows/values are lists o...`<br>`apps/api/dms_api/routes/chat.py:408 - body.envelope goes straight to the serializer; no mint_manifest, no /v1/contract/submit, no /v1/contract/ledger...` | `export-projections-no-provenance-tie` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/chat.py:409-413 - EnvelopeExportError becomes 400 with a named code (envelope_required); gate refusal becomes 403 with the re...`<br>`apps/api/dms_api/gatekeeping.py:66-69 and chat.py:406 - enforce(decision, mutation=False) returns silently on gate_unavailable / gate_task_unknown, s...` | `browse-and-chunk-routes-not-answer-paths` |

Also on this path: apps/api/dms_api/routes/chat.py:397-403 and packages/cortex_client/cortex_client/gate.py:46-57 - route metadata (answer_id, target) is never sent: compliance_gate builds its payload from event_id/task_id/filled_template/actor only, so F5 'chat.export' cannot decide on content and is a task-level ch...

Also on this path: apps/api/dms_api/routes/chat.py:390-420 - the export writes no ledger or audit entry, so who exported which rows under which badge is unrecorded

Also on this path: packages/core/dms_core/pii.py:170-183 - answer_id, as_of, audit_id, space_id and ask_mode are _KEEP_KEYS and are copied to the Cover unscanned (caller-supplied strings)

<a id="ep-export-bi"></a>
### `export:bi`

Power BI / Superset connect stub from a caller-supplied ask envelope (INSIGHTS-EXPORT-02). Producer: packages/core/dms_core/bi_export.py:203 export_envelope_bi. Reachable: yes - POST /v1/chat/export.bi always mounted (apps/api/dms_api/routes/chat.py:423; app.py:146); UI 'Power BI' and 'Superset' buttons render whenever envelope.answer_id is truthy (AnswerMessage.tsx:339-356), and the stub...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `apps/api/dms_api/routes/chat.py:430-442 - only compliance_gate, enforce and export_envelope_bi(body.envelope, target=body.target)`<br>`packages/core/dms_core/bi_export.py:203-247 - pure transformation of the request dict; imports at :11-23 are math, typing, dms_core.pii, dms_core.xls...` |  |
| G2 ontology verification | n/a (medium) | `packages/core/dms_core/bi_export.py:1-6 - copies envelope rows (or values, or Cover); does not invent DAX or Superset metrics`<br>`packages/core/dms_core/bi_export.py:80-100 - power_query_m embeds literal rows in a #table; no measure expression is generated` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/core/dms_core/bi_export.py:11-23 - no SQL engine, ontology or Cortex client import`<br>`packages/core/dms_core/bi_export.py:235-247 - response is {table, columns, targets}; no sql_used is even returned` |  |
| G4 typed ingest | n/a (high) | `packages/core/dms_core/bi_export.py:151-168 - _col_type infers STRING/INTEGER/FLOAT/BOOLEAN from the Python values of the caller's rows; it is a labe...`<br>`packages/core/dms_core/bi_export.py:203-247 - no table is read` |  |
| G5 PII mask (envelope + record) | part (low) | `packages/core/dms_core/bi_export.py:217 - envelope = fail_closed_mask_envelope(envelope) runs before envelope_connect_table, so rows and values in th...`<br>`packages/core/dms_core/bi_export.py:118-135 - when rows and values are both empty the table is the Cover, built from COVER_KEYS, which includes audit...` | `audit-receipt-and-late-added-cards-skip-mask` |
| G6 strict pin + served fields | **NO** (medium) | `packages/core/dms_core/bi_export.py:235-247 - response carries badge and abstained echoed from the request, and no served_attribution / served_provid...`<br>`packages/core/dms_core/bi_export.py:209-216 and xlsx_export.py:117-136 - the only check on badge is membership in ALLOWED_BADGES` | `export-restate-badge-without-attribution` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/core/dms_core/bi_export.py:209-216 - shape-only gate (refuse_envelope_export from xlsx_export.py:117-136)`<br>`apps/api/dms_api/routes/chat.py:442 - body.envelope passed straight through; no mint_manifest, no /v1/contract/submit, no ledger append` | `export-projections-no-provenance-tie` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/chat.py:443-447 - EnvelopeExportError becomes 400 with a named code; the Literal on ExportBiBody.target (chat.py:146-150) mak...`<br>`apps/api/dms_api/gatekeeping.py:66-69 and chat.py:440 - gate_unavailable / gate_task_unknown is a silent soft-pass` | `demo-answers-exported-and-shared-unlabelled` |

Also on this path: apps/api/dms_api/routes/chat.py:430-442 - as with xlsx, route metadata target/answer_id is dropped by compliance_gate (gate.py:46-57), and the export writes no ledger entry

Also on this path: packages/core/dms_core/bi_export.py:203-247 - no payload size cap on the posted envelope; the stub embeds every row as an M literal

<a id="ep-export-csv-client"></a>
### `export:csv-client`

Client-side CSV download of answer rows. Producer: apps/ui/src/components/AnswerMessage.tsx:201 downloadRowsCsv -> apps/ui/src/lib/rowsToCsv.ts:61 rowsToCsv. Reachable: yes - the 'Download CSV' button renders whenever the answer has rows (apps/ui/src/components/AnswerMessage.tsx:322-330); the drillthrough branch is reachable when the envelope has a drillthrough_token and either the use...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/ui/src/components/AnswerMessage.tsx:187-199 - rowsForExport calls fetchDrillRows (:145-156) which POSTs /api/v1/chat/drillthrough with {token}; ...`<br>`apps/api/dms_api/routes/chat.py:357-385 - the handler takes only settings and cortex, runs compliance_gate with a soft pass on _SOFT_GATE (:364-370, ...` | `drillthrough-token-is-the-only-control` |
| G2 ontology verification | n/a (medium) | `apps/ui/src/lib/rowsToCsv.ts:61-69 - rowsToCsv only formats columns and cells; no arithmetic`<br>`packages/cortex_client/cortex_client/models.py:112-113 - the drillthrough request is only a token, so this path cannot author or alter a measure` |  |
| G3 join rule + fan-out guard | n/a (medium) | `packages/cortex_client/cortex_client/models.py:112-113 - DrillthroughRequest has no SQL field, so no multi-table SQL can be supplied or run by this p...`<br>`apps/ui/src/lib/rowsToCsv.ts:1-69 - pure string formatting, no SQL` |  |
| G4 typed ingest | n/a (low) | `apps/ui/src/lib/rowsToCsv.ts:25-33 - csvCell renders by JS typeof; no ingest-type lookup`<br>`packages/cortex_client/cortex_client/models.py:116-122 - drillthrough response is rows, sql_used, source_table, answer_id; table typing is decided on...` |  |
| G5 PII mask (envelope + record) | part (high) | `apps/ui/src/lib/rowsToCsv.ts:61-69 and AnswerMessage.tsx:201-210 - rowsToCsv(exportRows) is written straight to a Blob download; no masker is called ...`<br>`packages/executor/dms_executor/envelope.py:1626-1638 - envelope.rows are masked server-side at build, so the envelope-rows branch is masked upstream` | `preview-and-drill-rows-unmasked` |
| G6 strict pin + served fields | n/a (medium) | `apps/ui/src/lib/rowsToCsv.ts:61-69 - output is rows only: no badge, no envelope, no attribution field`<br>`apps/ui/src/components/AnswerMessage.tsx:201-210 - no model or Cortex ask call; the only fetch is the drillthrough token replay` |  |
| G7 Cortex submit (manifest + ledger) | part (medium) | `apps/ui/src/components/AnswerMessage.tsx:187-199 - rows are either the envelope's own rows or Cortex drillthrough rows; the path runs no check of pro...`<br>`packages/executor/dms_executor/session_followup.py:122-138 - the follow-up lane runs its SQL in DMS's own DuckDB (execute_sql) with no Cortex submit;...` | `export-projections-no-provenance-tie` |
| G8 named abstain on failure | **NO** (high) | `apps/ui/src/components/AnswerMessage.tsx:153 and :195-197 - a non-OK drillthrough returns null and the CSV silently keeps the summary rows`<br>`apps/ui/src/components/AnswerMessage.tsx:325 - void downloadRowsCsv(): a fetch rejection is an unhandled promise rejection, no state is set (exportEr...` | `drillthrough-failure-and-truncation-not-named` |

Also on this path: apps/ui/src/lib/rowsToCsv.ts:6-9 and :25-33 - csvEscape only quotes on , " and newlines; a cell starting with = + - or @ is written as-is, so ingested text like =HYPERLINK(...) is a formula when the CSV is opened in Excel (the xlsx path writes inlineStr and is safe: packages/core/dms_core/xlsx_ooxm...

Also on this path: apps/api/dms_api/routes/chat.py:380-382 - /v1/chat/drillthrough returns raw rows and sql_used unmasked (belongs to the chat:drillthrough row, flagged here because the CSV and the Sources panel consume it)

Also on this path: apps/api/dms_api/routes/chat.py:386-390 - the drillthrough failure detail is str(exc)[:400] and is logged at :385; a Cortex error string could carry values (not proven)

<a id="ep-ui-share-answer"></a>
### `ui:share-answer`

Share answer (envelope JSON to clipboard). Producer: apps/ui/src/lib/answerDelivery.ts:23 shareEnvelopePayload. Reachable: yes - the 'Share answer' button renders for every answer, unconditionally (apps/ui/src/components/AnswerMessage.tsx:358-369); no server route, no flag.

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `apps/ui/src/lib/answerDelivery.ts:23-41 - shareEnvelopePayload is JSON.stringify over fields of the in-memory envelope; no fetch`<br>`apps/ui/src/lib/copilotPrompts.ts:37-57 - copyText writes to navigator.clipboard or a hidden textarea; no network` |  |
| G2 ontology verification | n/a (high) | `apps/ui/src/lib/answerDelivery.ts:23-41 - copies text, values, rows, chart, audit_receipt as received; no computation`<br>`apps/ui/src/components/AnswerMessage.tsx:361-363 - the share handler performs no arithmetic or lookup` |  |
| G3 join rule + fan-out guard | n/a (high) | `apps/ui/src/lib/answerDelivery.ts:23-41 - the payload has no sql_used field and no query call exists on the path`<br>`apps/ui/src/components/AnswerMessage.tsx:361-363 - no network call` |  |
| G4 typed ingest | n/a (high) | `apps/ui/src/lib/answerDelivery.ts:23-41 - JSON.stringify of an existing object`<br>`apps/ui/src/lib/copilotPrompts.ts:37-57 - clipboard only` |  |
| G5 PII mask (envelope + record) | part (medium) | `apps/ui/src/lib/answerDelivery.ts:23-41 - no masker runs on this path; text, values, rows and chart are copied as received`<br>`packages/executor/dms_executor/envelope.py:1626-1638 - the ask-side build masks text, rows, values, sources, chart and sql_used before the envelope l...` | `audit-receipt-and-late-added-cards-skip-mask` |
| G6 strict pin + served fields | **NO** (medium) | `apps/ui/src/lib/answerDelivery.ts:24-41 - payload fields are kind, answer_id, badge, abstained, text, values, rows, chart, space_id, audit_id, as_of,...`<br>`packages/executor/dms_executor/generative_ask.py:276 and packages/executor/dms_executor/__init__.py:504-510 - the ask envelope carries served_attribu...` | `export-restate-badge-without-attribution` |
| G7 Cortex submit (manifest + ledger) | part (medium) | `apps/ui/src/lib/answerDelivery.ts:23-41 - values and rows copied as received; no provenance check`<br>`packages/executor/dms_executor/session_followup.py:122-138 - follow-up lane figures are computed in DMS's own DuckDB, no Cortex submit` | `export-projections-no-provenance-tie` |
| G8 named abstain on failure | part (medium) | `apps/ui/src/components/AnswerMessage.tsx:361-363 - clipboard failure is reported as 'Copy failed' (named)`<br>`apps/ui/src/lib/answerDelivery.ts:24-41 - payload omits ask_mode, demo_fallback_used and demo_fallback_banner, plus assumptions and grounded_tables` | `demo-answers-exported-and-shared-unlabelled` |

Also on this path: apps/ui/src/lib/answerDelivery.ts:23-41 - the share payload also omits assumptions, grounded_tables and constraint_trace, so the scope the answer was grounded in does not travel with it

Also on this path: apps/ui/src/lib/answerDelivery.ts:23-41 - the shared JSON contains space_id and audit_id verbatim; not a leak by itself, but it is a Space identifier handed to a clipboard

<a id="ep-ui-check-accuracy"></a>
### `ui:check-accuracy`

Client-computed row-sum 'Check accuracy' figure. Producer: apps/ui/src/lib/answerDelivery.ts:68 checkAnswerTotals. Reachable: yes - default config, no flag. The button is rendered unconditionally on every answer card (apps/ui/src/components/AnswerMessage.tsx:372-378) and calls checkAnswerTotals (apps/ui/src/lib/answerDelivery.ts:68). No server...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `apps/ui/src/lib/answerDelivery.ts:68-158 - checkAnswerTotals(envelope) reads only envelope.rows/values/chart/badge; there is no fetch, DB or table ac...`<br>`apps/ui/src/components/AnswerMessage.tsx:372-375 - onClick calls checkAnswerTotals(envelope) and stores r.message in React state; no network call` |  |
| G2 ontology verification | **NO** (medium) | `apps/ui/src/lib/answerDelivery.ts:83-89 - the summed column is chosen by regex on column names (chart.y, else /value\|amount\|revenue\|total\|myr\|us...`<br>`apps/ui/src/lib/answerDelivery.ts:95-102 and :117 - a SUM over all rows and over values[] is computed in JS` | `ui-derives-figures-and-labels-client-side` |
| G3 join rule + fan-out guard | n/a (high) | `apps/ui/src/lib/answerDelivery.ts:68-158 - the function takes an envelope and returns {status,message}; no SQL is read, built or served`<br>`apps/ui/src/components/AnswerMessage.tsx:372-375 - the handler only stores the message string` |  |
| G4 typed ingest | n/a (medium) | `apps/ui/src/lib/answerDelivery.ts:44-62 - numericKeys/toNum accept numeric-looking strings and coerce them with Number(v)`<br>`apps/ui/src/components/AnswerMessage.tsx:372-375 - the call site; no table is read` |  |
| G5 PII mask (envelope + record) | part(2/3) (medium) | `packages/executor/dms_executor/envelope.py:1626-1636 - build_answer_envelope masks text/rows/values/sources/chart/sql_used with fail_closed_mask_payl...`<br>`packages/executor/dms_executor/envelope.py:1739 and packages/executor/dms_executor/__init__.py:508-510 - mask_unknown_keys runs on every build and ag...` |  |
| G6 strict pin + served fields | n/a (high) | `apps/ui/src/lib/answerDelivery.ts:68-158 - returns {status,message,rowSum,stated,column}, not an envelope; no fetch and no model call`<br>`apps/ui/src/components/AnswerMessage.tsx:372-375 - the whole click path` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `apps/ui/src/lib/answerDelivery.ts:95-102 - rowSum accumulated in browser JS`<br>`apps/ui/src/lib/answerDelivery.ts:117 - valueSum computed in browser JS` | `ui-derives-figures-and-labels-client-side` |
| G8 named abstain on failure | part (medium) | `apps/ui/src/lib/answerDelivery.ts:77,80,92,99 - skip branches (no rows, abstained, no numeric column, non-numeric column) return a plain-English reas...`<br>`apps/ui/src/lib/answerDelivery.ts:83-89,121-139,142-157 - verdict logic over a heuristically chosen column` | `ui-derives-figures-and-labels-client-side` |

Also on this path: apps/ui/src/lib/answerDelivery.ts:104-157 with packages/executor/dms_executor/envelope.py:1296-1307 - the check is self-referential: values[] is harvested from the same rows it is compared with, so a Match cannot detect a fan-out join, a dropped filter or a wrong measure (both sides inflate togethe...

Also on this path: apps/ui/src/lib/answerDelivery.ts:50,57-60 - numeric strings are coerced with Number(); an all-VARCHAR bronze result is summed with no declared type.

Also on this path: apps/ui/src/lib/answerDelivery.ts:108-114 - when values[] holds no numeric value the check returns status 'ok' and prints a new row-sum figure ('compare CSV yourself'), a figure outside values[] shown under an ok status.

<a id="ep-ui-exclusion-auto-confirm"></a>
### `ui:exclusion-auto-confirm`

UI auto-fired ask on an exclusion-confirm abstain. Producer: apps/ui/src/components/AnswerMessage.tsx:163-185 (auto ask(resolveExclusionNoChip(noChip)) after 5s; client rewrite :34-43); server rewrite packages/executor/dms_executor/demo_ask.py:177-189 normaliz.... Reachable: yes on default config; the trigger is engine-dependent. It needs an ABSTAIN envelope whose suggestions contain both a '^Yes - exclude' and a '^No - show\|without excluding' chip (apps/ui/src/components/AnswerMessage.tsx...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/ui/src/context/AppContext.tsx:341-346 - the auto-ask posts space_id=activeSpaceIdRef.current (may be null), session_id and grounded_tables`<br>`apps/ui/src/components/TopBar.tsx:50 - 'Company (default ACL)' option sets the Space to null` | `no-space-ask-widest-grant` |
| G2 ontology verification | **NO** (medium) | `packages/executor/dms_executor/__init__.py:776-813 - contract-ask lane: demo_acl, bind, _cortex.ask, map_ask_response_to_envelope; no ontology load o...`<br>`packages/executor/dms_executor/__init__.py:894-1046 - map_ask_response_to_envelope copies engine rows/values/badge; no ontology call` | `served-sql-never-reverified-against-ontology` |
| G3 join rule + fan-out guard | **NO** (medium) | `packages/executor/dms_executor/__init__.py:1006-1011 - resp.sql_used is only echoed into the envelope; nothing inspects joins`<br>`packages/executor/dms_executor/__init__.py:776-813 - no join-rule or fan-out call on the contract-ask lane` | `free-sql-lanes-no-fanout-guard` |
| G4 typed ingest | part (low) | `packages/executor/dms_executor/demo_warehouse.py:304-311,350-357 - the default readable demo tables are created with declared column types`<br>`packages/executor/dms_executor/__init__.py:360-368 - a grounded selection can name uploaded tables and becomes the manifest` | `cortex-lake-column-types-unread-by-dms` |
| G5 PII mask (envelope + record) | part (low) | `packages/executor/dms_executor/envelope.py:1626-1636,1739 - envelope text/rows/values/sources/chart/sql_used masked; remaining keys scanned by mask_u...`<br>`packages/executor/dms_executor/__init__.py:508-510 - mask_unknown_keys on every live_ask result` | `cortex-error-detail-returned-unmasked` |
| G6 strict pin + served fields | part (medium) | `packages/cortex_client/cortex_client/insights.py:256-262 and packages/cortex_client/cortex_client/compute.py:838-840 - generative lane Insights gener...`<br>`packages/cortex_client/cortex_client/compute.py:745-751 and insights.py:185-188 - only a 503 pin_unavailable becomes a named abstain; a mismatched se...` | `contract-ask-model-call-unpinned-and-stamped-none` |
| G7 Cortex submit (manifest + ledger) | part (medium) | `packages/executor/dms_executor/generative_ask.py:845-869 and verified_queries.py:347-362 - submit then a ledger entry that must carry entry_id and a ...`<br>`packages/executor/dms_executor/__init__.py:817-850 - submit_sql mints a signed manifest` | `contract-ask-answer-not-submitted-or-ledgered-by-dms` |
| G8 named abstain on failure | part (medium) | `apps/ui/src/components/AnswerMessage.tsx:163-185 - the countdown fires ask(resolveExclusionNoChip(noChip)) with no user input, turning the engine's c...`<br>`apps/ui/src/components/AnswerMessage.tsx:536-539 - the UI does say 'Yes cancels automatically in Ns - then the unfiltered top ranking runs'` | `exclusion-auto-decline-answers-different-question` |

Also on this path: apps/ui/src/components/AnswerMessage.tsx:163-185 with apps/ui/src/context/AppContext.tsx:400 - PLAUSIBLE runaway re-ask (static reading, not run). The effect deps include ask, and ask is rebuilt whenever asking toggles (AppContext.tsx:333,364,400). After the first auto-fire, asking flips, the effec...

Also on this path: packages/executor/dms_executor/__init__.py:541 and packages/executor/dms_executor/demo_ask.py:151-189 - normalize_ask_question replaces ANY question matching 'top N ... without exclud' with 'Top N selling SKUs by revenue', dropping the rest of the question (for example 'top 3 suppliers by risk with...

Also on this path: packages/executor/dms_executor/__init__.py:937-939 - an engine response with no badge and abstained=false is given L2_VALIDATED, a confident default.

<a id="ep-ui-source-panel-contribution"></a>
### `ui:source-panel-contribution`

Sources panel client-derived contribution figures (MYR-formatted weights, % share, row totals). Producer: apps/ui/src/components/SourcePanel.tsx:43-47 (totalRows, totalContribution), :122-125 (pct), :153 formatMoney(src.contribution); headline apps/ui/src/lib/sourcePanel.ts:35-45. Reachable: yes - default config. The panel auto-opens after every answer at lg width (apps/ui/src/context/AppContext.tsx:351-354) and via the sources button (apps/ui/src/components/AnswerMessage.tsx:407-410). It renders latestAnsw...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `apps/ui/src/components/SourcePanel.tsx:43-47,122-125,153 - arithmetic over the in-memory contributingSources only`<br>`apps/ui/src/context/AppContext.tsx:454 - contributingSources = sourcesForPanel(latestAnswer)` |  |
| G2 ontology verification | **NO** (medium) | `apps/ui/src/components/SourcePanel.tsx:43-47 - totalRows and totalContribution are client sums`<br>`apps/ui/src/components/SourcePanel.tsx:122-125,153 - % share and MYR rendering of the weight` | `ui-derives-figures-and-labels-client-side` |
| G3 join rule + fan-out guard | n/a (high) | `apps/ui/src/lib/sourcePanel.ts:3,11-16 - sql_used is only regex-scanned for the first FROM name; no SQL is built or served`<br>`apps/ui/src/components/SourcePanel.tsx:43-153 - the panel renders metadata only` |  |
| G4 typed ingest | n/a (high) | `apps/ui/src/lib/sourcePanel.ts:19-33 - cards come from envelope fields`<br>`apps/ui/src/components/SourcePanel.tsx:43-153 - no table is read` |  |
| G5 PII mask (envelope + record) | part(2/3) (medium) | `packages/executor/dms_executor/envelope.py:1626-1636 - sources are masked in build_answer_envelope; packages/core/dms_core/pii.py:852-877 walks and s...`<br>`packages/executor/dms_executor/envelope.py:1725,1739 - grounded_tables scanned by mask_unknown_keys` |  |
| G6 strict pin + served fields | n/a (high) | `apps/ui/src/components/SourcePanel.tsx:17-225 - renders; the only fetch is postReveal on click`<br>`apps/ui/src/lib/sourcePanel.ts:19-45 - pure functions` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `apps/ui/src/components/SourcePanel.tsx:43-47 - totalRows, totalContribution summed in JS`<br>`apps/ui/src/components/SourcePanel.tsx:122-125,153 - percentage share and MYR figure computed and rendered in JS` | `ui-derives-figures-and-labels-client-side` |
| G8 named abstain on failure | part (high) | `apps/ui/src/lib/sourcePanel.ts:35-45 - sourcesHeadline ignores env.badge; line 44 reads 'Certified answer, no file card from Cortex. Open SQL.' for a...`<br>`apps/ui/src/lib/sourcePanel.ts:19-33 - cards are synthesized from grounded_tables/sql even for an ABSTAIN envelope` | `ui-derives-figures-and-labels-client-side` |

Also on this path: apps/ui/src/lib/sourcePanel.ts:44 - the headline claim 'Certified answer' does not depend on the badge; it appears over ABSTAIN, L2_VALIDATED and L2_ANOMALOUS answers whenever no cards exist. The unit test only covers an L0 envelope (sourcePanel.test.ts).

Also on this path: apps/ui/src/components/SourcePanel.tsx:9-15,153 - a 0..1 weight is formatted as MYR currency ('RM 1.00 · 100.0%'); the demo envelope shows locations as '3 rows · RM 0.00 · 0.0%' on every demo answer (demo_ask.py:29-46).

Also on this path: apps/ui/src/lib/sourcePanel.ts:30 - synthesized cards report row_count = the answer's row count as if it were each table's size; the headline total multiplies it by the number of tables.

<a id="ep-ui-demo-source-fixture-preview"></a>
### `ui:demo-source-fixture-preview`

UI fabricated spreadsheet preview for demo answers (dormant). Producer: apps/ui/src/lib/previewFixtures.ts:20-61 (buildSheet / PREVIEW_SHEETS / sheetForSource). Reachable: no - dormant at this commit. The guard is ask_mode === 'demo' and a non-doc source whose ref_id is ref_q3_sales, ref_kl_branch or ref_erp_inv (apps/ui/src/components/SourcePanel.tsx:128-129; apps/ui/src/lib/previewFixtu...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (high) | `apps/ui/src/lib/previewFixtures.ts:20-47 - buildSheet generates all cells from literals and loop indexes`<br>`apps/ui/src/lib/previewFixtures.ts:59-60 - sheetForSource is a static map lookup by ref_id` |  |
| G2 ontology verification | **NO** (low) | `apps/ui/src/lib/previewFixtures.ts:34 - contributing cell value is 1000 + row * 17`<br>`apps/ui/src/components/SourcePanel.tsx:171-172 - the grid is rendered as the source's cells` | `ui-demo-source-fixture-unreachable` |
| G3 join rule + fan-out guard | n/a (high) | `apps/ui/src/lib/previewFixtures.ts:20-60 - no SQL anywhere in the module` |  |
| G4 typed ingest | n/a (high) | `apps/ui/src/lib/previewFixtures.ts:49-57 - sheets built in memory, no table read` |  |
| G5 PII mask (envelope + record) | yes (low) | `apps/ui/src/lib/previewFixtures.ts:20-47 - cells are SKU-n, North/South, A1-style labels and arithmetic numbers; no tenant data can enter`<br>`apps/ui/src/components/PreviewGrid.tsx:55-59 - rendering only; no storage, fetch or logging` |  |
| G6 strict pin + served fields | n/a (high) | `apps/ui/src/lib/previewFixtures.ts:20-60 - static data`<br>`apps/ui/src/components/SourcePanel.tsx:128-129 - render-time lookup; no envelope returned, no model call` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (medium) | `apps/ui/src/lib/previewFixtures.ts:31-47 - figures generated client-side`<br>`apps/ui/src/lib/previewFixtures.ts:3 - the file itself says 'Demo-only fixture sheet - never use for live answer provenance'` | `ui-demo-source-fixture-unreachable` |
| G8 named abstain on failure | part (low) | `apps/ui/src/components/SourcePanel.tsx:177-182 - a fixture miss falls to a named 'Cell preview unavailable for this source.'`<br>`apps/ui/src/components/PreviewGrid.tsx:55-59 - the grid header shows only sheet.title; there is no fixture label in the panel` | `ui-demo-source-fixture-unreachable` |

Also on this path: apps/ui/src/lib/previewFixtures.ts:49-57 and apps/ui/src/components/SourcePanel.tsx:128-129 - dead code that ships in the bundle; the realistic names (Q3_sales_final_v2.xlsx, KL_branch_sales.xlsx, erp.public.invoices) appear in no producer, test or e2e. The Cortex repo was not read: if its demo eve...

Also on this path: apps/ui/src/lib/previewFixtures.ts:59-60 - fixtures are keyed by ref_id alone; PreviewSheet carries no fixture marker, so the guard on ask_mode is the only protection.

<a id="ep-library-warehouse-preview"></a>
### `library:warehouse-preview`

Library warehouse table preview (raw rows). Producer: packages/executor/dms_executor/warehouse_browse.py:114 preview_warehouse_table (via apps/api/dms_api/wiring.py:92-93). Reachable: yes - GET /v1/library/warehouse/{table}/preview; library router is mounted unconditionally (apps/api/dms_api/app.py:151), no flag. Also reachable through the MCP tool when DMS_MCP=1 (see mcp:preview). Serves only the si...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (high) | `apps/api/dms_api/routes/library.py:272-274 - allowed = {t['table'] for t in warehouse_tables(space_id=space_id)}; a table outside it raises _refuse('...`<br>`apps/api/dms_api/wiring.py:42-43 - warehouse_tables() is dms_executor.list_warehouse_tables, not Executor.grantable_tables/demo_acl.` | `read-routes-own-space-check-not-shared-grant` |
| G2 ontology verification | n/a (medium) | `packages/executor/dms_executor/warehouse_browse.py:129-130 - the only SQL is SELECT COUNT(*) FROM {name} and SELECT * FROM {name} LIMIT/OFFSET.`<br>`packages/executor/dms_executor/warehouse_browse.py:1-17 - the module imports no ontology code.` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/warehouse_browse.py:19,122 - _ALLOWED = frozenset(DEMO_TABLES); any other name raises ValueError before SQL is built.`<br>`packages/executor/dms_executor/warehouse_browse.py:130 - fixed single-table template SELECT * FROM {name}; no caller-supplied SQL.` |  |
| G4 typed ingest | yes (medium) | `packages/executor/dms_executor/warehouse_browse.py:19,122 - only the six DEMO_TABLES can be read.`<br>`packages/executor/dms_executor/demo_warehouse.py:304-311,328-335,350-358,377-384,400-406,422-430 - each table is created with declared column types (...` |  |
| G5 PII mask (envelope + record) | part (high) | `apps/api/dms_api/routes/library.py:276-280 - the preview dict (rows, columns) is returned as-is; library.py:1-26 imports no masker.`<br>`packages/executor/dms_executor/warehouse_browse.py:130-137 - raw rows from SELECT *; no mask call. mask_unknown_keys/fail_closed_mask_payload callers...` | `preview-and-drill-rows-unmasked` |
| G6 strict pin + served fields | n/a (high) | `apps/api/dms_api/routes/library.py:276-280 - returns a plain preview dict plus scope label, not an answer envelope.`<br>`packages/executor/dms_executor/warehouse_browse.py:1-17 - imports only bronze, demo_warehouse and duckdb_scalar; no model provider or serving engine.` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/warehouse_browse.py:127-137 - connect_readonly() then con.execute(SELECT ...) on DMS's own DuckDB file.`<br>`packages/executor/dms_executor/demo_warehouse.py:458-461 - connect_readonly is a local file connection.` | `browse-and-chunk-routes-not-answer-paths` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/library.py:231-241,272-274 - ungranted or unknown table -> structured 403 warehouse_not_in_space naming the scope (never an e...`<br>`apps/api/dms_api/gatekeeping.py:68-71 with library.py:270 - an unreachable gate on a read is silently allowed; the response carries no marker.` | `browse-and-chunk-routes-not-answer-paths` |

Also on this path: packages/executor/dms_executor/warehouse_browse.py:125 and apps/api/dms_api/routes/library.py:249 - offset above 100_000 is clamped, so a table past 100k rows cannot be paged; the UI would repeat the same page.

Also on this path: packages/cortex_client/cortex_client/gate.py:44-54 - compliance_gate forwards only event_id/task_id/filled_template, so the table/scope metadata that library.py:257-269 passes never reaches Cortex F5; the audit record cannot say which table or Space was previewed.

Also on this path: packages/executor/dms_executor/demo_warehouse.py:264-290 and :458-461 - a preview GET calls ensure_demo_warehouse, which on first call per process DROPs and reseeds the six demo tables; 'connect_readonly' is a read-write connection (comment: same config as writers). Read-only is by convention only.

<a id="ep-library-bronze-preview"></a>
### `library:bronze-preview`

Library bronze table preview (raw uploaded / SQL-pulled rows). Producer: packages/executor/dms_executor/warehouse_browse.py:182 preview_bronze_table (via apps/api/dms_api/wiring.py:96-97). Reachable: yes - GET /v1/library/bronze/{table:path}/preview; library router mounted unconditionally (apps/api/dms_api/app.py:151), no flag. Called by the Library page and Studio tree (apps/ui/src/lib/api.ts:372-388). Reads DMS_WA...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (high) | `apps/api/dms_api/routes/library.py:308-319 - the Space check runs only under `if space_id:`; it compares the table to bronze_list(space_id) (registry...`<br>`packages/executor/dms_executor/bronze.py:546-558 - list_bronze_tables(space_id=None) returns EVERY bronze.* and main.bronze_* table with no registry ...` | `read-routes-own-space-check-not-shared-grant` |
| G2 ontology verification | n/a (medium) | `packages/executor/dms_executor/warehouse_browse.py:198-199 - SELECT COUNT(*) and SELECT * on one quoted table.`<br>`packages/executor/dms_executor/warehouse_browse.py:1-17 - no ontology import.` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/warehouse_browse.py:146-156 - _parse_bronze_ref enforces _IDENT for schema and [A-Za-z0-9_]+ for name; anything else r...`<br>`packages/executor/dms_executor/warehouse_browse.py:159-190 - the label must resolve to a name already in list_bronze_tables.` |  |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/bronze.py:491 - write_bronze_rows creates the landing table with every column VARCHAR; db_connector.py:529-536 uses it...`<br>`packages/executor/dms_executor/bronze.py:401-413 - CSV bronze tables take whatever DuckDB read_csv auto_detect sniffs; no declared contract is persis...` | `browse-and-chunk-routes-not-answer-paths` |
| G5 PII mask (envelope + record) | part (high) | `apps/api/dms_api/routes/library.py:321-327 - bronze_preview() result returned unmasked; no masker imported in library.py.`<br>`packages/executor/dms_executor/warehouse_browse.py:228-250 - rows, source (file path or SQL source string), extracted_at returned raw.` | `preview-and-drill-rows-unmasked` |
| G6 strict pin + served fields | n/a (high) | `apps/api/dms_api/routes/library.py:321-327 - returns a plain preview dict with scope, no envelope.`<br>`packages/executor/dms_executor/warehouse_browse.py:182-250 - no model provider or serving engine call.` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/warehouse_browse.py:196-201 - connect_readonly(path or warehouse_path()) then local con.execute of COUNT(*) and SELECT...`<br>`apps/api/dms_api/routes/library.py:297-327 - no manifest, no /v1/contract/submit, no ledger append; only the F5 gate.` | `browse-and-chunk-routes-not-answer-paths` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/library.py:320-325 - ValueError (unknown/disallowed table) -> 403 bronze_not_in_space, deliberately the same as ungranted (no...`<br>`apps/api/dms_api/gatekeeping.py:68-71 with library.py:306 - gate unavailable on a read is allowed silently.` | `browse-and-chunk-routes-not-answer-paths` |

Also on this path: apps/api/dms_api/routes/library.py:291-296 - docstring ('registered to none is not previewable under any scope') is false at this commit; bronze.py:546-558 serves unregistered tables under company-default.

Also on this path: apps/api/dms_api/routes/library.py:315-319 - the Space check compares base names only (rsplit('.', 1)[-1]), ignoring schema; with a registry joined on table_name alone (bronze.py:537) a same-named table in another schema compares equal. Exotic, low risk.

Also on this path: packages/executor/dms_executor/warehouse_browse.py:238-241 - the response returns `source` (full file path or credential-free SQL source string) to any caller of the route.

<a id="ep-library-chunks-search"></a>
### `library:chunks-search`

RAG-02 document chunk search returning chunk content. Producer: apps/api/dms_api/wiring.py:51-73 search_document_chunks -> packages/core/dms_core/control_plane/document_chunks.py:147 search_chunks. Reachable: yes. GET /v1/library/chunks/search is mounted unconditionally (apps/api/dms_api/app.py:151 include_router(library.router), no flag; only mcp is flag-gated at :158-159). Default config has DATABASE_URL=None (apps/api/dms...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/library.py:122 - space_id is a required Query param (omitted gives 422); it is the only Space input, and it is chosen by the ...`<br>`packages/core/dms_core/control_plane/document_chunks.py:188-189 - the only restriction is the SQL predicate tenant_id = settings tenant AND space_id ...` | `read-routes-own-space-check-not-shared-grant` |
| G2 ontology verification | n/a (high) | `packages/core/dms_core/control_plane/document_chunks.py:174-199 - a single fixed SELECT over dms.document_chunks; lexical_score (:180-186) is COUNT(*...`<br>`apps/api/dms_api/routes/library.py:135-140 + apps/api/dms_api/wiring.py:51-73 - no call into the ontology or executor on this path` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/core/dms_core/control_plane/document_chunks.py:174-199 - hard-coded SQL template whose only FROM is dms.document_chunks (:187); the other FR...`<br>`document_chunks.py:168-171 - the only interpolated fragment, source_clause, is a constant literal with a %s bind; q, space_id and source_ids are boun...` |  |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/batch_ingest.py:139-166 - chunks are written only for SheetClass.UNSTRUCTURED files, which skip bronze and typed inges...`<br>`packages/executor/dms_executor/document_chunks.py:14-31 - extract_text decodes raw bytes or openpyxl cell strings into text; no type inference or con...` | `browse-and-chunk-routes-not-answer-paths` |
| G5 PII mask (envelope + record) | part (high) | `packages/core/dms_core/control_plane/document_chunks.py:205-218 - search hits return raw content, blob_key and embed_meta; no mask call in document_c...`<br>`apps/api/dms_api/app.py:126,133 - the only middleware is CORS and RejectIdentityHeadersMiddleware; no response masking` | `chunk-text-routes-unmasked` |
| G6 strict pin + served fields | n/a (high) | `packages/core/dms_core/control_plane/document_chunks.py:14,18-26 - dense score is a local char-trigram hash vector; the comment notes upgrading to a ...`<br>`apps/api/dms_api/routes/library.py:135-140 - returns a bare list[dict]; no build_answer_envelope, no served_* fields` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/core/dms_core/control_plane/document_chunks.py:164-197 - rows are read from Postgres by a local psycopg connection inside DMS`<br>`packages/core/dms_core/control_plane/document_chunks.py:202-221 - score, lexical_score and dense_score are computed and sorted in Python` | `browse-and-chunk-routes-not-answer-paths` |
| G8 named abstain on failure | part (high) | `apps/api/dms_api/app.py:135-142 + routes/library.py:122-123 - missing space_id or q gives a named 422; apps/api/dms_api/gatekeeping.py:70 - a gate re...`<br>`apps/api/dms_api/wiring.py:64-65 - DATABASE_URL unset returns [] with 200 and no reason (this is the default config)` | `browse-and-chunk-routes-not-answer-paths` |

Also on this path: packages/core/dms_core/control_plane/document_chunks.py:22 - _dense_vector uses Python's per-process salted str hash(); PYTHONHASHSEED is set nowhere in the repo (grep). Vectors stored at index time (:81) cannot be compared with the query vector at search time (:164) after any API restart or in ano...

Also on this path: packages/core/dms_core/control_plane/document_chunks.py:185 - user query tokens go into LIKE '%' \|\| token \|\| '%' with no escaping of % or _. q='%%' or '__' gives every chunk in the Space a nonzero lexical score, so 'search' returns the whole Space ranked arbitrarily. Low severity because the li...

Also on this path: apps/api/dms_api/routes/library.py:131 + packages/cortex_client/cortex_client/gate.py:44-54 - the gate payload drops every metadata key except task_id and filled_template, so Cortex F5 never sees the Space on this route. With app.state.cortex = None, compliance_gate(client=None) returns gate_unavai...

<a id="ep-studio-chunks-list"></a>
### `studio:chunks-list`

RAG-01 steward list of a Space's document chunks. Producer: apps/api/dms_api/wiring.py:76-89 list_document_chunks -> packages/core/dms_core/control_plane/document_chunks.py:224 list_chunks. Reachable: yes. GET /v1/studio/chunks is mounted unconditionally (apps/api/dms_api/app.py:147 include_router(studio.router), no flag). Default config has DATABASE_URL=None (settings.py:38), so wiring.py:83-84 returns []. With the ...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/studio.py:154 - space_id is a required Query param; it is the only Space input and the caller chooses it`<br>`packages/core/dms_core/control_plane/document_chunks.py:239 - the only restriction is WHERE tenant_id::text = %s AND space_id::text = %s; set_tenant_...` | `read-routes-own-space-check-not-shared-grant` |
| G2 ontology verification | n/a (high) | `packages/core/dms_core/control_plane/document_chunks.py:236-241 - a plain SELECT ... ORDER BY source_id, chunk_index with no aggregate, join or compu...`<br>`apps/api/dms_api/routes/studio.py:164 + wiring.py:76-89 - no ontology or executor call` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/core/dms_core/control_plane/document_chunks.py:235-241 - fixed SQL literal, only FROM dms.document_chunks (:238); all values bound as parame...` |  |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/batch_ingest.py:139-166 - chunks are written only for UNSTRUCTURED files, which skip bronze and typed ingest`<br>`packages/executor/dms_executor/document_chunks.py:14-31 - raw text extraction, no types or contract persisted` | `browse-and-chunk-routes-not-answer-paths` |
| G5 PII mask (envelope + record) | part (high) | `packages/core/dms_core/control_plane/document_chunks.py:246-254 - list returns raw content, blob_key and embed_meta; no mask call in document_chunks....`<br>`apps/api/dms_api/app.py:126,133 - only CORS and RejectIdentityHeaders middleware; no response masking` | `chunk-text-routes-unmasked` |
| G6 strict pin + served fields | n/a (high) | `packages/core/dms_core/control_plane/document_chunks.py:224-256 - list_chunks makes no model call and has no model or provider import (imports at :5-...`<br>`apps/api/dms_api/routes/studio.py:164 - returns a bare list[dict]; no envelope, no served_* fields` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/core/dms_core/control_plane/document_chunks.py:232-244 - rows read from Postgres by a local psycopg connection`<br>`apps/api/dms_api/routes/studio.py:157-164 - the only Cortex call is compliance_gate; no manifest, no /v1/contract/submit, no ledger append` | `browse-and-chunk-routes-not-answer-paths` |
| G8 named abstain on failure | part (high) | `apps/api/dms_api/app.py:135-142 + routes/studio.py:154 - a missing space_id gives a named 422; gatekeeping.py:70 - a gate refusal gives 403 with the ...`<br>`apps/api/dms_api/wiring.py:83-84 - DATABASE_URL unset returns [] with 200, unnamed (the default config)` | `browse-and-chunk-routes-not-answer-paths` |

Also on this path: packages/core/dms_core/control_plane/document_chunks.py:236-241 - list_chunks has no LIMIT or pagination, so the response is unbounded; a Space with many uploads returns every chunk in one payload (studio.py:164).

Also on this path: packages/core/dms_core/control_plane/document_chunks.py:246-254 - the list response includes blob_key (sha256 content key from batch_ingest.py:140-141) and embed_meta.dense (a 64-float content fingerprint, see :81). Low severity internal field exposure.

Also on this path: packages/core/dms_core/control_plane/document_chunks.py:22 - the stored embed_meta.dense uses per-process salted hash() (PYTHONHASHSEED is set nowhere), so the stored vectors are not comparable across processes; this makes the library:chunks-search ranking noisy, and the list route returns the mean...

<a id="ep-mcp-ask"></a>
### `mcp:ask`

MCP-01 ask tool (wraps chat_ask). Producer: apps/api/dms_api/routes/mcp.py:118-133 -> apps/api/dms_api/routes/chat.py:213 chat_ask. Reachable: flag-gated. Only when DMS_MCP=1 (default False): apps/api/dms_api/app.py:158-159 mounts the router only if settings.dms_mcp; apps/api/dms_api/settings.py:45 sets dms_mcp False; docs/ACTIVE.md:110 says DMS_MCP=0. When mo...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (high) | `apps/api/dms_api/routes/mcp.py:118-129 - space_id is an OPTIONAL tool argument (args.get('space_id')) forwarded to AskBody; the MCP wrapper never req...`<br>`apps/api/dms_api/routes/chat.py:240-241 - a named but unknown Space is 404 space_not_found; an omitted space_id skips the check entirely.` | `no-space-ask-widest-grant` |
| G2 ontology verification | **NO** (high) | `packages/executor/dms_executor/__init__.py:593-632 - VQ and pack lanes go straight to Cortex submit; neither calls Ontology.verify or load_verified_o...`<br>`packages/executor/dms_executor/demo_pack.py:40-69 - pack SQL is hard-coded text with joins and SUM measures (inventory JOIN suppliers, shipments JOIN...` | `served-sql-never-reverified-against-ontology` |
| G3 join rule + fan-out guard | **NO** (high) | `packages/executor/dms_executor/demo_pack.py:40-69 - stored multi-table pack SQL (joins) served with no _resolve_path and no fanout check.`<br>`packages/executor/dms_executor/verified_queries.py:98-120 - steward SQL may name several tables; only identifiers and hostile patterns are checked.` | `free-sql-lanes-no-fanout-guard` |
| G4 typed ingest | **NO** (high) | `packages/executor/dms_executor/bronze_sheet_ask.py:190-200,221,262 - the bronze-sheet lane computes SUM(TRY_CAST(measure AS DOUBLE)) and CAST(col AS ...`<br>`packages/executor/dms_executor/bronze.py:491-492 - write_bronze_rows (XLSX path) creates every column as VARCHAR; bronze.py:406 CSV uses DuckDB read_...` | `bronze-sheet-trycast-drops-rows-silently` |
| G5 PII mask (envelope + record) | part (medium) | `packages/executor/dms_executor/envelope.py:1626-1640 - build_answer_envelope runs fail_closed_mask_payload over text, rows, values, sources, chart an...`<br>`packages/executor/dms_executor/__init__.py:504-510 - live_ask applies mask_unknown_keys to the final envelope, which masks assumptions, suggestions, ...` | `ledger-append-carries-unmasked-sql` |
| G6 strict pin + served fields | part (high) | `packages/cortex_client/cortex_client/compute.py:814-840 - the Insights generate body is stamped with the strict pin (stamp_generate_body); :750 a 503...`<br>`packages/cortex_client/cortex_client/compute.py:710-760 - only shot.kind == 'unavailable' is refused; a mismatch (unpinned or missing served model) f...` | `contract-ask-model-call-unpinned-and-stamped-none` |
| G7 Cortex submit (manifest + ledger) | part (high) | `packages/executor/dms_executor/__init__.py:441-453,816-855 - VQ, pack and generative lanes: bind session, mint a signed manifest (manifest.py:165-194...`<br>`packages/executor/dms_executor/verified_queries.py:347-367, demo_pack.py:351-376, generative_ask.py:845-868 - ledger append through Cortex and the en...` | `followup-no-submit` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/chat.py:240-241,243-247,260-268,277-290 - named: 404 space_not_found, 403 gate reason, 503 cortex_unavailable, 403 grounding_...`<br>`apps/api/dms_api/routes/chat.py:291-335 - AskServiceError: outside_grounded_scope 403, Space-boundary ABSTAIN envelope (:316-324), else 403/409/429/5...` | `certified-lane-failure-not-named` |

Also on this path: apps/api/dms_api/routes/mcp.py:108-114 plus packages/cortex_client/cortex_client/gate.py:46-57 - the 'mcp.call' gate sends only event_id, task_id, filled_template and actor; the metadata (tool, question, space_id) built at mcp.py:110 and chat.py:243-247 is never forwarded, and gate_task_unknown is ...

Also on this path: packages/executor/dms_executor/__init__.py:193,578-589 with apps/api/dms_api/routes/mcp.py:127 - the follow-up cache _turns is process-global and keyed only by caller-chosen (session_id, space_id); any MCP caller who supplies another session's id and Space can run 'add N' or 'average of them' over ...

Also on this path: packages/executor/dms_executor/__init__.py:249-286 vs apps/api - see the other:answer-user-sql row; not reachable from MCP.

<a id="ep-mcp-preview"></a>
### `mcp:preview`

MCP-01 preview tool (wraps warehouse preview). Producer: apps/api/dms_api/routes/library.py:244 preview_wh_table -> packages/executor/dms_executor/warehouse_browse.py:114. Reachable: flag-gated - POST /v1/mcp/call {name:'preview'} exists only when DMS_MCP=1 (apps/api/dms_api/settings.py:45 default False; apps/api/dms_api/app.py:158-159 mounts the router only if set; docs/ACTIVE.md:110 says flag off)...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (high) | `apps/api/dms_api/routes/mcp.py:142-145 - space_id comes straight from the caller's arguments and is passed to preview_wh_table.`<br>`apps/api/dms_api/routes/library.py:272-274 - same warehouse_tables(space_id) membership check, 403 warehouse_not_in_space.` | `read-routes-own-space-check-not-shared-grant` |
| G2 ontology verification | n/a (medium) | `packages/executor/dms_executor/warehouse_browse.py:129-130 - COUNT(*) and SELECT * only.`<br>`apps/api/dms_api/routes/mcp.py:143-147 - wrapper adds no computation.` |  |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/warehouse_browse.py:19,122,130 - allowlisted DEMO_TABLES name into a fixed one-table template.`<br>`apps/api/dms_api/routes/mcp.py:137 - the '/' and '..' check is only lexical; the single-table guarantee comes from the executor allowlist.` |  |
| G4 typed ingest | yes(2/3) (medium) | `packages/executor/dms_executor/warehouse_browse.py:19,122 - only the six DEMO_TABLES.`<br>`packages/executor/dms_executor/demo_warehouse.py:304-430 - each created with declared column types; no cast at read (warehouse_browse.py:130).` |  |
| G5 PII mask (envelope + record) | part (high) | `apps/api/dms_api/routes/mcp.py:143-147 - the unmasked preview is returned inside {ok, name, result}.`<br>`apps/api/dms_api/routes/library.py:276-280 - no mask on the underlying route.` | `preview-and-drill-rows-unmasked` |
| G6 strict pin + served fields | n/a (high) | `apps/api/dms_api/routes/mcp.py:143-147 - returns {ok, name, result}, not an answer envelope.`<br>`packages/executor/dms_executor/warehouse_browse.py:1-17 - no model provider.` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `apps/api/dms_api/routes/mcp.py:143-145 -> library.py:276 -> warehouse_browse.py:127-137 - local DuckDB SELECT.`<br>`apps/api/dms_api/routes/mcp.py:121-126 - only an F5 compliance_gate; no submit or ledger append.` | `browse-and-chunk-routes-not-answer-paths` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/mcp.py:136-141 - bad table or limit/offset -> 400 (named status, plain-string detail).`<br>`apps/api/dms_api/routes/mcp.py:149-152 - unknown tool -> 404 {error:'unknown_tool'}.` | `browse-and-chunk-routes-not-answer-paths` |

Also on this path: apps/api/dms_api/routes/mcp.py:121-126 and library.py:257-269 - the F5 gate is called twice per preview (mcp.call, then library.preview_warehouse); neither call carries the table or Space to Cortex (gate.py:44-54).

Also on this path: apps/api/dms_api/routes/mcp.py:75-82 - McpCallIn has no Space or caller binding; space_id is a free-form argument string.

<a id="ep-insights-post-ask"></a>
### `insights:post-ask`

Hosted Cortex Insights ask pass-through (generate=false). Producer: apps/api/dms_api/routes/insights.py:182-193 -> packages/cortex_client/cortex_client/client.py:243-266 insights_ask -> packages/cortex_client/cortex_client/insights.py:229 insights_post -> _request :1.... Reachable: yes - POST /v1/insights is mounted unconditionally (apps/api/dms_api/app.py:153) and generate defaults to false, ask to true (routes/insights.py:32-33). Needs only a Cortex client, which lifespan builds (app.py:98-103)....

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | **NO** (high) | `apps/api/dms_api/routes/insights.py:35 - space_id is a free optional str; never looked up in the Space store`<br>`apps/api/dms_api/routes/insights.py:165-173 - the only pre-call check is compliance_gate(task_id='insights.ask', metadata space_id)` | `insights-routes-bypass-dms-gates` |
| G2 ontology verification | **NO** (medium) | `apps/api/dms_api/routes/insights.py:182-193 - the path hands the question to Cortex and returns its payload; no load_verified_ontology / Ontology.ver...`<br>`packages/cortex_client/cortex_client/insights.py:190-191 - a 200 body is returned verbatim, values included` | `insights-routes-bypass-dms-gates` |
| G3 join rule + fan-out guard | **NO** (medium) | `packages/cortex_client/cortex_client/insights.py:263-271 - request is sent and the response returned; no SQL parse, no _resolve_path, no fanout_refus...`<br>`apps/api/dms_api/routes/insights.py:182-193 - no multi-table check before or after the Cortex call` | `insights-routes-bypass-dms-gates` |
| G4 typed ingest | **NO** (low) | `apps/api/dms_api/routes/insights.py:182-193 - DMS names no table and consults no ingest contract on this path`<br>`packages/cortex_client/cortex_client/insights.py:210-271 - request carries only intent/question/flags/session_id/space_id; no column types are sent o...` | `insights-routes-bypass-dms-gates` |
| G5 PII mask (envelope + record) | part (medium) | `apps/api/dms_api/routes/insights.py:183-193 - the Cortex payload goes straight to honest_envelope, with no mask_payload/mask_envelope/fail_closed_mas...`<br>`packages/cortex_client/cortex_client/insights.py:124-131 - honest_envelope only forces live_5000_ci=false` | `insights-routes-return-payload-unmasked` |
| G6 strict pin + served fields | **NO** (medium) | `packages/cortex_client/cortex_client/insights.py:246-262 - the strict pin (model + strict body, X-OpenVault-Strict header) is stamped only when gener...`<br>`packages/cortex_client/cortex_client/insights.py:183-189 - interpret() runs, but only kind=='unavailable' is acted on` | `insights-routes-bypass-dms-gates` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/cortex_client/cortex_client/insights.py:1-7 - off-contract sibling to /v1/contract/*; the path is POST /v1/insights, not /v1/contract/submit`<br>`packages/cortex_client/cortex_client/insights.py:263-271 - plain POST with no manifest` | `insights-routes-bypass-dms-gates` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/insights.py:156-158 - empty intent returns HTTP 400`<br>`apps/api/dms_api/routes/insights.py:57-74,88-94 - Cortex missing returns REFUSE, values [], code cortex_unavailable (503)` | `insights-routes-bypass-dms-gates` |

Also on this path: packages/cortex_client/cortex_client/insights.py:103 and tests/test_insights_bearer_01.py:274-284 - generate=false (the ask lane) still sends the published demo viewer key 'dms-demo-viewer-key' (settings.py:36), so an unconfigured DMS reaches Cortex with the public key (tracked: dms#273 KEY-01, ope...

Also on this path: apps/api/dms_api/routes/insights.py:77-79 - _from_error forwards the Cortex error body via honest_envelope(exc.payload). redact_secrets runs only on message text, not on the forwarded payload (a 'refused' string with an ov_/sk- token would pass through). New, low.

Also on this path: apps/api/dms_api/routes/insights.py:26,172 - the gate's no-decision reasons (gate_unavailable, gate_task_unknown) proceed on this figure-serving POST, so F5 is fail-open here. This is deliberate per the comment at :170-171 and matches chat.ask, but it is a gap against the 'F5 gate' intent.

<a id="ep-insights-post-generate"></a>
### `insights:post-generate`

Hosted Cortex Insights generate pass-through (generate=true). Producer: apps/api/dms_api/routes/insights.py:175-193 -> packages/cortex_client/cortex_client/insights.py:229-271 insights_post (generate branch). Reachable: yes, but config-gated: same route as post-ask (routes/insights.py:153-154) with body generate=true. On default settings cortex_api_key == DEMO_VIEWER_KEY (settings.py:36, cortex_client/insights.py:21), so insights_post ...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | **NO** (high) | `apps/api/dms_api/routes/insights.py:35,165-173,182-193 - space_id is an unvalidated optional string; the only check is F5 compliance_gate, which does...`<br>`packages/cortex_client/cortex_client/gate.py:44-54 - gate payload drops meta['space_id']` | `insights-routes-bypass-dms-gates` |
| G2 ontology verification | **NO** (medium) | `apps/api/dms_api/routes/insights.py:182-193 - no load_verified_ontology/Ontology.verify call on this path; payload is returned as-is`<br>`packages/cortex_client/cortex_client/insights.py:263-271 - generate POST result is returned without any DMS-side ontology compile or verification` | `insights-routes-bypass-dms-gates` |
| G3 join rule + fan-out guard | **NO** (medium) | `packages/cortex_client/cortex_client/insights.py:263-271 - generate response returned verbatim; no _resolve_path / fanout_refused`<br>`apps/api/dms_api/routes/insights.py:182-193 - no multi-table check` | `insights-routes-bypass-dms-gates` |
| G4 typed ingest | **NO** (low) | `apps/api/dms_api/routes/insights.py:182-193 - DMS consults no ingest contract or declared types`<br>`packages/cortex_client/cortex_client/insights.py:246-254 - request carries no column-type information` | `insights-routes-bypass-dms-gates` |
| G5 PII mask (envelope + record) | part (medium) | `apps/api/dms_api/routes/insights.py:183-193 - Cortex generate payload passes to honest_envelope, no masker`<br>`packages/cortex_client/cortex_client/insights.py:124-131 - honest_envelope only forces live_5000_ci=false` | `insights-routes-return-payload-unmasked` |
| G6 strict pin + served fields | part (medium) | `packages/cortex_client/cortex_client/insights.py:256-262 - generate=true stamps model + strict:true in the body and X-OpenVault-Strict: true`<br>`packages/cortex_client/cortex_client/strict_pin.py:118-136 - stamp_generate_body/headers set the pinned model; a caller-error pin sets pin_refusal` | `insights-routes-bypass-dms-gates` |
| G7 Cortex submit (manifest + ledger) | **NO** (medium) | `packages/cortex_client/cortex_client/insights.py:1-7 - off-contract sibling to /v1/contract/*`<br>`packages/cortex_client/cortex_client/insights.py:263-271 - plain POST /v1/insights, no manifest` | `insights-routes-bypass-dms-gates` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/routes/insights.py:175-180 - OpenVault unreachable returns REFUSE with code openvault_unavailable (503)`<br>`packages/cortex_client/cortex_client/insights.py:243-245 - demo or missing key returns ABSTAIN insights_bearer_missing; insecure transport returns in...` | `insights-routes-bypass-dms-gates` |

Also on this path: packages/cortex_client/cortex_client/insights.py:62-82 plus 98-107 - the bearer/transport refusal is based on the DMS-configured key only; the caller's own identity plays no part (no per-user credential, DR-0004 single-tenant). Tracked broadly by dms#268 BANK-01.

Also on this path: apps/api/dms_api/routes/insights.py:175 - the OpenVault probe runs on every generate request (outbound HTTP to settings.openvault_url with 1.5s timeout) before Cortex is called; a 401/403 from OpenVault counts as reachable (:50). Informational.

Also on this path: packages/cortex_client/cortex_client/insights.py:256-262 and strict_pin.py:130-136 - the pin header and body model are added, but DMS_STRICT_PROVIDER is only validated locally (strict_pin.py:101-103) and never sent, so the pinned provider is not enforced on the wire.

<a id="ep-insights-get-ontology-ranking"></a>
### `insights:get-ontology-ranking`

Hosted Cortex Insights ontology ranking pass-through for a question. Producer: packages/cortex_client/cortex_client/client.py:234-241 insights_ontology -> packages/cortex_client/cortex_client/insights.py:210 insights_get -> _request :150 -> honest_envelope :124. Reachable: yes - GET /v1/insights/ontology?q= is mounted unconditionally (routes/insights.py:137, app.py:153); needs a non-empty q and a Cortex client. No gate, no auth beyond RejectIdentityHeadersMiddleware. The UI does not call ...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (medium) | `apps/api/dms_api/routes/insights.py:137-150 - the handler reads no DMS data; it forwards q to Cortex`<br>`packages/cortex_client/cortex_client/client.py:234-241 - GET /v1/insights/ontology with params={'q': q} only (no space_id, no session)` |  |
| G2 ontology verification | n/a (medium) | `apps/api/dms_api/routes/insights.py:147 - returns honest_envelope(cortex.insights_ontology(intent)) with no figure computed`<br>`packages/cortex_client/cortex_client/compute.py:676-682 - consumers treat the response as an ontology ranking to be compiled later against the DMS on...` |  |
| G3 join rule + fan-out guard | n/a (medium) | `apps/api/dms_api/routes/insights.py:137-150 - no SQL executed or returned by DMS`<br>`packages/cortex_client/cortex_client/client.py:234-241 - the only input is a q string; the response is an ontology ranking` |  |
| G4 typed ingest | n/a (medium) | `apps/api/dms_api/routes/insights.py:137-150 - no table read`<br>`docs/ACTIVE.md:212 - ranking comes from Cortex's YAML ontology` |  |
| G5 PII mask (envelope + record) | part (low) | `apps/api/dms_api/routes/insights.py:147 - Cortex payload returned through honest_envelope; no masker call`<br>`packages/cortex_client/cortex_client/insights.py:124-131 - honest_envelope only forces live_5000_ci=false` | `insights-routes-return-payload-unmasked` |
| G6 strict pin + served fields | **NO** (low) | `apps/api/dms_api/routes/insights.py:147 - returns an Insights envelope via honest_envelope`<br>`packages/cortex_client/cortex_client/client.py:234-241 - GET with q only: no model, no strict stamp` | `insights-routes-bypass-dms-gates` |
| G7 Cortex submit (manifest + ledger) | n/a (medium) | `apps/api/dms_api/routes/insights.py:147 - the route returns Cortex's ranking and no query result`<br>`tests/test_gen_path_climb.py:377-383 - ontology payload is metric ids only` |  |
| G8 named abstain on failure | part(2/3) (medium) | `apps/api/dms_api/routes/insights.py:139-141 - empty q returns HTTP 400`<br>`apps/api/dms_api/routes/insights.py:88-94,142-144 - Cortex missing returns REFUSE, values [], code cortex_unavailable (503)` |  |

Also on this path: apps/api/dms_api/routes/insights.py:137-150 - the route returns Cortex's body verbatim and does not strip 'values' (the ask lane does: cortex_client/compute.py:592-599). The notes call this PLAUSIBLE only. Confirmed at this commit that DMS code does not prevent it; the G7/G1/G3 N_A verdicts depend ...

Also on this path: apps/api/dms_api/routes/insights.py:138 and packages/cortex_client/cortex_client/client.py:239 - the user's question is carried in the URL query string (q=), so it lands in uvicorn's default access log (apps/api/Dockerfile:23) and in Cortex's request log. A POST body would not. If a user types a na...

Also on this path: apps/api/dms_api/routes/insights.py:97-150 - none of the four GET /v1/insights* routes calls compliance_gate. CLAUDE.md rule 8 requires it only on mutation routes, so this is within the rule, but it means F5 cannot refuse them.

<a id="ep-studio-xlsx-orch-crosscheck"></a>
### `studio:xlsx-orch-crosscheck`

XLSX-ORCH crosscheck with source-grid oracle figures. Producer: packages/executor/dms_executor/xlsx_orch.py:69 run_crosscheck -> packages/core/dms_core/xlsx_orch.py:296 crosscheck_pack -> :195 oracle_from_sheets (+ strengthen_pack :250). Reachable: yes - POST /v1/studio/xlsx-orch/crosscheck is mounted unconditionally (apps/api/dms_api/app.py:147 include_router(studio.router), no flag). It needs compliance_gate to allow (studio.py:194-200; enforce at apps/api/dms_a...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/studio.py:167-170 - XlsxOrchCrosscheckIn has pack/workbook_path/pack_id and no space_id field at all`<br>`apps/api/dms_api/routes/studio.py:187-205 - handler is compliance_gate + enforce then xlsx_orch_crosscheck(body.pack, workbook_path=...). No grantabl...` | `xlsx-orch-cross-space-path-read` |
| G2 ontology verification | **NO** (medium) | `packages/core/dms_core/xlsx_orch.py:195-247 - oracle_from_sheets computes AVG(cost) and COUNT where OnTime is truthy, in Python, over one sheet grid`<br>`packages/core/dms_core/xlsx_orch.py:73-78,216-217 - the 'cost' and 'OnTime' columns are chosen by regex over header text (_COST_COL/_ONTINE_COL), fir...` | `xlsx-orch-outside-governed-path` |
| G3 join rule + fan-out guard | n/a (high) | `packages/core/dms_core/xlsx_orch.py:195-247 - the oracle indexes exactly one grid (by_name.get(source_sheet) or the first sheet with OnTime+cost head...`<br>`packages/executor/dms_executor/xlsx_orch.py:3 and :12-24 - docstring says 'No duckdb.execute' and imports contain no duckdb or SQL` |  |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/triage.py:368-384 - parse_xlsx_grids returns raw openpyxl cell values (read_only, data_only), with no declared or infe...`<br>`packages/core/dms_core/xlsx_orch.py:98-116 - _truthy and _as_float coerce strings at request time` | `xlsx-orch-coercion-and-false-ok` |
| G5 PII mask (envelope + record) | part (medium) | `apps/api/dms_api/routes/studio.py:187-205 - returns the producer dict as dict[str, Any] with no mask_payload / mask_envelope / fail_closed_mask_paylo...`<br>`packages/core/dms_core/xlsx_orch.py:239-247,340-351 - the returned source_oracle includes avg_cost, counts and the verbatim header strings ontime_col...` | `xlsx-orch-unmasked-and-raw-persist` |
| G6 strict pin + served fields | n/a (high) | `apps/api/dms_api/routes/studio.py:187-205 - returns a plain dict; no build_answer_envelope / assert_envelope_valid on this path`<br>`packages/core/dms_core/xlsx_orch.py:10-15 and packages/executor/dms_executor/xlsx_orch.py:12-24 - imports show no model client, provider or pin module` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/core/dms_core/xlsx_orch.py:238-243 - avg = sum(costs) / len(costs); round(avg, 4); ontime_count/total_count are Python len()s. This is local...`<br>`packages/core/dms_core/xlsx_orch.py:340-351 - those figures are returned as source_oracle and inside strengthened_pack.dms_source_oracle (:283-288)` | `xlsx-orch-outside-governed-path` |
| G8 named abstain on failure | part (high) | `apps/api/dms_api/gatekeeping.py:53-70 and studio.py:194-200 - gate refusal or unreachable gate gives a named HTTP 403 (reason in detail)`<br>`packages/core/dms_core/xlsx_orch.py:311-335 - pack_required, workbook_unverified and the first fatal issue code come back as named reasons with statu...` | `xlsx-orch-coercion-and-false-ok` |

Also on this path: packages/core/dms_core/xlsx_orch.py:226-230,242 - rows with a true OnTime flag but an unparseable cost are silently dropped. ontime_count is len(costs), not the number of OnTime rows, while total_count counts all body rows (:243). Both the count and the average are plausible and wrong with no signa...

Also on this path: packages/core/dms_core/xlsx_orch.py:39-40 - 'test_fixture' is an accepted producer in production code. The pack's paste_owner/producer is caller-asserted and the forbidden-producer refusal (:146-148) is trivially bypassed.

Also on this path: The allowlisted-path read has no size cap (executor xlsx_orch.py:57) and no Space tie. This is a minor DoS surface and is covered by the G1 PARTIAL.

<a id="ep-studio-xlsx-orch-extract"></a>
### `studio:xlsx-orch-extract`

XLSX-ORCH extract/store with parsed Analysis figures. Producer: packages/executor/dms_executor/xlsx_orch.py:90 run_extract -> packages/core/dms_core/xlsx_orch.py:354 inspect_result_grids. Reachable: yes - POST /v1/studio/xlsx-orch/extract is mounted unconditionally (apps/api/dms_api/app.py:147). It needs compliance_gate to allow (studio.py:215-225; enforce is fail-closed 403 for mutation=True at gatekeeping.py:53-7...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/studio.py:173-177,208-231 - space_id is a free-text optional body field defaulting to 'company-default' and is used only as a...`<br>`packages/executor/dms_executor/xlsx_orch.py:102-126 - the source is read from any file that resolve_allowlisted_file accepts` | `xlsx-orch-cross-space-path-read` |
| G2 ontology verification | **NO** (medium) | `packages/core/dms_core/xlsx_orch.py:354-388 - inspect_result_grids returns analysis_numbers, analysis_labeled and export_row_count (a len() of the Ex...`<br>`packages/executor/dms_executor/xlsx_orch.py:135,147-150 - attached to the response as inspected / families / missing_sheets` | `xlsx-orch-outside-governed-path` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/xlsx_orch.py:3 and :12-24 - 'No duckdb.execute'; no SQL or duckdb import`<br>`packages/core/dms_core/xlsx_orch.py:354-388 - the inspection is Python loops over the Analysis and Export grids, and the Export sheet is only len()-c...` |  |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/triage.py:368-384 - parse_xlsx_grids yields raw cell values with no persisted types`<br>`packages/core/dms_core/xlsx_orch.py:105-116,368-378 - Analysis cells are coerced ad hoc with _as_float at request time` | `xlsx-orch-coercion-and-false-ok` |
| G5 PII mask (envelope + record) | **NO** (high) | `apps/api/dms_api/routes/studio.py:208-231 - raw dict[str, Any] return; no masker call`<br>`packages/core/dms_core/xlsx_orch.py:371-374,379-388 - analysis_labeled keys are arbitrary cell text from Analysis column A, returned verbatim with sh...` | `xlsx-orch-unmasked-and-raw-persist` |
| G6 strict pin + served fields | n/a (high) | `apps/api/dms_api/routes/studio.py:208-231 - returns a plain dict, no envelope`<br>`packages/executor/dms_executor/xlsx_orch.py:12-24 and packages/core/dms_core/xlsx_orch.py:10-15 - no model client, provider or pin imports` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/core/dms_core/xlsx_orch.py:368-388 - figures are parsed locally from posted bytes (and export_row_count is a local len())`<br>`packages/executor/dms_executor/xlsx_orch.py:135-150 - returned in the response with no submit` | `xlsx-orch-outside-governed-path` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/gatekeeping.py:53-70 and studio.py:215-225 - gate refusal gives a named 403`<br>`packages/executor/dms_executor/xlsx_orch.py:104-123 - empty result_path gives awaiting_pointer_receipt; outside the allowlist gives path_not_allowlis...` | `xlsx-orch-coercion-and-false-ok` |

Also on this path: packages/core/dms_core/xlsx_orch.py:39-40,532 - 'test_fixture' is accepted as a producer in production, and 'producer' is a caller-asserted string with no verification. It is recorded as provenance in artifact.json (:554). The forbidden-producer (MCP/openpyxl) refusal is bypassed by sending produce...

Also on this path: packages/executor/dms_executor/xlsx_orch.py:129-134 - the xlsx_read_error response echoes str(exc)[:200], so parser exception text goes back to the caller.

Also on this path: packages/core/dms_core/xlsx_orch.py:488-500 - _safe_component maps distinct space_id values (and pack_ids) to the same directory ('a b' and 'a_b'). A later extract with the same space/pack overwrites the stored file and artifact.json with no versioning.

<a id="ep-studio-xlsx-orch-golden"></a>
### `studio:xlsx-orch-golden`

XLSX-ORCH FRTR golden evaluation figures. Producer: packages/executor/dms_executor/xlsx_orch.py:153 run_golden -> packages/core/dms_core/xlsx_orch.py:405 evaluate_frtr_golden. Reachable: yes - POST /v1/studio/xlsx-orch/golden is mounted unconditionally (apps/api/dms_api/app.py:147). It needs compliance_gate to allow (studio.py:241-247; enforce is fail-closed 403 at gatekeeping.py:53-70). No UI or script...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | part (medium) | `apps/api/dms_api/routes/studio.py:180-184,234-253 - space_id is an optional free-text body field defaulting to 'company-default'; there is no Space r...`<br>`packages/executor/dms_executor/xlsx_orch.py:161-168 - load_artifact_meta(space_docs_root, space_id=<caller-chosen>, pack_id) selects whichever Space'...` | `xlsx-orch-cross-space-path-read` |
| G2 ontology verification | **NO** (medium) | `packages/core/dms_core/xlsx_orch.py:33-35,439-448 - avg/ontime/total are taken from labeled Analysis cells (or the nearest cell) and compared to hard...`<br>`packages/core/dms_core/xlsx_orch.py:463-485 - avg_cost, ontime_count, total_count, export_row_count are returned on both miss and pass` | `xlsx-orch-outside-governed-path` |
| G3 join rule + fan-out guard | n/a (high) | `packages/executor/dms_executor/xlsx_orch.py:3 and :12-24 - 'No duckdb.execute'; no SQL or duckdb import`<br>`packages/core/dms_core/xlsx_orch.py:405-485 - evaluate_frtr_golden compares Python numbers and uses len() on the Export sheet (:456, via inspect_resu...` |  |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/xlsx_orch.py:178 and packages/executor/dms_executor/triage.py:368-384 - the stored workbook is re-parsed into raw cell...`<br>`packages/core/dms_core/xlsx_orch.py:105-116,368-378 - numeric meaning is decided per request by _as_float` | `xlsx-orch-coercion-and-false-ok` |
| G5 PII mask (envelope + record) | part (medium) | `apps/api/dms_api/routes/studio.py:234-253 - raw dict[str, Any] return; no masker call`<br>`packages/core/dms_core/xlsx_orch.py:463-474 - on golden_miss the response includes inspected (sheetnames, families, analysis_labeled with cell label ...` | `xlsx-orch-unmasked-and-raw-persist` |
| G6 strict pin + served fields | n/a (high) | `apps/api/dms_api/routes/studio.py:234-253 - plain dict return; no envelope on this path`<br>`packages/core/dms_core/xlsx_orch.py:10-15 and packages/executor/dms_executor/xlsx_orch.py:12-24 - no model client, provider or pin imports` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/core/dms_core/xlsx_orch.py:439-448,463-485 - the figures are read or selected locally from the workbook grids and returned`<br>`packages/executor/dms_executor/xlsx_orch.py:153-184 - no manifest, submit or ledger call on this path` | `xlsx-orch-outside-governed-path` |
| G8 named abstain on failure | part (medium) | `apps/api/dms_api/gatekeeping.py:53-70 and studio.py:241-247 - gate refusal is a named 403`<br>`packages/executor/dms_executor/xlsx_orch.py:168-182 - no artifact gives awaiting_pointer_receipt; not allowlisted gives path_not_allowlisted; unreada...` | `xlsx-orch-coercion-and-false-ok` |

Also on this path: packages/core/dms_core/xlsx_orch.py:391-395 - _pick_labeled returns the first label that contains any key as a substring. 'total' matches 'total cost', and 'ontime' matches 'ontime avg cost', so a wrong cell can be taken as the on-time count and produce a golden_miss on a correct sheet. This is a f...

Also on this path: packages/core/dms_core/xlsx_orch.py:39-40 and executor xlsx_orch.py:183 - producer is caller-asserted, and 'test_fixture' is an accepted producer in production. The XLSX-ORCH-12 refusal of MCP/openpyxl producers is trivially bypassed by sending producer='pointer_copilot' or 'test_fixture'.

Also on this path: packages/executor/dms_executor/xlsx_orch.py:163-167 and core xlsx_orch.py:568-580 - load_artifact_meta only catches JSONDecodeError, so a corrupted or non-UTF-8 artifact.json (or an OSError on read_text) is an unhandled 500. Low likelihood, since DMS writes that file itself.

<a id="ep-cli-insights-report"></a>
### `cli:insights-report`

Deterministic insights report CLI. Producer: scripts/insights.py:199 mine (main :377). Reachable: no - operator CLI only. No HTTP route, UI or in-repo caller in apps/ or packages/ (only importer is tests/test_insights_brief.py:26-27). It needs data/lake, which is gitignored (.gitignore:13 'data/', absent in this wor...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (medium) | `scripts/load_adventureworks.py:75-78 - DATABASES is a fixed list of three AdventureWorks .bak files; this is the lake's whole source`<br>`scripts/load_adventureworks.py:500 - the only writer of extract_manifest.json in the repo (grep over *.py/*.md/*.json/*.yml found no other writer)` |  |
| G2 ontology verification | yes (high) | `scripts/insights.py:203-207 - from_manifest builds the ontology, onto.verify(con) runs, and any violation returns an error before any figure is compu...`<br>`packages/executor/dms_executor/ontology.py:910 - verify() sets self.verified = not violations` |  |
| G3 join rule + fan-out guard | yes (high) | `scripts/insights.py:239-246 - every grouping goes through onto.compile; a Refusal is recorded with its reason, not served`<br>`packages/executor/dms_executor/ontology.py:1432-1435 - compile() resolves any non-grain group_by object via _resolve_path and returns a Refusal if it...` |  |
| G4 typed ingest | part (medium) | `scripts/load_adventureworks.py:458,466 - the lake is pd.read_sql(...) over pymssql, then frame.to_parquet, with no dtype handling and no DMS contract`<br>`scripts/insights.py:149-151 - dimension discovery relies on DuckDB DESCRIBE reporting VARCHAR for the parquet columns` | `operator-cli-outside-governed-path` |
| G5 PII mask (envelope + record) | **NO** (high) | `scripts/insights.py:415-417 - stdout prints each headline (built from group labels, :288-289,307) and the top-3 label=value pairs unmasked`<br>`scripts/insights.py:430 - args.json.write_text(json.dumps(report ...)) persists rows [[label, value] x10], SQL, caveats and columns_excluded unmasked` | `operator-cli-outside-governed-path` |
| G6 strict pin + served fields | n/a (high) | `scripts/insights.py:18-19 - 'There is no model in the loop'`<br>`scripts/insights.py:44-51,200 - the only imports are argparse/json/sys/dataclasses/pathlib/typing, duckdb and dms_executor.ontology; no model provide...` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `scripts/insights.py:402-404 - duckdb.connect(':memory:') opens a local engine inside the DMS process`<br>`scripts/insights.py:248 - rows = con.execute(got.sql).fetchall() runs the answer SQL locally over read_parquet relations` | `operator-cli-outside-governed-path` |
| G8 named abstain on failure | part (medium) | `scripts/insights.py:393-400 - a missing manifest or an unlisted database prints 'FAIL ...' and exits 2`<br>`scripts/insights.py:204-209 - verify violations, including an unreadable relation (ontology.py:733-737 relation_readable), return a named error and e...` | `operator-cli-outside-governed-path` |

Also on this path: tests/invariants/test_boundaries.py:17-20 - hard rule 7 scans only apps/api and packages, and :142-149 flags only duckdb.execute or <x>.duckdb.execute. scripts/insights.py runs DuckDB locally via con.execute (:149,152,225,248) and is outside the rule's scan roots and its pattern.

Also on this path: scripts/insights.py:212 - onto.verified = True is a manual override of the gate flag, with a stale comment (ontology.py:508-526: add_measure never resets it). It is a no-op today, but it would hide a future add_measure that invalidates verification.

Also on this path: scripts/insights.py:430 vs :432-439 - the JSON report is written before the failure checks, so a run that exits 1 still leaves a report file on disk that brief.py will accept (brief.py has no reference to broken or conserves).

<a id="ep-cli-brief-deck"></a>
### `cli:brief-deck`

Slide deck / HTML brief generator from an insights report. Producer: scripts/brief.py:296 build_pptx and :268 build_html (main :411). Reachable: no - operator CLI only (python scripts/brief.py --insights <json> --pptx/--html). No HTTP route or UI, and no caller in apps/ or packages/; the only importer is tests/test_insights_brief.py:26-28. python-pptx is a dev-e...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | n/a (medium) | `scripts/brief.py:426-431 - the only input is json.loads of an operator-supplied --insights file; no database, manifest, lake or Space is opened`<br>`scripts/brief.py:49-57 - the imports are argparse/html/json/math/re/sys/pathlib/typing; no duckdb, pandas or dms_executor` |  |
| G2 ontology verification | **NO** (high) | `scripts/brief.py:426-436 - the report is json.loads of any file, then only validate_report runs`<br>`scripts/brief.py:182-263 - validate_report checks field presence, types, 2 dp, rows-sum <= total and n_groups; it never calls Ontology.verify, never ...` | `operator-cli-outside-governed-path` |
| G3 join rule + fan-out guard | **NO** (medium) | `scripts/brief.py:206-212 - 'sql' is only required to be a non-empty string; its content is never parsed or checked`<br>`scripts/brief.py:286 and :334 - the SQL text is shown in the HTML <pre> and pptx notes as the provenance of the figures, never executed or screened` | `operator-cli-outside-governed-path` |
| G4 typed ingest | n/a (high) | `scripts/brief.py:426-431 - the only data read is one JSON file`<br>`scripts/brief.py:49-57 - no duckdb, pandas or parquet import` |  |
| G5 PII mask (envelope + record) | **NO** (high) | `scripts/brief.py:282 - HTML rows emit _esc(lbl); _esc (:78-80) is html.escape, which is escaping, not PII masking`<br>`scripts/brief.py:314,326,332 - the pptx headline, label cells and footer are written from report text with str(), unmasked` | `operator-cli-outside-governed-path` |
| G6 strict pin + served fields | n/a (high) | `scripts/brief.py:49-57 - the imports are argparse/html/json/math/re/sys/pathlib/typing, plus pptx inside the builders; no model provider, strict-pin ...`<br>`scripts/brief.py:41-42 - 'It does not phrase.'; a model is only a future option` |  |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `scripts/brief.py:428 - the figures are read from the JSON file and passed through`<br>`scripts/brief.py:137 - the remainder figure is computed locally with math.fsum and round, and is not a report field` | `operator-cli-outside-governed-path` |
| G8 named abstain on failure | part (medium) | `scripts/brief.py:426-431 - an unreadable or invalid JSON file prints 'FAIL report: cannot read ...' and exits 1`<br>`scripts/brief.py:432-436 - validate_report problems print 'FAIL <insight id>: <what is wrong>' and exit 1` | `operator-cli-outside-governed-path` |

Also on this path: scripts/brief.py:163-166 and :182-263 - the provenance claim 'Every figure compiled and conserved.' is unconditional. validate_report never reads report['broken'] or insight['conserves'] (grep for broken/conserves in brief.py finds only the docstring word 'unbroken' at :9). scripts/insights.py:430 ...

Also on this path: scripts/brief.py:385-398 - deck_numbers scans slide.shapes text frames and tables only. The speaker notes holding the SQL (:334) are not read back, so numbers in the notes are not checked.

Also on this path: scripts/brief.py:401-405 - verify_deck compares sets of numeric tokens, so it cannot catch figures swapped between rows or between slides, only a token the report lacks.

<a id="ep-other-answer-user-sql"></a>
### `other:answer-user-sql`

Executor.answer_user_sql (user SQL -> L2 envelope on local DuckDB), dormant. Producer: packages/executor/dms_executor/__init__.py:256-286 answer_user_sql (Executor.execute :249-254). Reachable: no. Dormant at this commit. No HTTP route, MCP tool, UI surface or script calls Executor.answer_user_sql: a grep of the whole worktree (excluding docs/subagents_findings) finds only the definition at packages/executor/d...

| Gate | Status | Citations | Gap |
|---|---|---|---|
| G1 grant / Space check | **NO** (high) | `packages/executor/dms_executor/__init__.py:256-286 - answer_user_sql uses space_id only as a label passed to build_answer_envelope (:277-282); it nev...`<br>`packages/executor/dms_executor/__init__.py:249-254 - execute() = reject_hostile_chat_sql, ensure_demo_warehouse, execute_sql(path=self._warehouse): a...` | `answer-user-sql-unwired` |
| G2 ontology verification | **NO** (medium) | `packages/executor/dms_executor/__init__.py:256-286 - user SQL is executed and its rows shipped as a figure under badge L2_VALIDATED; there is no onto...` | `answer-user-sql-unwired` |
| G3 join rule + fan-out guard | **NO** (high) | `packages/executor/dms_executor/manifest.py:262-322 - the only checks are hostile patterns, UNNEST shadowing and SELECT/WITH/( head; nothing limits th...`<br>`packages/executor/dms_executor/__init__.py:249-254,271 - execute() runs the statement as given.` | `answer-user-sql-unwired` |
| G4 typed ingest | **NO** (medium) | `packages/executor/dms_executor/__init__.py:249-254 - the local DuckDB file is read with no check that tables came from typed ingest; the user's SQL m...`<br>`packages/executor/dms_executor/bronze.py:491-492 - XLSX bronze tables are created all-VARCHAR, so any CAST or aggregation in user SQL happens at ques...` | `answer-user-sql-unwired` |
| G5 PII mask (envelope + record) | yes (medium) | `packages/executor/dms_executor/__init__.py:271-285 - the result goes through build_answer_envelope, which masks text, rows, values, sources, chart an...`<br>`packages/executor/dms_executor/__init__.py:249-286 - the function stores nothing: no _store_turn, no ledger append, no file or cache write; the only ...` |  |
| G6 strict pin + served fields | **NO** (medium) | `packages/executor/dms_executor/__init__.py:256-286 - returns an envelope, but with_served_attribution is not called on this path.`<br>`packages/executor/dms_executor/__init__.py:504-510 - with_served_attribution is applied only inside live_ask; grep of packages and apps finds no othe...` | `answer-user-sql-unwired` |
| G7 Cortex submit (manifest + ledger) | **NO** (high) | `packages/executor/dms_executor/__init__.py:249-254 - execute() runs the SQL via execute_sql (demo_warehouse.py:464-500) on the DMS-local DuckDB; no m...`<br>`packages/executor/dms_executor/__init__.py:271-285 - the envelope is badged L2_VALIDATED with audit_id 'ans_user_sql' (envelope.py:1712 falls back to...` | `answer-user-sql-unwired` |
| G8 named abstain on failure | part (medium) | `packages/executor/dms_executor/__init__.py:262-270 and envelope.py:1830-1858 - a real $as_of placeholder returns a named ABSTAIN (reserved_param:as_o...`<br>`packages/executor/dms_executor/envelope.py:1453-1460 - an executed query with zero rows is demoted to ABSTAIN with named text (hard rule 12).` | `answer-user-sql-unwired` |

Also on this path: packages/executor/dms_executor/manifest.py:268-282 - the DMS hostile list names read_parquet, read_csv, read_json, parquet_scan, ATTACH, COPY and similar but not read_text, read_blob, glob, read_ndjson or similar file-reading table functions; for answer_user_sql this lexical list is the only barrie...

Also on this path: packages/executor/dms_executor/__init__.py:250-253 - execute() calls ensure_demo_warehouse, which reseeds (DROPs and recreates the six demo tables) on the first call per process; user SQL against a warehouse file shared with other work may therefore see reseeded demo data.

Also on this path: The method is a public member of Executor with no caller; per hard rule 6 and the file law it should be deleted or kept behind a lane with the shared grant function and submit, otherwise a future route would inherit all eight gaps.

## For the PRD (questions, not rulings; no ticket is minted from this)

- A-0007 closed the Space-less read on the Library routes, but the ask path still resolves a request with no `space_id` to every demo table (including `alerts`, which no Space grants) plus every Space's uploads (`no-space-ask-widest-grant`). Is that intended, or should it refuse `no_space` like the bronze lane?
- FOLLOWUP-CONTRACT-01 as ruled covers the missing submit and the shared grant function. The matrix finds three more follow-up defects it does not name: the turn cache is keyed only by caller-supplied `session_id` (no principal, DR-0004), the answer is relabelled RM whatever the unit, and a failed turn can leave a stale prior figure. Do they belong under that ruling?
- Bronze lane: a table whose name is also a `warehouse_`-prefixed alias is treated as granted by `table_is_granted` (`bronze-grant-warehouse-alias-collision`, test in `test_gap_grants.py`), and the lane mints `L0_CERTIFIED` locally with no Cortex submit or ledger entry (hard rule 3). Does either belong under BRONZE-GRANT-01 / BRONZE-WAREHOUSE-01?
- Hard rule 11: `DMS_ASK_MODE=demo` and the demo fallback paths answer with demo numbers and no Space grant; the demo-mode path sets no banner. With `DMS_DEMO_FALLBACK=1`, three 403 policy codes (`manifest_malformed`, `sql_not_analyzable`, `manifest_not_yet_valid`) and a refused verified-query submit come back as demo numbers instead of an error.
- Masking: previews, drillthrough, chunk search, `POST /v1/insights`, the audit receipt and the ledger SQL payload skip the personal-data masker. Previews exist so a steward can see raw rows; is that exemption intended, and does it extend to the others?
- G2 as written asks for ontology verification at serving time. Stored (verified-query / pack) SQL is certified by a person and never re-verified; the generative lane can ship over a failed-verify ontology (#258). Which is the authority?
- Zero-row guard: rule 12's net fires on an empty set, but an aggregate that returns one row of NULL or 0 ships green (`rule12-aggregate-zero-row-green`).
- G6 has no serving-time check of served fields: the only checker is in the offline scorer (#337 touches it). Is a runtime check wanted?

### Related open work, not touched here

- #338 (draft, another session) is a different audit, Plan C: 338 adversarial SQL cases through the gate with strict-xfail tests in `tests/redteam/`. Its findings and this matrix overlap on L2 SQL (fan-out, zero-row); no file overlaps.
- #331 GRANT-READ-02 would close part of `ungranted-tick-dropped-not-refused`; #336 SERVING-PRECHECK-01 and #337 PIN-NOMODEL-01 touch the scorer side of G6. None is on main, so none turns a cell into yes here.

## Tracer contradictions to re-check

1. All paths append the raw SQL text to the Cortex ledger. Three cells fail it, two call it clean (multi-grain yes; named-abstain says the ledger payload has no cell values). Pack is defensible only because its SQL has no user literals. (cells: chat-ask:generative-multi-grain G5 (yes), chat-ask:generative-insights-sql G5 (partial), chat-ask:generative-ontology-plan G5 (partial), chat-ask:generative-named-abstain G5 (calls the ledger record ...; shared code: generative_ask.py:845 ledger_append({'sql': sql, ...}) in _submit_validated (called at :985 multi-grain, :1284 insights-sql, :1454 ontology-plan); executor/__i...)
2. Six cells fail the receipt; the other abstain-envelope cells and the two cells whose own lane is contract-ask score yes or ignore it. Verdicts depend on whether the tracer looked at the receipt. (cells: chat-ask:generative-named-abstain G5 (partial), chat-ask:cortex-contract-ask G5 (partial), chat-ask:cortex-doc-retrieval G5 (partial), export:xlsx G5 (partial), export:bi G5 (partial), ui:share-answe...; shared code: pii.py:201-211 (_HANDLED_KEYS contains audit_receipt), pii.py:1068-1076 (mask_envelope rewrites only include.rows), executor/__init__.py:504-510 (live_ask scan...)
3. Tracers use 'zero-row' for two shapes: empty result (demoted) and one aggregate row of 0/NULL (not demoted). Two cells treat the net as sufficient. (cells: chat-ask:cortex-contract-ask G8 (partial: aggregate zero row ships), chat-ask:cascade-attach G8 (partial: wrong-encoding zero row keeps badge), ui:exclusion-auto-confirm G8 (lists zero-row demotion a...; shared code: envelope.py:1450-1460 (hard rule 12 net fires only when rows_out is empty; comment 'COUNT(*) returning one zero-row is fine').)
4. Pass-through figures are n_a for G2/G3 on exports but 'no' on insights and xlsx-orch; exports are scored 'no' on G7 for the same pass-through. (cells: export:xlsx G2/G3 (n_a: no computed figure), export:bi G3 (n_a), export:csv-client G2 (n_a), studio:xlsx-orch-extract G2 (no; tracer says DMS computes none and N_A is arguable), insights:post-ask G2/...; shared code: n/a (same pass-through pattern: xlsx_export.py:117-136, routes/insights.py:182-193, core xlsx_orch.py:354-388).)
5. Surfaces that restate a trust claim get 'no' for some and n_a for others. (cells: ui:share-answer G6 (no), export:xlsx G6 (no), export:bi G6 (no), export:csv-client G6 (n_a), ui:source-panel-contribution G6 (n_a; headline claims 'Certified answer', sourcePanel.ts:44), ui:check-acc...; shared code: apps/ui/src/lib/answerDelivery.ts:23-41, packages/core/dms_core/xlsx_export.py:26-36, apps/ui/src/lib/sourcePanel.ts:35-45.)
6. The same 'no Space' request has three different grant sets across routes; STATUS.md says A-0007 closed 'refused under all'; library cells call the union by design while ask cells call alerts access a gap. (cells: library:warehouse-preview G1, mcp:preview G1, library:bronze-preview G1, the 12 chat-ask/mcp:ask no-space G1 cells in gap no-space-ask-widest-grant; shared code: demo_grants.py:61-85 and warehouse_browse.py:42-50 (no space = company_default_tables, no alerts) vs executor/__init__.py:306-317 (no space = all DEMO_TABLES +...)
7. cascade-attach says the ungranted tick vanishes before the bronze lane at :663-664; code shows the bronze lane never reads ticks. That half of its bypass_scenario is mis-attributed (downgraded). (cells: chat-ask:cascade-attach G1 (partial), chat-ask:bronze-grant-abstain G1 (yes); shared code: executor/__init__.py:659-666 (drop narrows only requested -> readable for cascade and generative), :693-723 (bronze lane uses bronze_lane_table(question) and g...)
8. Wording only: the issues are open, the first PRs merged. (cells: chat-ask:cascade-abstain G1 (#297 merged), chat-ask:harness-generative-miss G1 (#297 open), chat-ask:generative-named-abstain G1 (#297 open), mcp:ask G1 (#297 open), chat-ask:cascade-abstain G6 (#305...; shared code: n/a (gh: issues #297 and #305 OPEN; PRs #302 and #319 merged, git f5fcc42 and 156cde8).)
9. Same DDL counts as evidence for DMS-executed reads but not for Cortex-executed ones; chunk routes score 'no' although the tracers say it is definitional. (cells: library:warehouse-preview G4 (yes), chat-ask:demo-mode G4 (yes), chat-ask:governed-pack G4 (partial), chat-ask:generative-multi-grain G4 (partial), library:chunks-search G4 (no, self-described defini...; shared code: demo_warehouse.py:288-440 (declared DDL).)

## Off-matrix findings from the tracers

- Off-matrix findings from tracer other_findings: ensure_demo_warehouse DROPs and reseeds the six demo-named tables on first call per process (chat-ask:demo-mode, library:warehouse-preview other_findings); CSV export does not neutralise formula-leading cells (export:csv-client); possible runaway auto re-ask loop in ui:exclusion-auto-confirm (static reading).
- Repo pre-existing gate harness: tests/gate_matrix/conftest.py registers the gate_gap marker; no tests were written here.

