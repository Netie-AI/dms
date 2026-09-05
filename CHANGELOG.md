# CHANGELOG

Append-only. Never edited, only added to. Newest first.

## 2026-09-05 - Verified-query L0 requires Cortex submit + ledger (F83 / EPIC-019)

- **Hole.** Studio-registered hits executed SQL in local DuckDB and stamped
  `L0_CERTIFIED` with `cortex.asks == []`. Engine certification was forged.
- **Fix.** A Space hit still skips the CCA cascade (steward already decided),
  then `live_ask` submits the stored SQL through existing Cortex `submit_sql`
  and appends `ask.verified_query` to the Cortex ledger. L0 is minted only
  when submit returns SQL output and the ledger returns a real hash distinct
  from `entry_id`. Missing submit/ledger does not fall back to local execute.
- **Tests.** FakeCortex: in-space L0 asserts sql submit + ledger; bind-only
  submit and hash==entry_id fall through to ask. No product regex for F32.
  dms#38 residual. Live Cortex still required to close the epic.

## 2026-09-05 - MCP-01 tools wrap existing HTTP (EPIC-014, flag off)

- **MCP-01 (#20).** `GET /v1/mcp/tools` and `POST /v1/mcp/call` expose `ask`,
  `preview`, and `list_metrics` by calling the same handlers as
  `POST /v1/chat/ask`, warehouse preview, and `GET /v1/ontology/metrics`.
- **Flag.** `DMS_MCP=0` by default: the router is not mounted. Swap: IDE MCP
  client. No new serving engine, no CortexOS import, no cortex-contract bump
  (`McpCallIn` name+arguments already exists).
- **Gate.** `POST /v1/mcp/call` calls `compliance_gate`; preview still refuses
  ungranted tables. Tests assert badge/values/rows match the HTTP path.

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
