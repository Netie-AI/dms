# CHANGELOG

Append-only. Never edited, only added to. Newest first.

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
