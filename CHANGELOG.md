# CHANGELOG

Append-only. Never edited, only added to. Newest first.

## 2026-09-26 - ORACLE-FIX-02: cq_audit_overdue judged at the engine date (#308)

- **Ticket.** [ORACLE-FIX-02 #308](https://github.com/Netie-AI/dms/issues/308) under dms#231. CI fixtures, not live. Not COMPLETE.
- **Cause.** On the DMS seeded lake the certified oracle runs (no error). It reads `CURRENT_DATE`, so the judge evaluated it at the harness date: a correct answer at a different engine date judged WRONG.
- **Change.** `dms_executor/engine_clock.py`: a clock-reading answer SQL is bracketed by a `CURRENT_DATE`/`TimeZone` probe on the same submit; the envelope carries `engine_clock`. `score_curated.py` binds the oracle's `CURRENT_DATE` to that date; missing is ORACLE_ERROR, before != after is INVALID (new category, fails the live run). Comparator in `oracle_row_match.py` untouched. No oracle SQL changed.
- **Gate.** `tests/test_oracle_fix_02.py` (two dates, INVALID, missing date, HTTP envelope). `tests/test_oracle_fix_01.py`: exemption removed, zero-row check covers every answer oracle (tightens only).

## 2026-09-25 - ONTO-STORE-01: durable versioned ontology store (#279)

- **Ticket.** [ONTO-STORE-01 #279](https://github.com/Netie-AI/dms/issues/279) under CONNECT-ASK-01 #277 / EPIC-020 #178. Does not close tickets. Not COMPLETE. No live figures.
- **Change.** Alembic `0004_ontology_store` after `0001`-`0003`: `ontology_version` (space, source identity kind+host+database+schema, schema fingerprint, status proposed|active|superseded, created_by, created_at); `onto_object_type`; `onto_property` (PK flag); `onto_link` (from/to types, FK columns, declared one-to-one|one-to-many, measured cardinality from `verify()`); `onto_measure` (column, aggregate, grain); every element state proposed|confirmed|rejected; `onto_object`; `onto_link_instance`; `onto_audit` (who, action type, inputs, result, when). Thin repository in `dms_core.control_plane.onto_store`: load active, load by version, list versions. The only write is reconnect: same identity+fingerprint returns the same version id; a changed fingerprint inserts a new proposed version and one audit row; the old active row is not updated. First snapshot bootstraps `active`. Confirm/reject is the next ticket.
- **Guards.** Extract-only (DR-0005). No credentials, connection strings, or passwords in any column. No Cortex/LLM/network. Fingerprint is a sha256 of sorted tables/columns/types/PKs/FKs (stable under column-order changes).
- **Gate.** `tests/test_onto_store_01.py` (no skip / xfail / importorskip). Floor 1 calls 0004 `upgrade()`/`downgrade()` on a recording Alembic `op` because GitHub CI sets `DMS_SKIP_CONTROL_PLANE_TESTS=1`; live `alembic upgrade head` is `tests/control_plane/conftest.py` when Postgres is up.
- **Not this ticket:** action engine confirm/reject, ask-path wiring, COMPLETE, merge.

## 2026-09-25 - ORACLE-FIX-01: curated oracles use demo seed txn_type (#301)

- **Ticket.** [ORACLE-FIX-01 #301](https://github.com/Netie-AI/dms/issues/301) under dms#231 / EPIC-020 / dms#178. Does not stamp COMPLETE. Does not merge. CI fixtures, not live.
- **Change.** Nine curated oracles in `tests/fixtures/curated_ceo/oracles.yaml` filter `txn_type = 'outbound'` (the value `demo_warehouse.py` seeds and `_REVENUE_SQL` uses). Not `'OUT'` (Cortex warehouse). Value from seed source, never from an answer. Phase 1 figures are not re-labelled; 1b and later runs record this merge commit as the oracle file commit.
- **Gate.** `tests/test_oracle_fix_01.py::test_curated_oracles_match_demo_seed_txn_type`. Seeded tmp lake. Fails on parent `f5fcc42e` (R-0007): nine `'OUT'` literals and nine zero-row oracles. `cq_audit_overdue` keeps its own ORACLE_ERROR category. No skip/xfail. No existing-test edit. Scorer, questions, pins, contract, invariants, import-linter, `.github/` untouched.
- **Not this ticket:** live prove; COMPLETE; merge; Phase 1 re-label.

## 2026-09-25 - GRANT-READ-01: generate context and cascade ignore unticked uploads (#297)

- **Ticket.** [GRANT-READ-01 #297](https://github.com/Netie-AI/dms/issues/297) under EPIC-020 / dms#178. Does not stamp COMPLETE. Does not merge. CI fixtures, not live. No Cortex ranking claim.
- **Claim.** uploads not selected by the caller never enter the generate context.
- **Change.** Ask path only: with no `tables`, cascade and retrieve use `requested or default_readable` (the same set `demo_acl` mints: grantable intersect DEMO_TABLES). With `tables`, intersection with `granted` only. If `grantable_tables` cannot be read, the ask uses an empty context and never falls back to the whole space. `DemoSessionStore.list_space_source_ids` reads the Executor warehouse, not the process default. `demo_acl`, the manifest, bearer/transport, qualifier guard, scorer, invariants, import-linter, contract pin, WRONG pins, `.github/`, `settings.py`, and `cortex_read.py` untouched.
- **Gate.** `tests/test_grant_read_01.py`. Fake httpx transport. Fails on parent `38f8924a` (R-0007). No skip/xfail. No existing-test edit.
- **Not this ticket:** live prove; COMPLETE; merge.


## 2026-09-25 - A2-06: caller ontology stays unread; missing verify cache abstains (#262)

- **Ticket.** [A2-06 #262](https://github.com/Netie-AI/dms/issues/262). Same PR. Does not merge. Does not stamp COMPLETE. CI fixtures, not live.
- **Verify.** `_compile_maybe_unverified` compiles on a private shallow copy (`verified=True` on the copy only). A scoped ask leaves the caller's `verified` and every link cardinality unchanged. `Ontology.verify` writes `_violations` on `self.__dict__`. If that slot is missing or None after a failed verify, plan and SQL both ABSTAIN `ontology_unverified`.
- **Gating.** `tests/test_hostile_schema_a2.py` differs from `3f0353a6` only by `_A2_06_OK` (three named ABSTAIN->OK pins). The six defect-touching pins stay byte-identical ABSTAIN.
- **Not this ticket:** live prove; COMPLETE; merge.

## 2026-09-25 - A2-06: scope ontology refusals to the failed subject (#262)

- **Ticket.** [A2-06 #262](https://github.com/Netie-AI/dms/issues/262) under EPIC-A2 #256. Does not stamp the epic COMPLETE. Does not invent a live coverage or WRONG=0 figure. Any n is a CI fixture.
- **Change.** `load_verified_ontology` keeps a caller-declared ontology when `verify` fails. Failed-link cardinality is never re-blessed. A hop through a failed `fk_intact` link is a use of that subject (plan path or SQL that reads both relations). A failed object key cites when it is the grain, a hop, a group/filter/via destination, or when SQL reads its relation. Compile of a clean plan uses a shallow copy so the caller's `verified` flag is not toggled. Lakes with no declared ontology (demo/live, BIRD) still drop the default demo ontology on failure.
- **Gate.** Flip list re-derived after the Epic hop ruling: only pins whose envelope rows equal the hostile-schema oracle as a **multiset** move ABSTAIN -> OK. A badge, OK status, or matching row count alone cannot pass. New fixtures (orphan-with-lines; duplicated dimension key) abstain when the path/SQL touches the failed subject. Defect-touching ABSTAIN pins stay. New: `tests/test_a2_06_scoped_ontology.py`. No skip/xfail. No invariants / import-linter / contract / WRONG / scorer / BEARER / QUAL-GUARD edits. Any n/M is CI fixtures, not live.
- **Not this ticket:** live prove; FX; COMPLETE; merge.

## 2026-09-25 - QUAL-GUARD-01: named abstain when a qualifier is dropped (#290)

- **Ticket.** [QUAL-GUARD-01 #290](https://github.com/Netie-AI/dms/issues/290) under EPIC-INSIGHTS-UX #178. Does not stamp the epic COMPLETE. Does not close #231. Does not edit `scripts/score_curated.py` (dms#292).
- **Change.** Deterministic qualifier extractor (time grain day/week/month/quarter/year including daily..yearly; time filter last/this N / named month/year; group-by `by X`/`per X`; named filter values). Coverage check on every non-abstain generate_sql / ranked retry / ontology_plan / bind_plan path before the envelope is stamped. Uncovered qualifier is named ABSTAIN `unhonored_qualifier:<kind>=<value>`. Ranked retry adds the grain/dimension or skips. Generate legs recorded. Route A `fallback:generate_empty`, route B `fallback:validate:<why>` (reject reason kept). Grain table in retrieve gains month/week/quarter/year. PII-01 masking untouched. No invented ontology grains.
- **Gate.** `tests/test_qual_guard_01.py` seeded fixtures only. Pack questions that newly abstain are listed on the PR. Pack figure measured under the old scorer (pre SCORE-ROWS-01), labelled CI fixtures / scorer OK, rows not compared. Not a live 52-row claim.
- **Not this ticket:** dms#292 scorer; live prove; Cortex; LLM.

## 2026-09-25 - SCORE-ROWS-01: curated judge compares answer rows with oracle SQL (#292)

- **Ticket.** [SCORE-ROWS-01 #292](https://github.com/Netie-AI/dms/issues/292) under EPIC-020 #178. Does not close tickets. Not COMPLETE. Does not invent a live WRONG=0 or coverage number. Bar PASS is only Platform's live row-compared `--prove-path`.
- **Change.** In live, `--prove-path`, `--ab` and `--climb`, `judge()` runs each `expect: l0` oracle SQL from `tests/fixtures/curated_ceo/oracles.yaml` read-only against `--oracle-db` (required on live host modes; schema_version recorded). Answer rows vs oracle rows as a multiset: column-order-insensitive value tuples, numbers rounded to the oracle ROUND scale, ORDER BY honoured only with LIMIT (top-N), NULL equals NULL. Mismatch is WRONG (`rows_mismatch:count=<a>/<b>` or `rows_mismatch:values`). Failed oracle SQL is `ORACLE_ERROR`, never OK and never skipped. Refuse/abstain/trap keep the badge rule (confident is WRONG). Report categories answered / abstained / WRONG / LAYER / ORACLE_ERROR / excluded-pending-scan are each out of 52. For one release the old badge judge is printed beside the new one as `scorer_ok_rows_not_compared`. Helper: `scripts/oracle_row_match.py` (same multiset idea as BIRD `grade_envelope`, not that harness).
- **Gate.** `tests/test_score_rows_01.py` builds its own DuckDB fixture (no skip / xfail / importorskip). 3-row ungrouped vs monthly is WRONG; exact match OK; top-5 one SKU off WRONG; float past ROUND OK; missing column ORACLE_ERROR never OK; confident trap WRONG. Nine refuse/trap shapes each have a test. `--self-check` still passes on the 52-question pack. Existing scorer assertions were not loosened. No oracle SQL, WRONG pin, contract pin, invariants, import-linter, or `.github/` edits.
- **Figures.** Any number in this PR is a CI fixture out of 52, labelled `CI fixtures, not live`. Earlier KEEP_HOLD climb/WRONG figures stay KEEP_HOLD until re-run under this scorer on prove.
- **Not this ticket:** runtime `packages/` / `apps/`, live prove, changing oracles, COMPLETE, merge.

## 2026-09-25 - BEARER-01: generate=true bearer + transport guards (#289)

- **Ticket.** [BEARER-01 #289](https://github.com/Netie-AI/dms/issues/289) under EPIC-INSIGHTS-UX #178. Does not close tickets. Not COMPLETE. Does not clear the pilot security bar (KEY-01 #273 owns the demo default).
- **Generate only.** `POST /v1/insights` with `generate=true` (hosted `insights_post` and ask-lane `compute_insights`) sends the configured Cortex key as `Authorization: Bearer`. Empty or demo-viewer keys make no generate call. Plain `http` to a non-loopback host makes no generate call. Named ABSTAIN: `insights_bearer_missing`, `insights_bearer_insecure_transport`. Never WRONG. Ranked / `generate=false` / `cortex_read` / ontology-trust routes unchanged. `settings.py` and `cortex_read.py` untouched.
- **Must not:** mint or rotate a key; log or echo the token; edit existing tests; touch `.github/`, invariants, import-linter, `score_curated.py`, or `semantic_retrieve.py`.

## 2026-09-25 - PII-01: mask personal columns before Cortex context and in exports (#272)

- **Ticket.** [PII-01 #272](https://github.com/Netie-AI/dms/issues/272) under parked EPIC-BANK-01 #267. Does not stamp the epic COMPLETE. Does not close the bank bar.
- **Change.** Local deterministic detector in `dms_core/pii.py` (no network, no model). Column-name plus value-pattern checks for person names, Malaysian IC numbers, phones, emails and account numbers. Flagged sample values are dropped from retrieve `encodings` / `bound_values` before compute. Answer text, rows, xlsx and BI exports replace raw values with stable `DMSMASK_<kind>_<nn>` tokens. Aggregates keep their numbers. Detector errors fail closed (do not send the values). Not a sixth port: swap is Cortex #268 HTTP PII-MASK or a DLP call behind the same functions.
- **Placeholders.** Tokens are not email/phone/IC/account-shaped, so a plain regex second masker leaves them unchanged. Cortex #268 is not merged; the live both-maskers run is an honest leftover and is not claimed.
- **Gate.** `tests/test_pii_01.py` seeds a fake table, captures compute context, Insights POST `body["ontology"]` (GEN-RESTORE path: `retrieve_short_context` -> `compute_insights` ontology), plus export bytes, and asserts none of the seeded raw values appear. Demo `locations.name` / `supplier_name` stay unmasked so existing packs do not gain WRONG.
- **Not this ticket:** Cortex #268, login, TLS, auditor export, `scripts/score_curated.py`, live both-maskers prove.

## 2026-09-25 - GEN-RESTORE-01: Cortex setup fields copy-through (#276)

- **Setup fields.** When Insights returns `served_provider`, `served_model`, `served_local`, `learn_enabled`, `learn_source`, `route_store_id`, copy them onto the DMS answer envelope exactly as received. Null stays null. Missing stays absent. Never infer. Prove without Cortex#269 is expected to omit them. Harness fingerprint compare is dms#264 (parked). Not COMPLETE.

## 2026-09-25 - GEN-RESTORE-01: Insights compute seam, fail closed, WRONG=0 (#276)

- **Ticket.** Serves [GEN-RESTORE-01 #276](https://github.com/Netie-AI/dms/issues/276) under EPIC-INSIGHTS-UX #178. Does not close tickets. Not COMPLETE. Does not invent a live #231 score or bar PASS.
- **Seam.** Generative `live_ask` calls Cortex Insights only: `POST /v1/insights` generate=true, `GET /v1/insights/ontology` when ranking is omitted, one ranked-slot generate retry. Ask lanes never POST `/dms/query` (F-0055). `compute_query` stays for CONTRACT-FAKE-01 (`dms_query=False` or `compute_insights`). Timeout is `INSIGHTS_ASK_TIMEOUT_SECONDS = 8.0` (not the 120s contract timeout, not the old 45s /dms/query stall).
- **Fail closed.** Named ABSTAIN: `insights_unarmed`, `insights_refused`, `insights_unauthorized`, `insights_timeout`, `insights_no_sql_no_ranking`. None bind. `bind_on_miss=False` on every lane. `ask_path` 400 unless `DMS_HARNESS_ASK_PATHS`. Pre-gates stay. Validate then Cortex submit, or ABSTAIN.
- **plan_origin.** Answered envelopes stamp `plan_origin=generate_sql` or `plan_origin=ontology_ranking` next to `plan_source`.
- **Chart.** `_l2_envelope` reuses `chart_from_rows` (the Cortex contract fallback). No chart when rows do not fit. No new builder.
- **Keys.** No provider keys in the body. Model access stays in Cortex via OpenVault.
- **Not this ticket:** live Studio re-prove (#231, Platform), CONNECT-ASK-01 (#277), deleting `/dms/query`, moving pre-gates (GEN-07), `semantic_retrieve.py` (PII-01 #272).

## 2026-09-25 - A1-02: record Cortex ROUTER-1 served_* fields (#264)

- **Ticket.** Follow-up on [A1-02 #264](https://github.com/Netie-AI/dms/issues/264). Cortex ROUTER-1 (#269) returns `served_provider`, `served_model`, `served_local` on Insights answers; GEN-RESTORE-01 copies them onto the DMS envelope with `learn_enabled`, `learn_source`, `route_store_id`.
- **Change.** The Mini-Dev harness reads those exact names from the DMS answer envelope. Absent is `unknown`, never guessed. Per-answer fields, run-level mix, and `setup_fingerprint` all include them. Also records `plan_origin` (`generate_sql` or `ontology_ranking`) and counts them separately. Fixtures `served_present.json` / `served_absent.json` / `served_nested.json`.
- **Must not:** guess from aliases; live Mini-Dev score; merge.

## 2026-09-25 - A1-02: BIRD Mini-Dev harness (EPIC-A1 #257, #264)

- **Ticket.** [A1-02 #264](https://github.com/Netie-AI/dms/issues/264). Rebuild on main after EPIC-A2 (#230), A2-05 (#261), and GEN-RESTORE-01 (#276). Held local `a1-02` @ `6396dec` was never pushed. Does not stamp EPIC-A1 COMPLETE. Does not touch `scripts/score_curated.py`.
- **Harness.** `python scripts/score_bird.py --minidev <mini_dev_postgresql.json> --live` POSTs each question to `/v1/chat/ask`, executes gold SQL read-only, and grades envelope rows as a multiset (column-order-insensitive). Prints n, answered, RIGHT/ABSTAIN/WRONG, EX on answered, abstain rate, rule-of-three bound, per-db and per-difficulty breakdown, run SHA / time / data size. GOLD_ERROR is counted and excluded from n. `--limit` is smoke and is printed. `--self-check` plants on a synthetic Mini-Dev-shaped fixture (DuckDB via `dms_executor.minidev_gold`).
- **Numeric.** Not 4 dp absolute. Integers exact (29+ digit strings must not crash `norm_cell`). Other numbers: `|a-b| <= max(1e-9, 1e-6 * max(|a|,|b|))`.
- **Until Cortex ROUTER-1.** Live Cortex scoring requires `CORTEX_FREEROUTE_LEARN=0` and a fresh `CORTEX_ROUTE_STORE`, with before/after hash or row count in the artifact. Missing those is CONFIG, not a silent score. `--offline` / `--self-check` skip freeze.
- **Setup fields.** Each case copies six envelope fields from the DMS answer (`served_provider`, `served_model`, `served_local`, `learn_enabled`, `learn_source`, `route_store_id`) or records `unknown` (never guessed). Also records `plan_origin` (`generate_sql` or `ontology_ranking`) and counts them separately. `setup_fingerprint` covers learn flag, store state, and those setup fields. `--compare` refuses different fingerprints unless `--force-cross-setup` (labeled cross-setup).
- **Must not:** commit BIRD data; set a target; shrink the 500; quote a live Mini-Dev score from this tree (Platform baseline later); keys in code.

## 2026-09-25 - A2-05: currency asked vs currency in the data (EPIC-A2 #256, #261)

- **Ticket.** [A2-05 #261](https://github.com/Netie-AI/dms/issues/261). Founder chose sqlglot in DMS (hard rule 6 swap scenario). Replaces the regex/allowlist that failed four adversarial rounds. No FX conversion. Does not stamp #256 COMPLETE. Does not touch #262.
- **Change.** Pin `sqlglot>=27,<28` on `dms-executor`. Parser isolated in `dms_executor/sql_currency.py`. When a question names a currency, both typed-plan and generated-SQL paths parse DuckDB SQL, resolve select/aggregate outputs through CTEs, derived tables, subqueries, aliases and joins, and answer only if the measure unit is known and matches (column suffix or same-relation currency column). Else ABSTAIN naming the mismatch. Parse/lineage failure fails closed. Column-name vs currency-column conflict is unverified.
- **Gate.** `*/revenue_usd/*` pins move WRONG -> ABSTAIN. A2 gate confident WRONG 7 -> **0**. Questions naming no currency are unchanged. Round 1-3 bypasses are regression tests in `tests/test_sql_currency_a2_05.py`.
- **Not this ticket:** FX rates; #262 scope-refusal; `scripts/score_curated.py`.

## 2026-09-23 - A1-01: scorers print n and the rule-of-three bound beside WRONG=0 (EPIC-A1 #257, #263)

- **Ticket.** First ticket of [EPIC-A1 #257](https://github.com/Netie-AI/dms/issues/257). Reporting only; no verdict logic touched. Built in its own worktree and accepted by an independent adversarial verifier on the first round.
- **Change.** `scripts/score_bound.py` (shared helper: `bound_pct = 300/answered` or None, a checker that rejects any `WRONG=0` / `0 confidently wrong` line without `answered=` and a bound on it or the next line). `score_bird.py` and `score_answers.py` print `answered=<n> bound about X pct (rule of three, 95 pct)`, or `bound n/a (nothing answered)`, and stamp `answered` / `bound_pct` (null when unanswered) into their JSON artifacts. Both `--self-check` / `--oracle-only` CI steps now fail if the line regresses to a bare zero.
- **Why.** NETIE.md rule 7: below n=300 a printed 0 invites reading 0 percent. `ontology_bench.py` already did this; the other two did not.
- **Not this ticket:** `scripts/score_curated.py` (CLIMB-13 PR #240 owns it); any coverage target; any BIRD number as a claim.

## 2026-09-23 - A2-02/03/04: schema-defect refusals (EPIC-A2 #256)

- **Tickets.** #258 A2-02, #259 A2-03, #260 A2-04. Each built in its own worktree and accepted by an independent adversarial verifier (#259 after one repair round). #261 A2-05 (currency) not in this entry: its verifier rejected round 2.
- **#258.** A caller-declared ontology that fails `verify` now refuses generated SQL that reads a failed link's relations, and both paths name the violation (check, link, orphan count) instead of a bare `ontology_unverified`. Lakes with no declared ontology (live product, BIRD) are unchanged; `score_bird --self-check` passes before and after.
- **#259.** `verify` refuses a link declared on the child's own key (`fk_is_child_key`) unless `one_to_one=True`, and names a sibling column that matches the parent key.
- **#260.** `add_object(..., business_key=[...])` is verified unique and non-null (`business_key_unique`). Declared only, no profiling.
- **Measured.** A2 gate n=40: confident WRONG 14 -> **7** (all 7 are currency, #261). Coverage costs pinned, not hidden: when one claim fails the whole declared ontology drops, so unaffected questions on that lake abstain (named).
- **Open, founder call.** `from_manifest` still treats a DB FK declared on the child's own PK as one-to-one (AdventureWorks shared-PK subtypes rely on it). Nothing yet produces `business_keys` from source UNIQUE constraints.

## 2026-09-23 - A2-01: hostile-schema envelope gate (EPIC-A2 #256)

- **Ticket.** First ticket of [EPIC-A2 #256](https://github.com/Netie-AI/dms/issues/256) (PRD-001 amendment accepted by founder 2026-09-23). Measurement only. No product code changed. Does not stamp #256 COMPLETE.
- **Gate.** `tests/test_hostile_schema_a2.py` plants four defects one at a time (orphan FK, FK on the wrong column, duplicate business key behind a surrogate, `revenue_usd` holding MYR) into a clean 3-table warehouse, asks 4 questions on the typed-plan and generated-SQL paths through `maybe_generative_ask` with a real duckdb submit, and grades envelope rows against oracle values, not row counts.
- **Measured.** n=40 envelopes, **14 confidently WRONG** on main @ `3c3b621`. Pinned in `MEASURED` so a fix or a regression both fail until the pin is edited. Owners: #258 (SQL path ignores failed verify), #259 (wrong-column FK), #260 (duplicate business key), #261 (currency never checked; 9 of 14).
- **Proves it can fail.** Flipping one pin to OK fails with the envelope's rows and text. A clean-schema control must answer with oracle values, so abstaining cannot pass for free.
## 2026-09-23 - GEN-PATH-CLIMB-13: unused parent-SQL leftover past ontology_plan=39 (#231)

- **Ticket.** Serves [GEN-PATH-CLIMB-13 #231](https://github.com/Netie-AI/dms/issues/231) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. Does not reopen #178. #228 RISE_PASS @ `dff2a6ea` (ontology_plan=39 bind_plan=0 WRONG=0 answered=39/49 on uncapped harness) stands as the measured floor.
- **Climb.** Score leftover Cortex metrics.yaml parent-SQL of `certified_queries.yaml` L0s live already answers (`how many SKUs per category`, `what is our inventory worth per category`, Ops `freight spend per destination`) as `ontology_plan`. Same SQL as `cq_sku_count_by_category` / `cq_stock_value_by_category` / `cq_cost_by_destination`. Not PACK_METRICS expansion (prove-path is generative). Retrieve locks `per category` / `per destination` group plus `inventory worth` onto `stock_value_myr`; overlay spine slots `sku_count_per_category` / `inventory_worth_by_category` / `freight_spend_per_destination`. Harness unions the new L0s so this SHA cannot score the 39/49 pack. Live prove FAILs on ontology_plan<=39 or climb-13 rise L0s not `ontology_plan`. Frozen n=26 stays a floor, not a cap. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 39 + WRONG=0 after deploy. CI green != climb PASS. QUALIFIED claim stays n=26. curated_ceo n=52.
- **Not this ticket:** climb PASS, #178 reopen, estate CLEAR, ticket close, greening planted refuses, pack shrink to fake a higher percent, reseating #237.

## 2026-09-23 - INSIGHTS-EXPORT-02: Power BI + Superset stubs from ask envelope (#189)

- **Ticket.** Serves [INSIGHTS-EXPORT-02 #189](https://github.com/Netie-AI/dms/issues/189) under EPIC-INSIGHTS-UX #178 Phase B. Does not stamp epic COMPLETE. Does not invent 99.95%. Does not reopen #108. Does not invent bar (4) PASS. Does not reseat #231/#237.
- **Export.** `POST /v1/chat/export.bi` copies an existing ask envelope into a Power Query `#table` and a Superset dataset JSON. Same gate as Excel (`answer_id` + badge). Does not re-ask Cortex, does not invent DAX/Superset metrics, does not emit a SQLAlchemy URI or DuckLake folder connector.
- **Honesty.** `complete` is always false. `live_connector` is always false. `needs_you` names Desktop / live ODBC / steward-hosted Superset. Prefer the named stub over a fake connector.
- **UI.** Power BI / Superset on the answer posts that envelope and shows the NEEDS-YOU panel. Filename `dms_answer_<answer_id>.pq` / `.superset.json` (no clock).
- **Not this ticket:** live Power BI Desktop walk, Superset-as-chrome, #178 COMPLETE, FRTR Copilot (#29).

## 2026-09-23 - ONTOLOGY-MULTIGRAIN-02: mg_sku_plant named missing_join/plant or granted path (#254)

- **Ticket.** Serves [ONTOLOGY-MULTIGRAIN-02 #254](https://github.com/Netie-AI/dms/issues/254) under EPIC-INSIGHTS-UX #178. Follow-up to #249 KEEP_HOLD (3/4): live `mg_sku_plant` died as bare `validate:ungranted:shipments`. Rebased onto GEN-03 #194 @ `9b29c565`. Does not stamp epic COMPLETE. Does not invent bar (1) PASS / 1PB LIVE.
- **Ask path.** Multi-grain compile is grant-aware. A SKU+plant ask either emits a join path whose tables the Space may read (Ops `shipments`+`locations`, or Finance `stock_value` via inventory+locations) or ABSTAINS `missing_join` naming `plant` -- never `validate:ungranted:shipments`. where_paths / WRONG=0 / refuse discipline from #238 stand. `bind_plan` stays non-confident. GEN-03 `ask_path` 400 + `bind_on_miss=False` on live_ask are unchanged.
- **Tests.** `tests/test_ontology_compile.py` mg_sku_plant: Finance shipping-cost named ABSTAIN; Ops shipping-cost granted path; Finance stock-value granted plant path. CI green != Platform live re-prove.
- **Not this ticket:** #178 COMPLETE, bar (1) PASS, 1PB LIVE, second vault / LIVE_KEY, reseating #194/#189/#231/#237/#238.

## 2026-09-23 - GEN-03: contain the ask path - no keyword-bound answer under a confident badge (#194)

- **Ticket.** Serves [GEN-03 #194](https://github.com/Netie-AI/dms/issues/194) under EPIC-INSIGHTS-UX #178. Does not close tickets. Not COMPLETE. No new climb number.
- **Refuse.** `POST /v1/chat/ask` with `ask_path` `exact` or `generative` returns 400 `ask_path_not_allowed` unless the server sets `DMS_HARNESS_ASK_PATHS` (default off). Checked first: no compliance gate, Cortex call, submit or ledger append on a refused request. Server config only, never a header (DR-0004).
- **Contain.** `live_ask` no longer POSTs Cortex `/dms/query` on any lane and passes `bind_on_miss=False` everywhere. Cortex ignores `mode`/`ontology` and returns no typed plan (KB F-0055), so the call always missed and the generative lane answered from `bind_plan` under L2_VALIDATED with wrong numbers. The paraphrase and vague-ask pre-gates still run where they were (moving them is GEN-07). `bind_plan` is kept for offline harnesses. Tip `f9cad233` multi-grain compile and GEN-PATH-REFUSE-01 named ABSTAIN stay `ontology_plan`, not bind_plan. Bar (2) KEEP_HOLD: predict / revenue-2099 is not L2 all-time pad; not-cold invert is not a confident badge (E11 sees quoted `"is_cold_storage"`). WRONG=0 not weakened.
- **Honest docstrings.** `cortex_client` compute no longer claims to be the engine's generate+validate path; deletion is CONTRACT-FAKE-01.
- **Not this ticket:** Cortex changes, the seam decision, GEN-04/05a/07, `scripts/score_curated.py` live `--ab` (400s on a customer origin until run against a measurement origin), ticket close.

## 2026-09-23 - GEN-PATH-REFUSE-01: named ABSTAIN when ontology path / metric missing (#238)

- **Ticket.** Serves [GEN-PATH-REFUSE-01 #238](https://github.com/Netie-AI/dms/issues/238) under EPIC-INSIGHTS-UX #178. Does not close #178. Does not stamp COMPLETE. Does not reseat GEN-03 `#194` ask_path 400 containment.
- **Refuse.** When Cortex Insights ranks an intended metric that does not resolve onto the verified DMS ontology, `maybe_generative_ask` returns ABSTAIN and names the gap in the customer text (`gap: unknown_measure: no measure named '...'`). Compile `unknown_measure` / `no_path` / `ontology_unverified` / `coverage_invalid` use the same named-gap sentence. Isolated gen does not `bind_plan` a nearby measure. Product lane does not return None into Cortex.ask on that miss.
- **Preserved.** Transport miss on a known measure still binds when `bind_on_miss=True` (GEN-02). Overlay recovery of a ranked id that compiles is not a gap. Planted refuses stay ABSTAIN. WRONG=0. OV/FreeRoute + Cortex only. No LIVE_KEY / `:5000` invent. E13 `audit_receipt` (#235/#252), multi-grain compile (#249/#234), SC grains (#232) stay on tip.
- **Tests.** `tests/test_gen_path_refuse.py`. Live uncapped prove remains Platform (`scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`). CI green is not a climb stamp.

## 2026-09-23 - ONTOLOGY-AUDIT-01-FLOOR: INVARIANT-CHANGE trailer for #235 squash (#252)

- **Ticket.** Serves [ONTOLOGY-AUDIT-01-FLOOR #252](https://github.com/Netie-AI/dms/issues/252) under EPIC-INSIGHTS-UX #178. Cures Verify R-0003 NO RELEASED on squash `dc752563` (push CI protected-paths FAIL: `tests/invariants/test_envelope.py` without `INVARIANT-CHANGE:` in the squash body).
- **Declare.** E13 `audit_receipt` include/exclude/unsure is declared on a commit that carries the trailer. Omit the receipt or stamp COMPLETE fails `assert_envelope_valid`. Does not weaken WRONG=0. Does not invent COMPLETE.
- **Ticket merge.** Squash-merge commit **body** MUST include `INVARIANT-CHANGE:` or push CI fails again. Do not re-YES the broken `dc752563`.
- **Not this ticket:** #178 COMPLETE, bar (1) PASS, #238/#194/#189 product work.

## 2026-09-23 - ONTOLOGY-AUDIT-01: include/exclude/unsure receipt on ask envelope (#235)

- **Ticket.** Serves [ONTOLOGY-AUDIT-01 #235](https://github.com/Netie-AI/dms/issues/235) under EPIC-INSIGHTS-UX #178. Does not close the epic. Does not stamp COMPLETE. Does not invent 99.95% / estate CLEAR.
- **Receipt.** `build_answer_envelope` always stamps `audit_receipt` with include rows, exclude reasons, and unsure/ABSTAIN (or explicit N/A with why). Include set is executed result rows only. Exclude is SQL WHERE/HAVING/FILTER or caller-supplied reasons; COMPLETE payloads are rejected and SQL is used instead. Unsure True from the engine demotes to ABSTAIN.
- **Pad.** A stated figure that is not an include cell, same-row gap, or full-column sum demotes. Missing cells are not treated as zero. The constructor does not grow include rows to make a total look complete.
- **Not this ticket:** #178 COMPLETE, FreeRoute client changes, GEN-03 ask_path containment, steward inspect of live Studio asks (Platform leftover).

## 2026-09-23 - ONTOLOGY-MULTIGRAIN-01: try_compile multi-grain before one-grain GEN-01 plan (#249)

- **Ticket.** Serves [ONTOLOGY-MULTIGRAIN-01 #249](https://github.com/Netie-AI/dms/issues/249) under EPIC-INSIGHTS-UX #178 (follow-up to #234 KEEP_HOLD). Rebased onto SC-ONTOLOGY-01 #232 @ `936d810`. Does not stamp epic COMPLETE. Does not invent bar (1) PASS / 1PB LIVE. #250 was a duplicate seat and is not this PR.
- **Ask path.** `maybe_generative_ask` runs `try_compile_multi_grain` after compute-unsure and **before** one-grain GEN-01 plan or SQL. Live ranking filling a sku-only `ontology_plan` no longer drops plant/day/lane/supplier. Envelope exposes `where_paths` and stamps `ontology_compile:where+importance` when that path wins. Honest ABSTAIN names `missing_join` / `missing_metric`. `bind_plan` stays non-confident. Coverage from #232 still stamps include/exclude/unsure on L2.
- **Tests.** `tests/test_ontology_compile.py` KEEP_HOLD cases: sku+plant, sku+day missing_join ABSTAIN, supplier+sku, sku+lane -- each with a one-grain ranked plan payload. CI green != Platform live prove.
- **Not this ticket:** #178 COMPLETE, bar (1) PASS, 1PB LIVE, second vault / LIVE_KEY, DB-GPT clone.

## 2026-09-23 - SC-ONTOLOGY-01: supply-chain grains SKU-supplier-plant-lane-day (#232)

- **Ticket.** Serves [SC-ONTOLOGY-01 #232](https://github.com/Netie-AI/dms/issues/232) under EPIC-INSIGHTS-UX #178. Does not close the epic. Does not stamp COMPLETE. Does not invent 1PB LIVE / fake supply-chain metrics.
- **Grains.** Named `sku` (alias of `product`), `supplier`, `plant` (alias of `location`), `day` (CAST `transactions.ts` when present), `lane` (origin->destination on shipments when origin column exists). Thin demo has dest-only shipments: lane is `missing_join` naming `origin_location_id`, not a padded route. Join `importance` 1/2/3 from a measure grain; grouping through M2M is filter-only.
- **Coverage.** Every compiled number carries include/exclude/unsure. Exclude always names `missing groups not zero-padded`. Ask path stamps `coverage` on ontology_plan envelopes. Missing metric/join ABSTAINS with the reason in assumptions/text. FreeRoute stays Cortex/OV `free+normal`. No LIVE_KEY / second vault. No pack shrink. GEN-03 containment untouched.
- **Regression.** `tests/test_sc_ontology.py`. CI green != Platform steward walk. #178 stays OPEN.

## 2026-09-23 - ONTOLOGY-COMPILE-01: ranked where-paths + importance for multi-join grains (#234)

- **Ticket.** Serves [ONTOLOGY-COMPILE-01 #234](https://github.com/Netie-AI/dms/issues/234) under EPIC-INSIGHTS-UX #178. Does not stamp epic COMPLETE. Does not invent 1PB LIVE. Parallel with #232 grains / #235 audit / #238 refuse / #194 GEN-03 -- this seat is compile, not ask_path containment or FreeRoute.
- **Compile.** `Ontology.compile_grains` locates sku/supplier/plant/lane/day, ranks where-paths by importance (shortest verified many-to-one first), then emits SQL. Missing object/join -> `missing_join` naming the grain. Missing measure -> `missing_metric`. Equal-importance paths still `ambiguous_path` (no silent pick). `bind_plan` is not the confident path: a >=2-grain miss compiles as `ontology_plan` or ABSTAIN. Plant aliases to `location` until #232 lands a plant object. Day with no calendar object abstains honestly.
- **Tests.** `tests/test_ontology_compile.py` -- two-grain conserve + ranked paths, missing join/metric, equal-importance refuse, ask-path `plan_source=ontology_plan` not bind_plan. CI green != Platform live prove.
- **Not this ticket:** #178 COMPLETE, 1PB LIVE, second vault / LIVE_KEY, DB-GPT clone, #232 grain tables, #194 ask_path 400.

## 2026-09-23 - SCALE-FREE-AI-01: FreeRoute multi-provider consume via OpenVault API (#233)

- **Ticket.** Serves [SCALE-FREE-AI-01 #233](https://github.com/Netie-AI/dms/issues/233) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. Platform/Free Keys owns mint; LIVE_KEY_ID is not rotated here.
- **Consume.** Prove/ask stay on FreeRoute `free+normal`. DMS resolves which free providers that preference would attempt vs skip from OpenVault HTTP (`/api/freeroute/status`, `/api/freeroute/onboard`, `/api/tool/register`, `/api/keys`). No `POST /v1/chat/completions` for discovery. No local vault Path scrape. No second vault. Duplicate labels skipped (`dup_label`). Paid primary hops skipped. WRONG=0 unchanged -- this does not add generate retries to burn more keys.
- **Harness.** `GET /v1/freeroute/providers` plus `python scripts/bakeoff_freeroute.py --self-check`. Docs: `docs/FREEROUTE_PROVIDERS.md`. Live catalog leftover is Platform (`OPENVAULT_URL` already on the host). CI green != climb PASS.
- **Not this ticket:** #178 COMPLETE, LIVE_KEY rotate, mass fake accounts, ticket close, climb/ontology/export edits.

## 2026-09-23 - AGI-BUYER-WALK-01: Studio buyer walk (ask + refuse) (#236)

- **Ticket.** Serves [AGI-BUYER-WALK-01 #236](https://github.com/Netie-AI/dms/issues/236) under EPIC-INSIGHTS-UX #178. Does not close the epic. Does not stamp COMPLETE. Does not invent logos / ARR / dashboard screenshots.
- **Walk.** `python scripts/walk_buyer_studio.py` on Studio. Five supply-chain asks must resolve as `plan_source=ontology_plan` or honest ABSTAIN (`ask_path=generative`). One planted refuse (`Just give me last month's number`) must ABSTAIN and print why. Artifacts cited: library/Studio receipt, ask envelope, Excel export from that envelope. Exact-match L0 does not count. Silent `127.0.0.1:8090` default is CONFIG, not PASS. CI green != live walk PASS.
- **Studio copy.** Operate Studio lists the five asks and the refuse demo. Clicks prefill Chat (`draftQuestion`). Download Excel copies envelope rows. No invented charts.
- **Not this ticket:** #178 COMPLETE, ontology compile, FreeRoute, fake buyer logos, ARR.

## 2026-09-21 - GEN-PATH-CLIMB-12: unused Ops parent-SQL leftover past ontology_plan=36 (#228)


- **Ticket.** Serves [GEN-PATH-CLIMB-12 #228](https://github.com/Netie-AI/dms/issues/228) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #226 RISE_PASS @ `6eb89562` (ontology_plan=36 bind_plan=0 WRONG=0 answered=36/46 on uncapped harness) stands as the measured floor.
- **Climb.** Score leftover Ops parent-SQL of Cortex `certified_queries.yaml` L0s live already answers (`SKU count by category`, `stock value by category`, `shipment cost by destination`) as `ontology_plan`. Same SQL as `cq_sku_count_by_category` / `cq_stock_value_by_category` / `cq_cost_by_destination`. Not PACK_METRICS expansion (prove-path is generative). Ontology spine slot `shipment_cost_by_destination` plus retrieve/overlay lock sku-by-category onto `sku_count`, stock-value onto `stock_value_myr`, shipment-cost onto `shipping_cost_myr`. Harness unions the new L0s so this SHA cannot score the 36/46 pack. Live prove FAILs on ontology_plan<=36 or climb-12 rise L0s not `ontology_plan`. Frozen n=26 stays a floor, not a cap. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 36 + WRONG=0 after deploy. CI green != climb PASS. QUALIFIED claim stays n=26. curated_ceo n=49.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses, pack shrink to fake a higher percent.

## 2026-09-21 - GEN-PATH-CLIMB-11: unused certified leftover past ontology_plan=33 (#226)

- **Ticket.** Serves [GEN-PATH-CLIMB-11 #226](https://github.com/Netie-AI/dms/issues/226) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #224 RISE_PASS @ `f833e567` (ontology_plan=33 bind_plan=0 WRONG=0 answered=33/43 on uncapped harness) stands as the measured floor.
- **Climb.** Score leftover Cortex `certified_queries.yaml` L0s live already answers (Ops `Show warehouse capacity utilisation`, Ops `Show the CCTV camera for warehouse A`, Ops `Which SKUs are below reorder level in warehouse A?`) as `ontology_plan`. Same parent SQL as Finance L0s. Not PACK_METRICS expansion (prove-path is generative). Retrieve/overlay lock utilisation/cctv onto `utilisation_pct` and low-stock onto `below_reorder_lots`. Harness unions the new L0s so this SHA cannot score the 33/43 pack. Live prove FAILs on ontology_plan<=33 or climb-11 rise L0s not `ontology_plan`. Frozen n=26 stays a floor, not a cap. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 33 + WRONG=0 after deploy. CI green != climb PASS. QUALIFIED claim stays n=26. curated_ceo n=46.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses, pack shrink to fake a higher percent.

## 2026-09-21 - GEN-PATH-CLIMB-10: unused certified leftover past ontology_plan=30 (#224)

- **Ticket.** Serves [GEN-PATH-CLIMB-10 #224](https://github.com/Netie-AI/dms/issues/224) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #222 RISE_PASS @ `1257fc3b` (ontology_plan=30 bind_plan=0 WRONG=0 answered=30/40 on uncapped harness) stands as the measured floor.
- **Climb.** Score leftover Cortex `certified_queries.yaml` L0s live already answers (Ops `Which items are expired?`, Ops `Which locations are cold storage?`, Ops `Which locations are above 90 percent capacity?`) as `ontology_plan`. Same parent SQL as Finance L0s. Not PACK_METRICS expansion (prove-path is generative). Retrieve/overlay lock expired onto `stock_value_myr` and cold/capacity onto `utilisation_pct`. Harness unions the new L0s so this SHA cannot score the 30/40 pack. Live prove FAILs on ontology_plan<=30 or climb-10 rise L0s not `ontology_plan`. Frozen n=26 stays a floor, not a cap. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 30 + WRONG=0 after deploy. CI green != climb PASS. QUALIFIED claim stays n=26. curated_ceo n=43.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses, pack shrink to fake a higher percent.

## 2026-09-21 - GEN-PATH-CLIMB-09: unused certified leftover past ontology_plan=27 (#222)

- **Ticket.** Serves [GEN-PATH-CLIMB-09 #222](https://github.com/Netie-AI/dms/issues/222) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #220 RISE_PASS @ `4f189f81` (ontology_plan=27 bind_plan=0 WRONG=0 answered=27/37 on uncapped harness) stands as the measured floor.
- **Climb.** Score leftover Cortex `certified_queries.yaml` L0s live already answers (Ops `How many SKUs in inventory?`, Ops `SKU count in inventory`, Ops `List chemicals in inventory`) as `ontology_plan`. Not PACK_METRICS. Retrieve/overlay lock chemicals onto `stock_value_myr`. Harness unions the new L0s so this SHA cannot score the 27/37 pack. Live prove FAILs on ontology_plan<=27 or climb-09 rise L0s not `ontology_plan`. Frozen n=26 stays a floor, not a cap. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 27 + WRONG=0 after deploy. CI green != climb PASS. QUALIFIED claim stays n=26. curated_ceo n=40.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses, pack shrink to fake a higher percent.

## 2026-09-18 - GEN-PATH-CLIMB-08: unused certified leftover past ontology_plan=24 (#220)

- **Ticket.** Serves [GEN-PATH-CLIMB-08 #220](https://github.com/Netie-AI/dms/issues/220) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #218 RISE_PASS @ `9b73b377` (ontology_plan=24 bind_plan=0 WRONG=0 answered=24/34 on uncapped harness) stands as the measured floor.
- **Climb.** Score leftover Cortex `certified_queries.yaml` L0s live already answers (`top 3 categoty sales`, Ops sku count, Ops sku count by category) as `ontology_plan`. Not PACK_METRICS. Retrieve/overlay aliases certified `categoty` to `category`. Harness unions the new L0s so this SHA cannot score the 24/34 pack. Live prove FAILs on ontology_plan<=24 or climb-08 rise L0s not `ontology_plan`. Frozen n=26 stays a floor, not a cap. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 24 + WRONG=0 after deploy. CI green != climb PASS. QUALIFIED claim stays n=26. curated_ceo n=37.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses, pack shrink to fake a higher percent.

## 2026-09-18 - GEN-PATH-CLIMB-07: unused certified synonyms past ontology_plan=21 (#218)

- **Ticket.** Serves [GEN-PATH-CLIMB-07 #218](https://github.com/Netie-AI/dms/issues/218) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #216 RISE_PASS @ `41f5824f` (ontology_plan=21 bind_plan=0 WRONG=0 answered=21/31 on uncapped harness) stands as the measured floor.
- **Climb.** Score leftover Cortex `certified_queries.yaml` synonyms of L0s live already answers (`Top 5 selling SKUs by sales`, `show top 3 category sales`, `top 3 category sales`) as `ontology_plan`. Not PACK_METRICS. Retrieve lock `category sales` group. Harness unions the new L0s so this SHA cannot score the 21/31 pack. Live prove FAILs on ontology_plan<=21 or climb-07 rise L0s not `ontology_plan`. Frozen n=26 stays a floor, not a cap. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 21 + WRONG=0 after deploy. CI green != climb PASS. QUALIFIED claim stays n=26. curated_ceo n=34.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses, pack shrink to fake a higher percent.

## 2026-09-18 - GEN-PATH-CLIMB-06: dual-flat 17/26 root-cause + rise L0s (#216)

- **Ticket.** Serves [GEN-PATH-CLIMB-06 #216](https://github.com/Netie-AI/dms/issues/216) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. Dual KEEP_HOLD: #212 @ `aa0ef108` and #214 @ `683827ed` both Platform ontology_plan=17 bind_plan=0 WRONG=0 answered=17/26.
- **Diagnosis.** Frozen 26 saturates at 17 L0s; remaining 9 are planted traps (WRONG if greened). Live `--prove-path --url` loads local `tests/fixtures/curated_ceo/questions.yaml` (`score_pack_live`); Studio SHA does not include the pack. #212's `cq_audit_overdue` and #214's four certified synonyms never entered a 17/26 stamp. #214 `_leftover_l0_unscored` only required leftover ids asked, not ontology_plan OK. 3/4 synonyms already lock #210 measures — if POSTed, ontology_plan would be >17. Ranking/ask_path on the original 17 were not no-ops.
- **Climb.** Union rise L0s in the prove harness so this SHA cannot score frozen 26. Live prove FAILs on ontology_plan<=17 or rise L0s not `ontology_plan`. `/health` `gen_path_climb` advertises pack identity (deploy coupling). Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 17 + WRONG=0 after deploy. CI green != climb PASS. A 17/26 stamp is FAIL on this SHA. QUALIFIED claim stays n=26.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses.

## 2026-09-18 - GEN-PATH-CLIMB-05: certified synonyms beyond flat ontology_plan=17 (#214)

- **Ticket.** Serves [GEN-PATH-CLIMB-05 #214](https://github.com/Netie-AI/dms/issues/214) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #212 KEEP_HOLD @ `aa0ef108` (ontology_plan=17 bind_plan=0 WRONG=0 answered=17/26) stands as the measured floor.
- **Diagnosis.** #212 CI GREEN left live flat because (1) all 17 frozen-pack L0s already compiled as ontology_plan and the other 9/26 are planted traps, (2) leftover `cq_audit_overdue` grew n to 27 but Platform scored **17/26** so the new ask never entered the denominator. Ranking/ask_path/retrieve on those 17 were not no-ops. `last_audit_date` on live lake remains a ceiling for that one leftover, not the 17.
- **Climb.** Score Cortex `certified_queries.yaml` synonyms of L0s live already answers (`How many SKUs in inventory?`, `SKU count in inventory`, `Top 5 SKUs by revenue`, `top 3 categories by sales value`) as `ontology_plan`. Not PACK_METRICS. Retrieve lock `categories`+sales. Audit typed-filter no longer None-outs when the verified measure exists. Harness fails closed if leftover L0 ids are not scored. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 17 + WRONG=0 after deploy. CI green != climb PASS. A 17/26 stamp is the frozen pack, not this SHA. QUALIFIED claim stays n=26.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses.

## 2026-09-18 - GEN-PATH-CLIMB-04: leftover Cortex L0 (audit overdue) beyond ontology_plan=17 (#212)

- **Ticket.** Serves [GEN-PATH-CLIMB-04 #212](https://github.com/Netie-AI/dms/issues/212) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #210 RISE_PASS @ `0a5c6a9c` (ontology_plan=17 bind_plan=0 WRONG=0) stands.
- **Climb.** 17/26 L0s were already ontology_plan. Leftover Cortex certified `cq_audit_overdue` ("Which suppliers have an audit overdue?") was not in the score pack and not in PACK_METRICS. Honest `audit_overdue` measure (90-day last_audit FILTER at supplier grain) compiles as `ontology_plan` via exhausted-ranking overlay. Finance has rows. Ops without `suppliers` still ABSTAIN. Planted refuses stay ABSTAIN. Not exact-match pack expansion. FreeRoute `free+normal` retry uses overlay slots. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 17 + WRONG=0 after deploy. CI green != climb PASS. curated_ceo n=27; QUALIFIED claim stays n=26 (floor, not a lock). Frozen baselines are not rewritten.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses.

## 2026-09-18 - GEN-PATH-CLIMB-03: exhausted-ranking pack overlay beyond ontology_plan=13 (#210)

- **Ticket.** Serves [GEN-PATH-CLIMB-03 #210](https://github.com/Netie-AI/dms/issues/210) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #208 RISE_PASS @ `b9836bfa` (ontology_plan=13 bind_plan=0 WRONG=0) stands.
- **Climb.** When prefer-locked ranking walk exhausts (Cortex YAML never ranked the pack id), overlay a question-matched spine pack id onto the retrieve prefer lock. Remaining pack-id group/keep_gt shapes (cold / expired / chemicals / cctv / supplier_rank / low_stock). Honest `supplier_rank_score` (risk+lead formula) so finance ranking compiles as `ontology_plan`; Ops without suppliers still ABSTAIN. Planted refuses stay ABSTAIN. FreeRoute `free+normal` retry uses overlay slots. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 13 + WRONG=0 after deploy. CI green != climb PASS.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses.

## 2026-09-18 - GEN-PATH-CLIMB-02: prefer-locked ranking walk beyond ontology_plan=11 (#208)

- **Ticket.** Serves [GEN-PATH-CLIMB-02 #208](https://github.com/Netie-AI/dms/issues/208) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / estate CLEAR. #180 RISE_PASS @ `58d27a81` (ontology_plan=11 bind_plan=0 WRONG=0) stands.
- **Climb.** Prefer-locked ranking walk past `sku_count` noise on SKU-list asks (top-5 revenue, top-3 volume, low-stock WH-A) so they compile as `ontology_plan` instead of aborting. FreeRoute `free+normal` generate retry sends the walked/resolved measure plus retrieve/pack slots, not the raw top pack id. `sales_top` pack-id overlay. Planted refuses stay ABSTAIN. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan > 11 + WRONG=0 after deploy. CI green != climb PASS.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses.

## 2026-09-18 - GEN-02: FreeRoute k-scale climb beyond #205 (#180)

- **Ticket.** Serves [GEN-02 #180](https://github.com/Netie-AI/dms/issues/180) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 99.95% / 57.69% as AI COMPLETE. Phase A CLEAR stands @ `e1f729a5`. #205 climb PASS @ `186f7d85` (ontology_plan=9 bind_plan=0 WRONG=0).
- **Climb.** FreeRoute `free+normal` ranked generate retry when generate ran but emitted no SQL (not UNARMED). Same-intent ranking walk past noise ids (not skip-to-weaker intent). Pack-id slot overlay (`_by_category` / `topN` / `categoty`). Typed `/dms/query` forwards `ranked_metric`. Planted refuses stay ABSTAIN. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api` and `--climb --ab` on the same origin. Need measured ontology_plan rise vs 9 + WRONG=0 after deploy. CI green != climb PASS.
- **Not this ticket:** #178 COMPLETE, estate CLEAR, ticket close, greening planted refuses.

## 2026-09-18 - STUDIO-MOBILE-03: TopBar phone clip (#192)

- **Ticket.** Serves [STUDIO-MOBILE-03 #192](https://github.com/Netie-AI/dms/issues/192). Follow-on to #182. Does not close tickets. Not COMPLETE. Not #182 product DONE. Not EPIC-008 COMPLETE.
- **TopBar.** Drop `overflow-x-auto` (it clips the New dropdown). Space takes leftover width (`min-w-[8rem] flex-1`). Compact `netie` title removed so Space/New/Operate fit 390px. New menu `z-50`, right-aligned below `lg`.
- **Sources.** Chat heading `max-lg:pr-32` so the painted Sources chip (about 106px) does not sit on the h1. Chip docks under the 48px bar (`top-12`). Operate drawer and #171 Ask/Sources overlay unchanged.
- **Rebase.** Rebased onto main after #199/#201/#203 and #205/#206. Does not invent product DONE. Live phone walk is Platform/Frontend leftover after merge+redeploy.
- **Not this ticket:** live phone walk PASS, #182/#171 DONE, ticket close.

## 2026-09-18 - GEN-PATH-CLIMB-01: raise ontology_plan coverage (WRONG=0) (#205)

- **Ticket.** Serves [GEN-PATH-CLIMB-01 #205](https://github.com/Netie-AI/dms/issues/205) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp epic COMPLETE. Does not invent 57.69% / 99.95% as AI COMPLETE. Phase A path-prove HOLD already CLEARED thin @ `e1f729a5` (ontology_plan=1).
- **Climb.** Cortex Insights ranking pack ids resolve onto DMS measures (exact / `cq_` strip / same-intent spine alias / >=2 token overlap). Retrieve-typed group/filter/limit overlay so "by category" is not a scalar. Generate `intent_slots` forwarded. Invalid generate SELECT may climb via ranked slots; hostile SQL does not. Isolated gen still must not bind_plan over Insights reached. FreeRoute `free+normal` only. No LIVE_KEY / `:5000` invent.
- **Harness.** CI plants ontology_plan>1. Live leftover is Platform: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`. Need measured ontology_plan>1 + WRONG=0 after deploy. #180 waits on that rise.
- **Not this ticket:** epic COMPLETE, #180 k-scale done, ticket close, estate CLEAR.

## 2026-09-18 - GEN-PATH-ROUTE-02: prove can count ontology_plan>=1 (#203)

- **Ticket.** Serves [GEN-PATH-ROUTE-02 #203](https://github.com/Netie-AI/dms/issues/203) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp Phase A CLEAR / COMPLETE. Does not invent 57.69% as Cortex AI.
- **Root cause (#201 leftover).** Isolated `ask_path=generative` `bind_on_miss` ran whenever Cortex Insights `generate=true` returned 200 REFUSE (FreeRoute unarmed) or 401 (A-0009: Authorization must be `ov_`, not `dms-demo-viewer-key`). `compute_query` treated that as a transport miss, so prove labeled the local keyword bind (`bind_plan=15` / `ontology_plan=0`). Offline `--prove-path` still has no Cortex. Deploy lag vs `a5b6fb1e` may have added to the first Platform stamp; the bind swallow remains after deploy.
- **Fix.** Insights 200/401 JSON is reached — do not bind_plan over it. A-0009 401 omits ranking; `GET /v1/insights/ontology` attaches YAML ranking (no FreeRoute). Top ranked Cortex metric id on the DMS ontology compiles as `ontology_plan`. Insights SELECT SQL still validate-or-abstain. Transport miss only may still bind on isolated gen. Prove timeout default 120s. No LIVE_KEY / `:5000` invent. No second vault.
- **Harness.** `--prove-path` counts `ontology_plan>=1` when the Insights path returns a stamped plan. Offline AB remains QUALIFIED bind. Platform live leftover after deploy.
- **Not this ticket:** Phase A HOLD clear, 57.69% as AI, #180 k-scale, ticket close.

## 2026-09-18 - GEN-PATH-ROUTE-01: Studio/prove generative hits Cortex ontology_plan (#201)

- **Ticket.** Serves [GEN-PATH-ROUTE-01 #201](https://github.com/Netie-AI/dms/issues/201) under EPIC-INSIGHTS-UX #178. Does not close tickets. Does not stamp Phase A CLEAR / COMPLETE. Does not invent 57.69% as Cortex AI.
- **Wire.** Generative compute posts Cortex `POST /v1/insights` `generate=true ask=false` (OV/FreeRoute free+normal inside Cortex) then typed `POST /dms/query`. Insights SELECT SQL is validate-or-abstain then Cortex submit. Envelope `plan_source=ontology_plan`. Isolated gen may still `bind_plan` on miss. No second vault. No LIVE_KEY / `:5000` invent.
- **Harness.** Offline `--prove-path` remains bind_plan (no Cortex). Platform re-runs `--prove-path --url https://studio.netie.ai/api` after deploy. Recipe: `scripts/score_climb.md` GEN-PATH-ROUTE-01.
- **Not this ticket:** live ontology_plan>0 stamp, Phase A HOLD clear, #180 k-scale, ticket close.

## 2026-09-18 - GEN-PATH-PROVE-01: ontology_plan vs bind_plan labels (#199)

- **Ticket.** Serves [GEN-PATH-PROVE-01 #199](https://github.com/Netie-AI/dms/issues/199) under EPIC-INSIGHTS-UX #178 Phase A HOLD. Does not close tickets. Does not stamp COMPLETE. Does not invent 57.69% as an AI-path prove.
- **Telemetry.** `bind_plan` stamps `plan_source=bind_plan`. Cortex `POST /dms/query` stamps `ontology_plan` (or copies engine `mode`/`plan_source`). Generative envelopes copy that field. Missing field is `other` -- harness does not guess from SQL or assumption strings.
- **Harness.** `python scripts/score_curated.py --prove-path` (offline, CI-safe) and `--prove-path --url https://studio.netie.ai/api` (Platform). Per-qid label, count/%, WRONG=0, `Phase A HOLD may clear` YES/NO. Offline `--ab` compute is local `bind_plan`, so it cannot clear HOLD. Live numbers are Platform after merge.
- **Trust.** `GET /v1/trust/summary` forwards `score_gen_path_prove.json` when present. Read-only. No LIVE_KEY invent. `#192`/`#198` untouched.
- **Not this ticket:** live Studio prove PASS, Phase A HOLD clear, epic COMPLETE, ticket close.

## 2026-09-18 - INSIGHTS-HOST-01: consume Cortex GET|POST /v1/insights (#196)

- **Ticket.** Serves [INSIGHTS-HOST-01 #196](https://github.com/Netie-AI/dms/issues/196) under EPIC-INSIGHTS-UX #178 + Platform Track A. Does not close tickets. Not COMPLETE. Not #178 COMPLETE.
- **Wire.** `GET|POST /v1/insights` on the DMS API forwards Cortex #213 (`6f701e93`) using `settings.cortex_api_key` (OV-custodied founder key on prove/studio). Off-contract sibling; cortex-contract stays 1.2.0. Generate fails closed if OpenVault is unreachable. No demo-number fallback.
- **Honesty.** Keys never logged. `live_5000_ci` forced false. Does not invent LIVE_KEY or `:5000` green. Cortex `#197`–`#200` not seated.
- **Not this ticket:** live prove/studio walk PASS, EPIC COMPLETE, second vault, ticket close.

## 2026-09-13 - INSIGHTS-EXPORT-01: Excel from real ask envelope (#188)

- **Ticket.** Serves [INSIGHTS-EXPORT-01 #188](https://github.com/Netie-AI/dms/issues/188) under EPIC-INSIGHTS-UX #178 Phase B. Does not close tickets. Not COMPLETE. Not EPIC-019 / #108 COMPLETE. Not #29 FRTR.
- **Export.** `POST /v1/chat/export.xlsx` serializes an existing ask envelope (gen or validated) to .xlsx via stdlib OOXML. Copies Cover + Values + Rows as received. Refuses without `answer_id` + badge. Does not re-ask Cortex or invent rows.
- **UI.** Download Excel on the answer posts that envelope. Filename `dms_answer_<answer_id>.xlsx` (no clock).
- **Not this ticket:** Power BI / Superset (#189), FRTR Copilot (#29), ticket close, COMPLETE.

## 2026-09-13 - SCORE-CLIENT-01: climb/A/B httpx vs urllib CF1010 (#187)

- **Ticket.** Serves [SCORE-CLIENT-01 #187](https://github.com/Netie-AI/dms/issues/187) under EPIC-INSIGHTS-UX #178. Does not close tickets. Not COMPLETE. Not 99.95%.
- **Transport.** `scripts/score_curated.py --climb` / `--climb --ab` probe `/health` and POST `/v1/chat/ask` via httpx (`score_http`). urllib CF1010s `https://studio.netie.ai`. CF1010 is BLOCKED (named, not IAP) and is not a grant-ABSTAIN. IAP 401/403 still BLOCKED.
- **Not this ticket:** live Studio remeasure, #178 COMPLETE, greening planted refuses, GitHub CI `--climb`, ticket close.

## 2026-09-13 - STUDIO-MOBILE-02: Operate LeftNav drawer + TopBar phone (#182)

- **Ticket.** Serves [STUDIO-MOBILE-02 #182](https://github.com/Netie-AI/dms/issues/182). Follow-on to #171. Does not close tickets. Not COMPLETE. Not EPIC-008 COMPLETE. Not #171 product DONE.
- **Nav.** Below `lg`, LeftNav is a closed drawer overlay (`left-nav-slot` is `max-lg:w-0`). Operate no longer docks `w-52` over chat. Desktop cream rail / graphite column unchanged.
- **TopBar.** Secondary chrome (Spaces, Library, API, role) hides below `lg`. Compact `netie` title. `overflow-x-auto` instead of clip. Short labels from #171 kept.
- **Not this ticket:** live phone walk PASS, #171 DONE, OpenVault user key UI, ticket close.

## 2026-09-13 - GEN-02 founder lock: generate SQL then validate (#180)

- **Ticket.** Serves [GEN-02 #180](https://github.com/Netie-AI/dms/issues/180) / PR #185. Does not close tickets. Not COMPLETE. Not 99.95%.
- **Try.** Isolated gen: retrieve (schema+spine YAML+hybrid_fuse) -> ontology compile -> execute -> validate. `keep_gt` from "above N percent" filters rows after execute; empty is ABSTAIN. Offline `--ab` now runs compiled SQL on the demo lake (not dummy rows).
- **Not.** ML route/train/apply (parked). LangChain/LangGraph. Pack SQL copy (supplier 0.65/0.35 formula stays miss). Planted refuses stay ABSTAIN.
- **Measured (offline `--ab`, this seat):** gen 15/26 answered WRONG=0 (frozen prove live gen remains 1/26 = 3.85 pct). Exact 10/26 WRONG=0.


## 2026-09-13 - GEN-02 YAML retrieve pack + typed lake filters (#180)

- **Ticket.** Serves [GEN-02 #180](https://github.com/Netie-AI/dms/issues/180) / PR #185. Does not close tickets. Not COMPLETE. Not 99.95%.
- **Retrieve.** `packages/executor/dms_executor/ontology_spine.yaml` is the slot-name pack `retrieve_short_context` allowlists (no SQL). Compile stays verified `demo_ontology`. Not a vendor YAML format.
- **Generate SQL.** Isolated gen binds typed lake filters (cold storage, expiry, WH-A, chemicals, below-reorder lots, CCTV) then compile/validate. Above-90 and supplier-rank still miss (no typed slot). Planted refuses stay ABSTAIN.
- **Measured (offline `--ab`, this seat, not Studio):** gen 14/26 answered WRONG=0 (was 9; frozen prove live gen remains 1/26 = 3.85 pct). Exact 10/26 WRONG=0.
- **Must not:** invent live Studio 14/26, pack-expansion-as-strategy, vendor paste, ticket close.


## 2026-09-13 - GEN-02 Distill ladder in harness (ideas only, #180)

- **Ticket.** Serves [GEN-02 #180](https://github.com/Netie-AI/dms/issues/180) / PR #185. Does not close #180 or #178. Not COMPLETE. Not 99.95%.
- **Mapping.** Certified-first then free gen; `demo_ontology` retrieve spine + slot-name YAML `tests/fixtures/curated_ceo/ontology_spine.yaml` (no SQL, not a vendor pack); `hybrid_fuse` + CRAG grades; Cortex `POST /dms/query` + `bind_plan` (no text2sql SDK). No paste from DB-GPT / mybot / n8n / OpenWillow / guaca / rakazo.
- **Try.** Isolated gen retrieve+bind miss is ABSTAIN after the attempt, not silent None. Product path still Cortex-asks on compute miss. Frozen prove A/B @ `a9578348`: exact 38.46 pct / gen 3.85 pct, WRONG=0. Offline `--ab` is a separate measurement.
- **Must not:** invent live Studio coverage, green planted refuses, pack expansion as the climb, GitHub CI `--climb`, ticket close.


## 2026-09-13 - GEN-02 retrieve bind on Cortex compute miss (#180)

- **Ticket.** Serves [GEN-02 #180](https://github.com/Netie-AI/dms/issues/180). Does not close #180 or #178. Not COMPLETE. Not 99.95%.
- **Climb.** Isolated gen @ `a9578348` was 1/26 with WRONG=0: Cortex `POST /dms/query` rarely returns a typed `query_plan`, so the gen lane missed. On compute **miss only** and `ask_path=generative`, bind a typed plan from retrieved ontology, then the same compile → EXPLAIN/grant validate → Cortex submit. Product path still misses into Cortex certified ask (VQ-01 / pack leftovers). Explicit Cortex `unsure` still abstains. Not pack expansion. Offline `--ab` (this seat, not Studio): gen answered **9/26** WRONG=0 (rose vs 1). Live `--climb --ab` is still Platform.
- **Ontology.** `demo_ontology` reads columns on disk (thin reseed vs Cortex lake): no `storage_bin` / shipment `supplier_id` claims the lake does not have. Honest measures `sku_count`, `outbound_kg`, `utilisation_pct`. Bind misses list/which asks and untyped filters (WH-A, cold, expired, CCTV, above-90). Ops spend still grant-abstains.
- **Must not:** invent live Studio coverage, green planted refuses, GitHub CI `--climb`, ticket close, keys in chat.


## 2026-09-13 - SCORE-BIRD-01: bronze batches, leftover trap skip (#184)

- **Ticket.** Serves [SCORE-BIRD-01 #184](https://github.com/Netie-AI/dms/issues/184). Does not close tickets. Not COMPLETE.
- **Grow.** Pack snapshot is not a ceiling. `--live` lists Studio bronze and prints `target=75 measured=N leftover=75-N`. First GO batch stays `gender`.
- **Skip.** Leftover traps whose `needs_table` has landed SKIP (no invented oracle). `trap_75_tables` and demo-pack bleed stay refuse.
- **Not this ticket:** EPIC-020b / #108 COMPLETE, Mini-Dev coverage from a partial batch, ticket close.


## 2026-09-13 - SCORE-BIRD-01: measured live harness on BIRD Space (#184)

- **Ticket.** Serves [SCORE-BIRD-01 #184](https://github.com/Netie-AI/dms/issues/184) under EPIC-020b #173. Does not close #184 or #173. Not COMPLETE.
- **Harness.** `python scripts/score_bird.py --self-check` (CI). `--live` requires `DMS_API_BASE` and A/B's exact-match pack miss vs `POST /v1/chat/ask` (GEN-01 #179 @ `a9578348`). No 127.0.0.1:8090 default. WRONG=0 law. Precision `n/a` when 0 answered.
- **Honesty.** Space `f0da7dd3-58b3-4d15-84a8-a18f2853ed87` source_count=1 data_source `12b6f170` bounded `gender` max_rows=50. Full 75-table Mini-Dev extract is Platform leftover. Runbook: `scripts/score_bird.md`.
- **Not this ticket:** EPIC-020b / #108 COMPLETE, 99.95%, DB-GPT clone, GEN-02 curated climb, live counts from a cloud seat, ticket close.


## 2026-09-13 - GEN-02 live A/B + CRAG validate-or-abstain harness (#180)

- **SoT widen.** Isolated live A/B: `POST /v1/chat/ask` `ask_path=exact|generative|product` (certified-first then free gen). Platform: `python scripts/score_curated.py --climb --ab --url https://studio.netie.ai/api`.
- **CRAG-style.** Harness grades gen envelopes `validated` / `abstain_validate` / `abstain_gate` (execute-validate or abstain). Ideas only; not a DB-GPT/CRAG clone. Doc RAG CRAG stays parked.
- **Baseline.** A/B @ `a9578348` exact answered 10/26, gen 1/26, WRONG=0. Offline `--ab` uses demo ontology retrieve (not pack expand). Frozen product-path @ `91c5cc99` unchanged.
- **Must not:** 99.95%/COMPLETE, greening planted refuses, GitHub CI live ask, ticket close, keys in chat.


## 2026-09-13 - GEN-02 measured live coverage climb harness (#180)

- **Ticket.** Serves [GEN-02 #180](https://github.com/Netie-AI/dms/issues/180) under EPIC-GEN-01 #178 after GEN-01 @ `a9578348`. Does not close tickets. Not COMPLETE. Not 99.95%.
- **Harness.** `python scripts/score_curated.py --climb --url https://studio.netie.ai/api` (or `DMS_API_BASE`). Reports real OK/LAYER/ABSTAIN/WRONG vs frozen baseline @ `91c5cc99` (OK7 LAYER10 ABSTAIN9 WRONG0). `answered_by_path` splits `route=generated` vs exact-match pack. WRONG=0 is FAIL if broken. Unreachable host or IAP 401/403 is BLOCKED, not a fake score. `--climb` has no laptop default.
- **Not this ticket:** pack expansion, greening planted refuses, GitHub CI live ask, EPIC-019 COMPLETE, reopen #108, ticket close, keys in chat.


## 2026-09-13 - GEN-01 semantic retrieve + A/B vs exact-match (#179)

- **Ticket.** Serves [GEN-01 #179](https://github.com/Netie-AI/dms/issues/179) under EPIC-INSIGHTS-UX #178 after SoT widen. Does not close tickets. Not COMPLETE.
- **Retrieve.** Schema `information_schema` SQL-filter + ontology slice + DISTINCT encodings, summarized to short context. Compute gets that blob, not the full catalog. No DB-GPT clone, no extra dep, no keys.
- **A/B.** `python scripts/score_curated.py --ab` scores exact-match pack vs retrieve+bind generative on the same curated_ceo pack. FAIL if either path WRONG>0.
- **Not this ticket:** pack expansion, EPIC-019 COMPLETE, GEN-02 live harness, reopen #108.


## 2026-09-13 - GEN-01 ontology-grounded generative ask (#179)

- **Ticket.** Serves [GEN-01 #179](https://github.com/Netie-AI/dms/issues/179) under EPIC-GEN-01 #178. Does not close #179 or #178. Not COMPLETE.
- **Path.** After VQ/pack/VQ-04 refuse and bronze, a non-exact-match ask may take GEN-01: Cortex `POST /dms/query` (compute) returns a typed ontology plan; `Ontology.compile` emits SQL; hostile/grant/EXPLAIN validate; Cortex submit + ledger. Badge `L2_VALIDATED` or `ABSTAIN`. Missing compute misses into contract ask.
- **Fail-closed.** Unsure compute, vague "just give me" / "worry about", VQ-04 planted paraphrases, compile refuse, ungranted relations, EXPLAIN fail: no execute. OpenVault keys stay in Cortex; empty api_key is not invented.
- **Not this ticket:** EPIC-019 COMPLETE, certified pack expansion, greening planted refuses, GEN-02 live harness, reopen #108, ticket close.


## 2026-09-13 - SQLSRC-PG-01: kind=postgresql on Studio SQLSRC (#172)

- **Ticket.** Serves [SQLSRC-PG-01 #172](https://github.com/Netie-AI/dms/issues/172) under EPIC-020b #173. Does not close #172 or #173. Not COMPLETE.
- **Kind.** `SqlSourceIn`, executor `SourceConfig`, and Studio `SqlSourcePanel` accept `kind=postgresql` next to sqlserver|mysql. Default port 5432. Catalog is ANSI INFORMATION_SCHEMA (database is not a schema). Driver is process `psycopg`, lazy import.
- **Laws.** Extract-only into bronze under the Space. Password is request-only. Receipt/verify unchanged. sqlserver|mysql behavior unchanged. `kind=postgres` stays 422.
- **Not this ticket:** Platform attach of `bird_minidev` into BIRD Space `f0da7dd3-58b3-4d15-84a8-a18f2853ed87`, SCORE-BIRD-01 PASS, reopen EPIC-020 #108 COMPLETE, EPIC-008 COMPLETE, keys in chat.


## 2026-09-13 - VQ-04 harden planted refuse traps after #175 (WRONG2)

- **Ticket.** Serves [VQ-04 #176](https://github.com/Netie-AI/dms/issues/176) under EPIC-019 #38. Does not close #176 or #38. Not COMPLETE.
- **Cause.** Live `score_curated` after PR #175 @ `497d1901` was OK7 LAYER10 ABSTAIN7 WRONG2. The 7 L0-gap LAYER lifts stay. Cortex L1 greened planted refuse: `trap_how_full_synonym` via vocabulary `how full` -> capacity utilisation; `trap_delayed_count` via `route_to_metric` delayed+per+warehouse -> `count_by_destination`. Neither is a `certified_queries.yaml` synonym (delayed golden is TARGET).
- **Fix.** Exact-phrase refuse on those two asks (same `_norm` as pack, not an intent regex). Intercept before `cortex.ask`. Map also fail-closes if Cortex still returns L1 (E9: no engine figures on ABSTAIN). VQ-03 exact certified phrases still pack-hit.
- **Not this ticket:** EPIC-019 / VQ-03 COMPLETE, greening traps for coverage, weakening gates, reopening EPIC-008, ticket close.


## 2026-09-13 - STUDIO-MOBILE-01: composer ink + TopBar phone labels (#171)

- **Composer.** Chat textarea now sets `text` / `placeholder` / `caret` to `--color-ink` so graphite does not paint typed words as transparent UA chrome.
- **TopBar.** Below `lg`: `New`, `Spaces`, `Operate`/`Ask`. At `lg`: `+ New`, `Manage`, `Switch to Operate/Ask`. DB Library from `sm`.
- **Not this ticket:** EPIC-008 COMPLETE, close #171/#8.

## 2026-09-13 - STUDIO-MOBILE-01: live 390px dock cannot zero chat (#171)

- **Live walk.** Frontend Support: 390x844 SOURCES open = 352px flex sibling, main width 0. Collapse works; arriving answer reopens the dock. Header scrollWidth 534.
- **Fix.** AppShell slot is `max-lg:w-0` so the dock is not a 22rem flex sibling below lg. Ask and value-trace do not expand the dock below lg. TopBar short labels (New / Operate / Ask; Manage from sm) to stop <=430 page scroll.
- **Not this ticket:** EPIC-008 COMPLETE, close #171/#8, public `:8090`.

## 2026-09-13 - VQ-03 recover 7 live expect:l0 ABSTAIN gaps (#170)

- **Ticket.** Serves [VQ-03 #170](https://github.com/Netie-AI/dms/issues/170) under EPIC-019 #38. Does not close #170 or #38. Not COMPLETE.
- **Pack.** Exact-match the 7 measured gaps onto Cortex `certified_queries.yaml` SQL and Cortex-submit (F83). Layer is `L1_GOVERNED_METRIC`. `how full is each warehouse` is not a synonym.
- **Thin seed.** SCHEMA_VERSION 4 adds `location_code`, `is_cold_storage`, `cctv_camera_id`, `expiry_date` so pack SQL can execute in tests. Does not overwrite the prove Cortex lake.
- **Platform recycle.** If prove Cortex lake lacks those columns, submit misses and the qid stays ABSTAIN. Recycle Cortex with the dms pack lake. Do not invent PASS.
- **Not this ticket:** EPIC-008 COMPLETE, EPIC-019 COMPLETE, public `:8090`, lake / `LIVE_KEY_ID`, greening planted traps, ticket close.


## 2026-09-13 - STUDIO-MOBILE-01: Sources drawer below lg (#171)

- **Ticket.** Serves [STUDIO-MOBILE-01 #171](https://github.com/Netie-AI/dms/issues/171). Parent is EPIC-008 residual polish, not a reopen of COMPLETE. Does not close #171 or #8.
- **Chat.** Real covering path was `SourcePanel` as a `shrink-0 w-[22rem]` flex sibling, default open. Below `lg` it is now a drawer, closed by default, with an explicit Sources control. After ask it does not auto-open on phone-width. Desktop `lg` dock unchanged.
- **Studio.** Files + SQLSRC stay on `lg:grid-cols-[22rem_1fr]`. Below `lg` they start collapsed behind a Sources control so preview / certified ask is reachable.
- **Not this ticket:** EPIC-008 COMPLETE, public `:8090`, OpenVault user key UI, LeftNav rewrite, ticket close.


## 2026-09-13 - curated_ceo pack 14 -> 26 (SCORE-PACK-01 #168)

- **Ticket.** Serves [SCORE-PACK-01 #168](https://github.com/Netie-AI/dms/issues/168). Not parented under EPIC-008 (#8). Does not close #168 or #8. Not COMPLETE.
- **Pack.** `tests/fixtures/curated_ceo/questions.yaml` is 26 cases. New L0
  expects are Cortex `packs/dms/semantic/certified_queries.yaml` only
  (cold storage, capacity>90, expired, chemicals, supplier rank, CCTV WH-A).
- **Refuse.** Alerts ungranted, high-risk pending (suppliers+shipments split
  across Spaces), Ops supplier rank, delayed-count TARGET, stock-by-bin, and
  "how full is each warehouse" (paraphrase, no certified synonym). Green on those
  is WRONG. Existing traps stay fail-closed.
- **Oracles.** `tests/fixtures/curated_ceo/oracles.yaml` holds the Cortex SQL.
  `score_curated --self-check` fails any expect:l0 without SQL. 403/409 grant
  refusal scores as ABSTAIN, not transport WRONG.
- **Not this ticket:** EPIC-008 COMPLETE, EPIC-019 VQ repo, live :8090, lake /
  `LIVE_KEY_ID`, dual-write of another pack, ticket close.

## 2026-09-13 - Library parallel list: one DuckDB attach per file

- **Cause.** Push CI on `b5f02be` (run 34750069692) failed `test_parallel_library_lists_same_file`. DuckDB 1.5 unique-file-handle refuses a second RW attach of the same file (`browse.duckdb` -> alias `browse`). `ensure_demo_warehouse` probed schema with another `connect()`, caught the BinderException as "schema missing", then connected again to reseed.
- **Fix.** Per-file attach lock. `connect_file` / `connect_readonly` hold one live RW handle until `close()`. Seeded fast path does not probe via a second connect. Library `/tree` lists serialize instead of 500.
- **Not this ticket.** P-DMS-34 (ingest overlapping ask), lake / `LIVE_KEY_ID`, public `:8090`, ticket close.



## 2026-09-13 - DEMO-HOST-02: measured host-online smoke (#164)

- **Cause.** Host-online COMPLETE needs a walked path through the Platform tunnel, not a `verify_demo_live.py` re-run and not a cloud seat pretending it can see prove `127.0.0.1:8090`.
- **Smoke.** `scripts/smoke_studio_host_online.py` + `scripts/smoke_studio_iap.md`. Fail closed: `STUDIO_ORIGIN` / `DMS_API_BASE` / `LOCAL_TUNNEL_PORT` have no laptop default. `0.0.0.0` refused. Unset loopback is BLOCKED (`error.type=env.unset`, owner=Platform/tunnel).
- **Honesty.** Temp CF hostname **ROTATE RISK** until founder GO durable `TUNNEL_TOKEN`. DMS `:8090` stays loopback. Cursor cloud is not the VPC certifying seat. Ask/upload BLOCKED prints `error.type` -- never invent PASS. Does not claim EPIC-008 COMPLETE.
- **Not this ticket:** public `:8090`, lake / `LIVE_KEY_ID`, standing the tunnel, weakening `verify_demo_live` 31/31.


## 2026-09-13 - DEMO-HOST-01: pin temp CF Studio hostname (#163)

- **Studio URL.** Platform quick tunnel (Platform/DevOps own it): `https://occurred-guest-guaranteed-practitioners.trycloudflare.com`. May rotate until a durable `TUNNEL_TOKEN`.
- **Smoke.** `GET /` and `/api/health` 200; health `database.backend=postgres`, `persistent=true`. DMS `:8090` stays loopback-only -- no public open.
- **Not this ticket:** EPIC-008 COMPLETE, DEMO-HOST-02 measured walk, standing the tunnel from dms.


## 2026-09-13 - DEMO-HOST-01: prove IAP/CF walk in the runbook (#163)

- **Cause.** `docs/DEMO_RUNBOOK.md` only listed laptop `127.0.0.1:3000` / `Start-DMS.bat`. Prove host-harden is UP (Platform: systemd, Spaces postgres, IAP to loopback `:8090`) with no founder/buyer walk that says open Studio via IAP/CF, point or upload, ask, see the envelope -- without a public `:8090`.
- **Docs.** Section **2.1 Prove host-online (IAP/CF)**: ownership table, tunnel start cites Platform (EPIC-008 #8 GO; recipe is not in this repo), `{STUDIO_URL}/studio`, 2-min walk (SQLSRC-09 form or file upload -> ask -> envelope). DR-0004 Option A restated. `:8090` stays `127.0.0.1`; no `0.0.0.0/0`.
- **UI.** Re-derived `StudioPage` / `SqlSourcePanel` / `apps/ui/src/lib/api.ts`: same-origin `/api` (Vite `VITE_API_TARGET` -> host `127.0.0.1:8090`). No Studio copy claimed localhost-only; no bind-address change.
- **Not this ticket:** stand the tunnel, public `:8090`, lake / `LIVE_KEY_ID` / OV e2-micro, EPIC-008 COMPLETE, DEMO-HOST-02 smoke, reopen ENV-E4 / CSV-01 / INGEST-SYNC.


## 2026-09-10 - F32 skip demo-lake SQL; pack leftover asks (EPIC-020)

- **Cause.** Lake is rich after hard restart. The 7 `verify_demo_live` ABSTAINs
  were E9-02/F32 on `cq_spend_by_country` / stock-by-category: Cortex matched
  and returned rows; DMS demoted a lake ranking as a Sales vs Wide_Fill scope
  conflict. Column-card `container_member` labels and bronze sheet-sibling
  shape were the false-positive inputs. F32 runs before E12, so the scalar
  total-spend leftover looked the same when the parent never stored a turn.
- **Envelope.** Derived F32 skips SQL that only cites `DEMO_TABLES` or
  Cortex `warehouse_<table>` aliases (not `bronze.*`). Quoted
  `"warehouse"."inventory"` stays a lake join (do not split on quote
  characters). Sheet-sibling fallback is bronze ingest labels. Column
  cards are not sheets. `warehouse_*` grants are not workbook sheets.
  A competing set that is only those lake aliases does not demote -- the
  live leftover sentence named warehouse_inventory / warehouse_locations /
  warehouse_suppliers / warehouse_transactions. Pack accepts Cortex
  `warehouse_<table>` grant aliases. Wide_Fill SQL and Summary/Detail
  workbook shape still demote. Explicit Sales vs Wide_Fill plants unchanged.
- **Pack.** Exact-match `spend_by_country`, `stock_value_by_category`,
  `total_spend` via Cortex submit + ledger (F83, no local DuckDB fallback).
  Warehouse Ops is not granted `suppliers`, so spend misses there. Stock
  answers in both Spaces.
- **Follow-up.** Closed list: `average of them`, `add N`. Arithmetic in
  `packages/executor`. Honest ABSTAIN if the prior turn has no numbers.
- **Warehouse.** SCHEMA_VERSION 3 thin seed includes `suppliers.country` /
  `inventory.category`. `ensure_demo_warehouse` always reseeds the DMS
  local file on first process call. Do not keep a rich lake on that path.
  Founder rich file is Cortex `/var/cortex/data/dms_demo.duckdb`. Do not
  point `DMS_WAREHOUSE_DB` at it. Pack does not probe local columns.
- **Not this ticket:** #116 live certify, EPIC-020 COMPLETE, invent COMPLETE.

## 2026-09-06 - Studio point-UI for SQL Server/MySQL (SQLSRC-09, #158)

- **Form.** Studio offers SQL Server / MySQL connection fields the route already
  accepts and POSTs `/v1/studio/sources/sql`. The API still owns `compliance_gate`.
- **Receipt.** 200 renders tables pulled, `extracted_at`, per-item `truncated`,
  and #156/#157 per-link cardinality / violations in steward words. A failed
  link is named; extract success is still shown.
- **Password.** Uncontrolled field, cleared after the request. Error copy
  redacts the just-submitted secret. Not stored.
- **R-0007.** vitest goes red if the submit handler does not call
  `POST /api/v1/studio/sources/sql`.
- **Not this ticket:** ask/chat ontology wiring, #116 live certify,
  EPIC-020 COMPLETE, OpenVault connection profiles, password storage.

## 2026-09-06 - verify() refuses orphans a max_rows cap invents (SQLSRC-07, #157)

- **`fk_intact` is now a verify() claim.** Every non-NULL child key must exist
  in the parent. Orphan row count and distinct orphan key count go on the
  violation; the link stays unverified so compile() refuses to join through it.
  NULL child keys stay optional FKs - LEFT JOIN is not reverted.
- **A cap and a dirty source produce different messages, same refusal.**
  `truncated` is copied from `SourcePull` onto `manifest_entry` and into
  `from_manifest`. The ontology does not read the bronze registry. A capped
  parent names `max_rows` and the parent table; a dirty source says the source
  is dirty.
- **The customer artifact is the sql_source receipt.** `verify_source_links`
  already ran on `POST /v1/studio/sources/sql` (#156); it now carries
  `fk_intact` when the extract invented orphans. The extract still lands.
- **R-0007.** `scripts/repro_capped_parent_orphans.py` exits 0. Deleting the
  claim turns the new tests red. A capped parent with no orphans is still
  many-to-one (R-0005).
- **Not this ticket:** SQLSRC-09 #158. #116 live verify. Chat/ask consulting
  the ontology. EPIC-020 COMPLETE.

## 2026-09-06 - the declared join is measured against the landed rows (SQLSRC-08, #156)

- **The semantic layer moved into `dms_executor`.** It lived in `scripts/`,
  which `packages/` cannot import, so `verify()` - the function EPIC-020
  acceptance clause 2 promises will "refuse, naming the link, any link the data
  violates" - was reachable only from bench scripts and had never run against a
  customer extract. It also calls `duckdb.execute`, which hard rule 7 permits
  only inside `packages/executor`. `scripts/ontology.py` stays as the CLI entry.
  `ROOT` moved from `parents[1]` to `parents[3]`, which is the one thing a move
  like this breaks silently.
- **`POST /v1/studio/sources/sql` now measures and reports.** The connector had
  been building `manifest_entry` - the exact shape `from_manifest` consumes,
  documented as such in its own docstring - putting it in the receipt, and
  dropping it. The seam was wired at one end. `verify_source_links` builds the
  ontology over the landed bronze relations and puts the measured cardinality of
  every declared link on the receipt, by name.
- **A doubtful join does not fail an extract.** A link that cannot be measured is
  reported unverified, the same outcome as one measured and found broken: the
  compiler refuses to join through either. The rows landed and their provenance
  is real.
- **A source declaring no foreign keys reports `measured: false`, not
  `verified: true`.** An empty claim set answered with a green result is R-0011's
  silent fallback, and it is the shape a steward reads as "the joins are safe".
- Gates are on the receipt, not the connector's return value (R-0001), and were
  shown able to fail first (R-0007): replacing the call with a hardcoded clean
  bill turns exactly the two new tests red. The bench corpus is unchanged - 896
  cases over 494 shapes, 811 answerable, precision 100.00 pct, 0 wrong (R-0005).
- **Still open, and stated rather than implied:** `verify()` has no
  referential-integrity claim at all, so the orphans a `max_rows` cap invents are
  still unrefused (#157, F-0046). EPIC-020 may not report COMPLETE until it lands.

## 2026-09-05 - EPIC-025: gold promote proves the ledger entry, not the claim

- **Gate.** `run_promote` no longer trusts `GoldMetricDef.is_signed`. A gold
  pipeline calls Cortex `POST /v1/contract/ledger/verify` (the contract 1.2.0
  read-back; there is no get-entry on this pin) before it materialises. Missing
  verify, a broken chain, or an unreachable Cortex refuses the promote and
  treats the metric as unsigned. Construction-time verify in `sign_gold_metric`
  stays; it is not this gate.
- **Invariant.** `tests/invariants/test_actor_trust_boundary.py` now scans every
  route request body, every ledger-append `actor=`, and every `GoldMetricDef`
  built in `apps/api`. No request field or header may name the ledger actor or
  an attestation. Guard-the-guard tests fail if those scans go empty (R-0007).
- Re-uses existing `verify_ledger` / `/v1/audit/ledger/verify`. No local hash
  chain, no C2 allowlist change, no CortexOS import.

## 2026-09-05 - MCP-01 tools wrap existing HTTP (EPIC-014, flag off)

- **MCP-01 (#20).** GET /v1/mcp/tools and POST /v1/mcp/call expose ask,
  preview, and list_metrics by calling the same handlers as
  POST /v1/chat/ask, warehouse preview, and GET /v1/ontology/metrics.
- **Flag.** DMS_MCP=0 by default: the router is not mounted. Swap: IDE MCP
  client. No new serving engine, no CortexOS import, no cortex-contract bump
  (McpCallIn name+arguments already exists).
- **Gate.** POST /v1/mcp/call calls compliance_gate; preview still refuses
  ungranted tables. Tests assert badge/values/rows match the HTTP path.

## 2026-09-05 - Verified-query L0 requires Cortex submit + ledger (F83 / EPIC-019)

- **Hole.** Studio-registered hits executed SQL in local DuckDB and stamped
  L0_CERTIFIED with cortex.asks == []. Engine certification was forged.
- **Fix.** A Space hit still skips the CCA cascade (steward already decided),
  then live_ask submits the stored SQL through existing Cortex submit_sql
  and appends ask.verified_query to the Cortex ledger. L0 is minted only
  when submit returns SQL output and the ledger returns a real hash distinct
  from entry_id. Missing submit/ledger does not fall back to local execute.
- **Tests.** FakeCortex: in-space L0 asserts sql submit + ledger; bind-only
  submit and hash==entry_id fall through to ask. No product regex for F32.
  dms#38 residual. Live Cortex still required to close the epic.

## 2026-09-05 - Constraint Cascade Ask binds ambiguous filters before L0 (EPIC-CCA)

- **Binder.** One matching rule for every cascade stage
  (`dms_executor/cca/binder.py`). A pack proposes canonical members and their
  spellings; a granted column's distinct landed values decide which exist.
  Matching is exact on a normalised form, never substring, so `MY` binds to
  Malaysia and `Crop Insurance Services` does not bind to agriculture. Members
  the data lacks are disclosed; values that match nothing come back as
  `unmatched_sample` for a steward to add on purpose.
- **Stages.** Sense lease/buy/housing-rent (CCA-02 #134), asset class
  commercial/residential (CCA-03 #135), the eleven Southeast Asian states with
  ISO-2/ISO-3 and alternate spellings (CCA-04 #136), and a 31-member industry
  segment taxonomy reaching plantations, crops, livestock, aquaculture and
  forestry (CCA-08).
- **Orchestrator.** The cascade runs on the ask path before L0 (CCA-05 #137).
  A stage that does not certify stops it: the ask returns ABSTAIN naming the
  missing binding and the engine is never asked. A certified prefix rides along
  on the answer with its coverage sentences in `assumptions`. `grain`,
  `ontology` and `sql` stay absent from the trace rather than claiming a
  certification the cascade did not earn.
- **Surface.** Audit paints CERTIFIED / ABSTAIN / REFUSE per stage with the
  candidate, binding, evidence and blocking reason (CCA-07 #139). An empty
  trace renders "no cascade ran", never an all-clear.
- **Guards against the opposite failure.** A question-side lexicon is now
  separate from the value-side pack: "capacity of warehouse A" abstained
  because a warehouse is both a commercial property type and this product's
  word for a location. Seven of the product's own questions are held to no
  engagement by test. Steward-registered verified queries run before the
  cascade and are not gated.
- **The ask-path hook ships OFF (`DMS_CCA_CASCADE=0`).** A second independent
  run measured the engagement rule in both directions against this product's
  own vocabulary: 46 of 106 ordinary questions engaged the cascade and then
  abstained, refusing answers the product gives today, and 35 of 37 asks that
  plainly name a filter were not recognised, so the stage recorded
  "(no recognised term)" as CERTIFIED and the trace went green over an
  unconstrained filter. Binding a term to landed values is solved; deciding
  from free text whether a question carries a filter is not, and a control that
  refuses 43 pct of ordinary work while silently passing 95 pct of the work it
  exists for makes the product worse in both directions. Everything is built,
  tested and reviewable behind the flag.
- **Polarity fails closed, in both directions.** Any polarity cue anywhere in a
  question stops a stage certifying. There is no carve-out for a result that
  already reads as an exclusion: the eval gate caught that exemption at once,
  because "excluding tax, commercial property revenue" derives
  exclude(Commercial) from a cue belonging to tax. Before this, "residential is
  excluded" certified `IN ('RES')` and "all of SEA other than Singapore"
  certified `country IN ('SG')` - confident answers to the opposite question.
- **Boundary fix.** The cascade read `tables or grantable_tables(...)` where
  `tables` is the request body's `grounded_tables`, unvalidated. The grant check
  caught it on the answering path, but a blocked cascade returns 200 before that
  check and its evidence carries up to twelve distinct values per scanned
  column. The grant now decides what may be opened; the request may narrow it
  and may never widen it, and `grounded_tables` on the envelope reports what was
  actually read.
- **Claim, narrowed after an independent run (R-0003).** The cascade certifies
  that an encoding exists and how the column spells it. It does **not** check
  that the executed query used that spelling, because DMS passes the question
  to Cortex unmodified. So the delivered guarantee is "abstains on a missing
  encoding, and discloses the landed spellings", not "never a confident wrong
  filter". Closing the rest is new work in its own ticket, not a widening of
  this epic. See `docs/subagents_findings/2026-09-05_cca-certifies-spelling-not-sql.md`.
- **Seven defects that run found, all fixed.** A `market` column of US city
  codes certified Laos out of `LA`; negation read backward only and inverted
  "residential excluded, commercial included"; a named-country exclusion was
  dropped and then contradicted; two granted columns produced a predicate
  naming one and listing both; "Not present in this data" was said of a
  spelling the pack merely did not know; a stage with no recognised term
  claimed the ask made no such constraint; and 25 of 36 ordinary domain
  questions engaged the cascade and then abstained. The last one is structural:
  `cca/intent.py` decides per alias whether a term names a filter anywhere or
  only next to a cue, one rule for all four stages.
- **RSF-02 (#140).** Typed research/segment/classify/filter artifact schema in
  `dms_core`, beside the CCA schemas, carrying `chosen_option` and a route
  decision trace. DMS half only; the Cortex consumer types are not in this repo
  and acceptance 3 stays open.

## 2026-09-04 - Studio register of Space-scoped verified Q→SQL (VQ-02)

- **Store.** Steward-certified question→SQL pairs persist in DuckDB
  `main._verified_queries`, keyed by Space. Not a global pack YAML rewrite.
  Hostile SQL and tables outside the Space grant are refused at register.
- **Ask.** `POST /v1/chat/ask` in that Space hits the asset as `L0_CERTIFIED`
  with executed rows. A foreign Space does not inherit the SQL. Cortex pack
  match (VQ-01) is unchanged.
- **Studio.** Register control on the Studio page for the active Space.
  `POST /v1/studio/verified-queries` calls `compliance_gate` before write.
  dms#40.

## 2026-09-03 - Promote receipts persist and are readable (EPIC-024 ticket 1)

- **Store.** Each silver/gold promote writes `main._promote_receipts` on the
  same DuckDB connection as the target (transaction: both commit or the run
  fails). Full `to_dict()` as JSON; `recorded_at` minted in Python UTC. Rejected
  homes: Postgres `dms` (Library must work without `DATABASE_URL`) and a JSON
  file (no transaction against the lake).
- **Read.** `GET /v1/pipelines/receipts?target=` is gated
  (`pipeline.receipts`, `enforce(mutation=False)`). `recorded` vs
  `no_receipt_yet` (never zeros, never a bare 404). Writer-held lake is
  `lake_busy` 503. Scope is named; targets still have no grant model.
- **Honest gap.** Promotes from before this merge were never stored; they
  answer `no_receipt_yet`. Nothing is rendered — tickets 2-4 own the UI.
  dms#113.

## 2026-09-03 - SQL source freshness is one watermark (EPIC-020 ticket 5)

- **One clock.** `extracted_at` is minted in Python at pull time and stored as a
  VARCHAR on the ingest registry (widened, not a sidecar). Receipt, bronze
  preview, Library tree node, and ask `sources[]` show that same string.
- **File vs SQL.** `source_kind` is `sql` when the registry filename is a
  `sqlserver://` or `mysql://` source, else `file`. Library copy says extracted
  vs uploaded. A table with no registry row shows `extracted_at: null`.

## 2026-09-03 - Studio SQL source extract (EPIC-020 ticket 4)

- **`POST /v1/studio/sources/sql`.** Steward posts connection details; F5 runs first
  (`studio.sql_source`, config actor). Rows land in bronze through the existing
  extract-only connector. The receipt names `source`, landed tables, `skipped`,
  and declared key counts - never the password, never `asdict` of the extract.
- **422 cannot echo secrets.** App-level `RequestValidationError` handler drops
  `input` and `ctx`. Three probes (over-long password, list body, int password)
  no longer leak `p;w}d`.

## 2026-09-03 - Studio ingest names the configured actor

- **DR-0004 Option A.** `POST /v1/studio/ingest` (and the other Studio hops)
  now pass `settings.dms_actor_user_id` into F5. They used to send `actor=None`,
  so Cortex recorded the literal default `"user"`. Identity still never comes
  from a request header.
- **F5 key.** `compliance_gate` forwards `X-API-Key` from `CortexClient.api_key`
  when one is set, and still sends none when it is not (fail closed, not spoof).
  Lifespan constructs the client with `settings.cortex_api_key`.

## 2026-09-03 - xlsx-orch paths stay inside the warehouse tree

- **Read-side allowlist.** `POST /v1/studio/xlsx-orch/{crosscheck,extract,golden}`
  used to open any caller-supplied absolute path. They now go through
  `resolve_allowlisted_file` (same roots as REVEAL-01: warehouse parent +
  `DMS_REVEAL_ROOTS`). Outside paths return `path_not_allowlisted` without a
  read. Mutation: the three new tests go red if the guard is skipped.
- **Write-side.** `artifact_dir` no longer keeps `.` in space/pack ids, so
  `space_id=..` cannot walk out of `space_docs`. Store/load also refuse a dest
  that does not resolve under the root.

## 2026-09-03 - Trust ask-path + Share fallback + viz CSV lock

- **Live reconfirm.** Hostile `score_answers` after API restart: precision
  100.00 pct (10/10), coverage 71.43 pct (10/14), 0 WRONG. Curated same
  10/14. Trust `/v1/trust/summary` attaches both packs and keeps Cortex
  `claim.supported` false (eval 404). Cream Ask still has Trust; Operate
  keeps Studio.
- **Share.** `copyText` falls back to `execCommand` when Clipboard API is
  denied. Spaces shows Copied / Copy failed per Space.
- **Viz bakeoff.** Envelope locked to certified sales01 CSV (Electronics /
  Home / Sports). Pointer channel fails closed if that CSV drifts.

## 2026-09-03 - hostile coverage 71 pct + grouped accuracy-check

- **Hostile two waves.** Named-sheet bronze intercept now does exact filters
  (`SKU-BETA` / `Kuala Lumpur` L0; `BETA` / `KL` hard-rule-12 abstain) and
  `TRY_CAST` so VARCHAR ingest still sums. Live Finance pack: wave 71.43 pct
  (10/14) then 64.29 pct (9/14 under lock), serial confirm 71.43 pct.
  Precision-on-answered 100.00 pct, 0 WRONG. RAG / F32 / encoding traps stay
  abstain. Do not start EPIC-022 while EPIC-017 is open.
- **CEO Check accuracy.** Grouped spend no longer treats the first country as
  a grand total. Browser: Match, 4 grouped values = row sum.
- **Trust.** Filters must use stored encoding (SKU-BETA, not BETA).

## 2026-09-03 - EPIC-016 DMS pack cross-check / extract / golden

- **#30 DMS half.** `POST /v1/studio/xlsx-orch/crosscheck` consumes the AirGPT
  D04 pack, schema-checks the source xlsx (OnTime + cost, refuse Summary
  theater), strengthens the pack, returns `awaiting_pointer_receipt`. Does not
  paste into Excel Copilot.
- **#31 DMS half.** `POST /v1/studio/xlsx-orch/extract` stores a Pointer-posted
  result xlsx byte-faithful under `space_docs/`. No path -> named
  `awaiting_pointer_receipt`. MCP/openpyxl producer refused.
- **#32 gate.** `POST /v1/studio/xlsx-orch/golden` asserts avg ~300.27 and
  ~184005/200000 on Analysis/Export, and fails when Export row count disagrees
  (theater trap). Live Copilot workbook still owed by Pointer.
- **Gate:** `tests/test_xlsx_orch.py` (11 passed). Pointer paste -> P-DMS-36.

## 2026-09-03 - bronze sync + Constructor Space routing

- **Hostile live after serving sync.** Cortex was down; `sync_bronze_to_serving.py`
  copied bronze (including hostile sheets) into `E:\Cortex\data\dms_demo.duckdb`.
  `score_answers --space Finance`: precision-on-answered 100.00 pct (6/6),
  coverage 54.55 pct (6/11), 0 WRONG. Five abstains are the traps (RAG, BETA,
  KL, F32 categoty, blank-band ungrantable). Added `sales01_widefill_top3`.
- **Constructor `--ask` routes by grant.** Shipments ask Warehouse Ops, alerts
  stay ungranted. Live: 5/5 grantable objects L0. Does not compile ontology.py.
- **Curated CEO pack.** 10/14 L0 including Ops shipment cost; 4 traps abstain;
  0 WRONG. Trust blurb: suggested asks are the walkthrough; a green typo is a fail.

## 2026-08-28 - F73 three accuracy/surface/delivery agents

- **Genie walkthrough analog.** `scripts/score_curated.py --live`: 9/13 L0
  certified hits, 4 traps abstain, precision-on-answered 100.00 pct, 0 WRONG.
  Constructor `--ask` maps catalog objects to those questions (4/6 L0;
  shipments + alerts abstain). Excel last-mile: `.tmp/curated_spend.xlsx`
  BarClustered matches Finance spend envelope (Malaysia longest).
- **Ask-mode dead links.** Cream Chat no longer points at Studio/Ontology/Audit;
  `ceoSafeHref` sends the CEO to Library or Trust.
- **Constructor ingest plan.** Catalog HTTP -> table list CSV
  (`bronze.constructor_objects`, 6 objects). `--ingest` posts to Studio.
  Does not import or compile `scripts/ontology.py`. Foundry dumps refused.
  Serving sync still locked while Cortex holds DuckDB.
- **CEO Library ground.** Ask mode has no Studio. Library preview now has
  **Ask about this table** (same Chat state as Studio). Cream hides Studio
  ingest links. Empty-state asks include live-curated spend-by-country.
- **Grounded Excel scope.** `live_ask` prefixes `Using only <bronze table>:`
  because contract AskRequest has no tables field. Manifest still refuses
  `FROM transactions`. Hostile score now grounds each workbook; 403
  `grounding_not_grantable` scores as abstain, not WRONG.
- **E12 over-abstain.** `total spend by country` is a grouped ask. `_PER_GROUP_ASK`
  now includes `by <token>` so E12 does not demote certified `GROUP BY` queries
  that match the question. Live: Finance spend `L0_CERTIFIED` 4 rows; Ops still
  abstains (`suppliers` not in manifest). Stock-by-category answers in both
  Spaces. `pytest` mapping/live/envelope/space-boundary: 66 passed.
- **Hostile live.** After uvicorn restart (stale process had no xlsx demote):
  `score_answers` PASS 0 WRONG, coverage 0/11. BETA `.xlsx` ask abstains instead
  of shipping 80M demo outbound revenue.
- **F73.** Founder `/goal` + `/create-subagent` routed to existing Wave 7
  (017/018/019), EPIC-016 last-mile, EPIC-003 mock honesty. No new epic.
  Project agents: `.cursor/agents/dms-accuracy.md`, `dms-surface.md`,
  `dms-delivery.md`.
- **F40 repro honesty.** `scripts/repro_refused_badge.py` LINK 2 now uses
  `map_ask_response_to_envelope` (the ask path). The old P0 used
  `build_answer_envelope` with no route and inverted abstain logic.
- **Product modes.** Cream = Ask (CEO nav: Chat, Spaces, Library, Trust;
  Claude-white). Graphite = Operate (full appliance). Dead search and
  `aminah@` stub removed. CEO empty-state asks include a typo trap.
- **Constructor source.** `scripts/constructor_source.py` stages GET
  `/cortex/constructor/ontology` (fixture when Cortex is down). Foundry
  CLI dumps refused. Does not import CortexOS or compile `ontology.py`.
- **Browser.** Ask mode: CEO nav (C/Sp/L/T) + "Ask your company's data".
  Operate mode: Studio/Ontology/Amend/Audit/Runs + role switcher.
- **Excel last mile.** `.tmp/viz_envelope.xlsx` BarClustered `Top3` from
  Sales oracle Electronics=1545366.4 / Home=1199018.49 / Misc=380948.33.
## 2026-08-28 - Playwright chrome/chat e2e

- **#102.** Chrome, Chat, Spaces/Studio/Amend smoke against a local demo
  stack (API `:8090` `DMS_ASK_MODE=demo` + UI `:3000`). 10/10 in this VM.
  Product abort/gate copy already landed in #99; this is the suite only.

## 2026-08-28 - E12 scalar ask vs ranking

- **E12 / ANS-02.** Live ask "What is total inventory quantity?" returned a
  10-row category ranking under `L2_VALIDATED` (stored query skill). Inverse of
  E10: a one-number ask with `GROUP BY` and 2+ rows demotes. A true one-row
  SUM stays certified (R-0005). Folded from #101 without its venv/tmp junk.
  HTTP `POST /v1/chat/ask` asserted. `INVARIANT-CHANGE` in envelope tests.

## 2026-08-27 - vendor Cortex OpenAPI 1.2.0 pin

- Copied `contract/openapi-1.2.0.json` + `.sha256` from Cortex origin/main
  via `scripts/sync_contract.py`. DMS does not author the spec. Pin
  `08efc36d84f976e1255ae33c4f19e563f50d52835833fab53cdb1837258bdb1b`.
  Pruned generation surface is unchanged (same 6 paths / 20 schemas), so
  `cortex_client.generated` was not regenerated.
- **#59 FF-03.** Cortex `SqlGateAbstain.__str__` on origin/main interpolates
  `violations` into the abstain reason (`L2 generation failed validation
  gate: {exc}`). DMS tests never asserted the old bare "exhausted retries"
  string; no test update required.

## 2026-08-26 - E9-02 ungrounded Wide_Fill + health abort

- **E9-02.** An ungrounded ask used DEMO_TABLES as `grounded_tables`, so F32
  demote never saw Sales vs Wide_Fill and a green ranking could ship. Executed
  SQL that names `stem_Sales` / `stem_Wide_Fill` now infers the sibling pair.
  Bare `FROM sales` is not a workbook pair. HTTP `POST /v1/chat/ask` asserts
  badge/text/rows without a client `grounded_tables` plant (rule 10/10a).
  `INVARIANT-CHANGE` in envelope tests. EPIC-018 stays queued.
- **UI health poll.** React StrictMode aborted the first `/api/health` fetch
  and the catch painted API offline over a live stack. Abort is not down.
  `gate_unavailable` copy names starting Cortex; mutations still fail closed.

## 2026-08-22 - FF-02 polarity guard (E11)

- **#57 FF-02.** A governed metric answered "warehouses that are not cold
  storage" with `SELECT COUNT(*) ... WHERE is_cold_storage = TRUE` (4 vs
  oracle 102,986) under `L1_GOVERNED_METRIC`. Ask and answer were both
  scalar, so E10 did not fire. Envelope now demotes when a closed-list
  negation (`not`, `non-`, `excluding`, `other than`) overlaps a filter
  that asserts the positive. Same class as E10: Cortex still matches;
  DMS refuses the governed badge. Positive "How many cold storage
  locations do we have?" still returns 4 under L1 (R-0005).
  `INVARIANT-CHANGE` in envelope tests.

## 2026-08-22 - S4 warehouse identity

- **Two DuckDB files were the remaining S4 gap** (TAS-DMS §6, measured
  2026-08-02). Studio ingest writes `DMS_WAREHOUSE_DB` (`data/dms_demo.duckdb`).
  Cortex answers from `CORTEX_HOME/data/dms_demo.duckdb`. An uploaded sheet was
  unreachable from `POST /v1/chat/ask` — a silent miss. dms#4/#5 (receipt
  honesty, grounding) stay closed; this is the warehouse-identity leftover.
- **Fix is an explicit bronze copy, not one file.** Demo seed uses
  `txn_type='outbound'`; the engine file uses `'OUT'`. Pointing ingest at the
  engine warehouse reseeds and drops its extra tables. `sync_bronze_to_serving`
  copies bronze user tables only.
- **Regression:** `tests/test_warehouse_identity.py` fails if an xlsx lands in
  ingest and serving cannot see it; `--check` exits 1 on that diverge.
- **Demo step:** `python scripts/sync_bronze_to_serving.py` (Start-DMSStack
  runs it before Cortex starts, while the serving file is unlocked).

## 2026-08-22 - extract-lab follow-up (pyarrow, migrate, AW_IMAGE)

- **pyarrow** is a declared runtime dep. `scripts/load_adventureworks.py --extract`
  writes Parquet via `pandas.DataFrame.to_parquet` and died mid-run when the
  extra was missing.
- **alembic upgrade head** runs on postgres bootstrap: API image entrypoint,
  API lifespan (already did), and `Start-DMSStack.ps1` after host-bound
  postgres is up. already-at-head is success. Fresh compose-postgres then has
  `dms.spaces` without a manual migrate.
- **AW_IMAGE** defaults to `mcr.microsoft.com/mssql/server:2025-latest`. A 2022
  tag cannot restore the shipped AdventureWorks*2025 backups (version 998).
  Script help, README, and a pre-RESTORE image/engine check say so.

## 2026-08-22 - CSV-01 deterministic download

- **CSV-01 (#18).** Download CSV is a pure serializer: UTF-8 BOM, RFC 4180
  quoting, CRLF, first-seen column union, raw JSON numbers (no `en-MY`
  thousands separators). Filename is `dms_answer_<answer_id>.csv` — no clock.
  No model on the path. Summary one-cell answers still fetch drill rows first.

## 2026-08-06 - working-tree recovery, SCORE-03, demo 31/31

- **Composition root recovered.** `apps/api/dms_api/wiring.py` had been
  truncated to zero bytes. It is the only module allowed to import
  `dms_executor` (`.importlinter`), so every route reaching the executor died at
  import and seven test modules failed to collect. Restored and extended with
  `reveal_origin_uri`, `search_document_chunks`, `list_document_chunks`;
  `warehouse_tables` now takes `space_id`. Chunk search resolves the Space
  filter in SQL, not by post-filtering rows.
- **RAG-01/02/04/05 + REVEAL-01 landed** with the `0003_document_chunks`
  migration, the L2 bakeoff record, the demo runbook, and the playground bank.
- **SCORE-03 (#42).** `f32_ambiguous_categoty_top3` — no sheet named, "categoty"
  left misspelled; fixture carries Sales truth (Electronics 1,545,366.40 / Home
  1,199,018.49 / Misc 380,948.33) against the Wide_Fill ranking the live stack
  returned under green (Home 383,803.56 / Sports 242,755.97 / Misc 228,548.84) —
  wrong rank *and* wrong magnitude. Plus `blank_hanging_rows_top3`: messy sheet
  must equal `Sales_Clean`. Both traps self-check inside `score_answers` and
  exit 1 rather than report green having tested nothing. Falsified per R-0007.
- **Two silent skips removed (R-0002).** `test_resolve_oracle_on_shipped_hostile_fixtures`
  returned early when the fixture dir was absent; `test_playground_pack`
  asserted ids and keys the bank never had, so it was testing nothing that
  existed. Now asserts ladder coverage L0-L5 and unique ids.
- **Demo verified live: 31/31** (`verify_demo_live.py`), twice consecutively.
- **#43 DEMO-COLD-01 filed.** The *first* run against a cold stack refused the
  freshly uploaded file as `grounding_not_grantable`; warm re-runs pass. Demo's
  own happy path. Candidate mechanism is the warehouse-read swallow at
  `demo_grants.py:90-98` turning an unreadable warehouse into a permission
  decision — unconfirmed, and not fixed here (unrouted product change).

## 2026-08-05 - Wave 7 land + hard-rule-12 + EPIC-019 start

- **E9-02 (#41 open — verify pending).** Ambiguous multi-sheet category ranking
  demotes at `build_answer_envelope` when competing Sales vs Wide_Fill (or
  cross-file sales) scopes disagree and the ask is not uniquely scoped.
  Uniquely scoped / single-grounded executed ranks still certify. F32 fixtures
  in `tests/invariants/test_envelope.py`.
- **E9-01 (#34 CLOSED).** Invent-totals demote on ask map path; CLAUDE rule 10a
  is E1-E9. Empty executed SQL result demotes to ABSTAIN (hard rule 12 —
  BETA/SKU-BETA empty-filter green). `INVARIANT-CHANGE` in envelope tests.
- **SCORE-01/02 (#36/#37 CLOSED).** Hostile 9-case pack +
  `tests/fixtures/hostile_score` + `--help` / DEMO_RUNBOOK live-stack recipe.
- **Cortex value-norm.** `city` in `VALUE_COLUMNS`; KL→Kuala Lumpur acronym
  ladder; literal_normalize unique cross-column resolve when hint missing.
- **VQ-01 start.** Certified assets accept curated `synonyms:` (exact normalize
  only). EPIC-019 filed #38; children #39/#40.
- **Playground.** `playground/` sample data + 13 mutable questions +
  `scripts/playground_ask.py`. L4/L5 are aspiration labels only (P-DMS-33).


## 2026-08-02 - demo-eve P0 sweep

- **P0-DEMO-01 (#4) fixed.** The first `.xlsx` into a fresh warehouse reported
  `ingested=0, parse_error:... _ingest_registry does not exist` while the rows
  were already in bronze. The registry is created before any path that renames a
  table into place, and the swap plus the registry write are one transaction, so
  no failure after the rename can produce a receipt the warehouse contradicts.
  Row counts are read back from the created table. Fixture 15 is the first
  non-CSV ingest fixture; all 14 before it were CSV, which is why this shipped.
- **ACL-01 (#2) fixed.** `live_ask` minted from `demo_acl()`, which allowlisted
  every demo table regardless of `space_id`. The DR-0002 grant split is now
  seeded in `dms_executor.demo_grants` behind the `SessionStore` port, so the
  boundary holds without Postgres. Wiring it exposed a second leak: the bound
  session id varied by grounding scope but not by Space, so switching Space in
  one chat was served under whichever manifest bound first.
- **P0-DEMO-03 (#5) fixed.** Grounding on an uploaded table widened the manifest
  to all six demo tables while the UI read "Grounded in 1 file". Uploads are
  grantable from the ingest registry, and a selection that cannot be granted is
  refused by name rather than dropped. The envelope now carries
  `grounded_tables`, so the count a viewer reads comes from the minted manifest.
- **Demo Spaces renamed** to DR-0002's `Finance` and `Warehouse Ops`.
- **Space refusals render as answers**, not raw `path_not_allowed` 403s.
- **`Start-DMSStack.ps1` binds Postgres to the host** via the hostdb overlay;
  without it the container was healthy and unreachable, the API fell back to the
  in-process Space store, and 18 control-plane tests errored instead of running.
- **CI-02 (#3)**: workflow now reads `CORTEX_CONTRACT_TOKEN`. Still 404 - the
  token itself cannot see `Netie-AI/Cortex`.

Verified live (`scripts/verify_demo_live.py`): 18/18 against DMS + Cortex +
OpenVault. Full corpus 188 passed.
