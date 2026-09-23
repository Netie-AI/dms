# Active map

What exists in this repo and where. Update when structure changes, not when state changes
- state lives in STATUS.md.

S4 warehouse identity: `packages/executor/dms_executor/warehouse_identity.py`
copies Studio bronze from `DMS_WAREHOUSE_DB` into `CORTEX_WAREHOUSE_DB`.
CLI + check: `scripts/sync_bronze_to_serving.py`. Regression:
`tests/test_warehouse_identity.py`.

EPIC-016 xlsx orch (DMS half): `packages/core/dms_core/xlsx_orch.py` (pack
cross-check + FRTR golden), `packages/executor/dms_executor/xlsx_orch.py`
(read-only openpyxl + space_docs store). HTTP:
`POST /v1/studio/xlsx-orch/crosscheck|extract|golden`.
Regression: `tests/test_xlsx_orch.py`. Pointer owns Copilot paste (P-DMS-36).

INSIGHTS-EXPORT-01 (#188): `packages/core/dms_core/xlsx_export.py` copies an
existing ask envelope into .xlsx (stdlib OOXML in `xlsx_ooxml.py`). HTTP:
`POST /v1/chat/export.xlsx`. Chat: Download Excel. Refuses without
answer_id+badge; does not re-ask or add rows. Not FRTR / #29. Regression:
`tests/test_insights_export.py`. Not COMPLETE.

INSIGHTS-EXPORT-02 (#189): `packages/core/dms_core/bi_export.py` copies an
existing ask envelope into Power Query M and a Superset dataset JSON (URI
omitted). HTTP: `POST /v1/chat/export.bi`. Chat: Power BI / Superset.
Refuses without answer_id+badge; does not re-ask, invent metrics, or stamp
COMPLETE. Live ODBC / Desktop / steward Superset = NEEDS-YOU. Regression:
`tests/test_insights_export_bi.py`. Not COMPLETE. Does not reopen #108.

INSIGHTS-HOST-01 (#196): hosted consume of Cortex `GET|POST /v1/insights`
(Cortex #213). Client: `packages/cortex_client/cortex_client/insights.py`.
HTTP: `apps/api/dms_api/routes/insights.py` forwards
`settings.cortex_api_key` (OV-custodied founder key on prove/studio). Fail
closed if Cortex or (generate) OpenVault is down. No LIVE_KEY invent;
`live_5000_ci` always false. Regression: `tests/test_insights_host.py`.
Live hosted walk = Platform after merge. Not COMPLETE.

SCALE-FREE-AI-01 (#233): FreeRoute free+normal providers resolve through
OpenVault API only (`dms_core.freeroute`, `GET /v1/freeroute/providers`).
Duplicate labels skipped. No chat-token discovery, no local vault scrape,
no second vault. WRONG=0 unchanged. Harness:
`python scripts/bakeoff_freeroute.py --self-check`. Docs:
`docs/FREEROUTE_PROVIDERS.md`. Regression: `tests/test_scale_free_ai.py`.
Live catalog leftover Platform. Not #178 COMPLETE.

EPIC-014 MCP-01: `apps/api/dms_api/routes/mcp.py` wraps existing
`POST /v1/chat/ask`, `GET /v1/library/warehouse/{table}/preview`,
`GET /v1/ontology/metrics`. Flag `DMS_MCP=0` (off). Regression:
`tests/test_mcp_tools.py`. Not a new serving engine.

EPIC-024 LINEAGE-01: promote receipts live in DuckDB `main._promote_receipts`
(same lake file as the target; `_` prefix hides them from Library listings).
Written at the end of `_run_silver` / `_run_gold` on the promote connection.
Read: `GET /v1/pipelines/receipts?target=` (gated, read posture).

EPIC-019 VQ-02: steward-registered Q→SQL assets live in DuckDB
`main._verified_queries` (underscore prefix). Write
`POST /v1/studio/verified-queries`, list `GET /v1/studio/verified-queries?space_id=`
(required). `live_ask` hits the Space's assets before Cortex pack match.
Studio register control: `apps/ui/src/pages/StudioPage.tsx`.
Regression: `tests/test_vq02_verified_register.py`.

EPIC-020 leftover pack + VQ-03 (#170) certified exact-matches in
`packages/executor/dms_executor/demo_pack.py` (spend/stock/total_spend plus
the seven live L0 gaps: capacity utilisation, low-stock WH-A, shipment
cost, cold storage, capacity>90, expired, CCTV WH-A). Asks go to Cortex
submit. Thin seed SCHEMA_VERSION 5 adds `location_code` /
`is_cold_storage` / `cctv_camera_id` / `expiry_date` / `last_audit_date` so pack SQL can run
in tests. The founder rich lake is Cortex `/var/cortex/data/dms_demo.duckdb`.
`live_ask` order:
follow-up → VQ-02 → pack → uncertified planted refuse (VQ-04) → cascade → bronze sheet → GEN-01 ontology compile+validate → Cortex. DR-0002
follow-ups (`average of them`, `add N`) in `session_followup.py`. F32
derived path skips demo-lake SQL so cq_spend_by_country is not a
sheet-scope conflict. Regression: `tests/test_demo_pack_followup.py`,
`tests/invariants/test_envelope.py` (lake-SQL skip),
`tests/test_demo_warehouse_reseed.py`. Not #116 COMPLETE.

EPIC-GEN-01 GEN-01 (#179): miss-path generative ask in
`packages/executor/dms_executor/generative_ask.py`. Semantic retrieve
(`semantic_retrieve.py`: schema SQL-filter + ontology + encodings → short
context) then Cortex `POST /dms/query`; `Ontology.compile` emits SQL;
validate then Cortex submit. Unsure or validate-fail is ABSTAIN. A/B vs
exact-match: `python scripts/score_curated.py --ab`. Not pack expansion.
Regression: `tests/test_gen01_generative_ask.py`. Not COMPLETE.

EPIC-GEN-01 GEN-02 (#180): live coverage climb + isolated A/B harness.
`python scripts/score_curated.py --climb --ab --url https://studio.netie.ai/api`
(`scripts/score_climb.md`). Probe + ask use httpx (`score_http`), not urllib
(SCORE-CLIENT-01 / #187; urllib CF1010s the public origin). `ask_path=exact|generative|product` on
`POST /v1/chat/ask`. GEN-03 (#194): `exact|generative` return 400
`ask_path_not_allowed` unless the server sets `DMS_HARNESS_ASK_PATHS`; no lane
POSTs Cortex `/dms/query` or binds a keyword plan, so isolated gen abstains
past the pre-gates and the product path goes to the Cortex contract ask
(`tests/test_gen03_contain_ask_path.py`). Bar (2) KEEP_HOLD on that ticket:
predict / revenue-2099 must not L2 all-time pad; not-cold must not invert
under a confident badge. Isolated gen (offline only): Cortex
compute miss may still bind retrieved ontology then validate/CRAG. Product path
still Cortex-asks on compute miss. Distill ladder in `scripts/score_climb.md` (certified-first,
YAML spine pack `ontology_spine.yaml` for retrieve, hybrid_fuse + CRAG,
Cortex text2sql -- no vendor SDK). Typed lake filters on isolated gen.
Baseline A/B @ `a9578348` exact 10/26 gen 1/26 WRONG=0.
Platform prove @ `186f7d85`: ontology_plan=9 bind_plan=0 WRONG=0.
K-scale @ `58d27a81`: ontology_plan=11 bind_plan=0 WRONG=0 (RISE_PASS).
Continue climb: GEN-PATH-CLIMB-02 (#208). Not a GitHub CI live job.
Regression: `tests/test_score_climb.py`,
`tests/test_gen01_generative_ask.py`, `tests/test_gen02_bind_fallback.py`,
`tests/test_gen_path_climb.py`, `tests/test_gen02_kscale.py`,
`tests/test_gen_path_climb02.py`.
Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-PROVE-01 (#199): independent `plan_source` label on gen envelopes.
`bind_plan` stamps `bind_plan`; Cortex Insights generate / typed `/dms/query`
stamps `ontology_plan`. Harness
`python scripts/score_curated.py --prove-path` (offline) or
`--prove-path --url https://studio.netie.ai/api` (Platform live) reports
per-qid `ontology_plan|bind_plan|other` from the envelope field only
(no SQL/assumption guess), count/%, WRONG=0, and
`Phase A HOLD may clear` YES/NO. Does not stamp COMPLETE. Live numbers
= Platform. Regression: `tests/test_gen_path_prove.py`. Trust read-only
pickup: `GET /v1/trust/summary` `gen_path_prove`.

GEN-PATH-ROUTE-01 (#201): Studio/prove generative compute calls Cortex
`POST /v1/insights` `generate=true ask=false` then typed `POST /dms/query`.
Offline `--prove-path` stays bind_plan. Not Phase A CLEAR.

GEN-PATH-ROUTE-02 (#203): #201 leftover was isolated-gen `bind_on_miss`
over Insights REFUSE/401 (unarmed / not `ov_`), so prove stayed
`ontology_plan=0` / `bind_plan=15`. Insights reached is not a bind;
401 then `GET /v1/insights/ontology`; top Cortex metric id on the DMS
ontology compiles as `ontology_plan`. Harness counts `ontology_plan>=1`
when that path works. Platform:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`
(timeout 120s). Live counts Platform. Phase A HOLD CLEARED thin @
`e1f729a5` (ontology_plan=1 / bind_plan=0 / WRONG=0). Epic NOT COMPLETE.

GEN-PATH-CLIMB-01 (#205): raise measured ontology_plan above 1 with
WRONG=0. Ranking pack ids resolve onto DMS measures (same-intent alias,
not skip-to-weaker). Retrieve slots overlay group/filter/limit.
`intent_slots` on Insights generate. Invalid SELECT may ranking-climb;
hostile SQL abstains. Regression: `tests/test_gen_path_climb.py`.
Platform prove @ `186f7d85`: ontology_plan=9 bind_plan=0 WRONG=0.
Not COMPLETE. Epic NOT COMPLETE.

GEN-PATH-CLIMB-02 (#208): continue live ontology_plan rise beyond 11.
Prefer-locked ranking walk past sku_count noise on SKU-list asks;
FreeRoute retry uses walked/resolved slots; `sales_top` pack-id overlay.
Regression: `tests/test_gen_path_climb02.py`. Platform leftover after
deploy: `python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>11 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-03 (#210): continue live ontology_plan rise beyond 13.
When prefer-locked ranking walk exhausts (YAML ranking never
includes the pack id), overlay a question-matched Cortex pack id onto
the prefer lock. Remaining pack-id shapes (cold/expired/chemicals/cctv/
supplier_rank/low_stock). Honest `supplier_rank_score` measure (risk+lead
formula), not sku_count. Planted refuses stay ABSTAIN. Regression:
`tests/test_gen_path_climb03.py`. Platform leftover after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>13 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-04 (#212): continue live ontology_plan rise beyond 17.
Leftover Cortex certified `cq_audit_overdue` (not PACK_METRICS). Honest
`audit_overdue` measure (90-day last_audit FILTER). Exhausted-ranking
overlay + FreeRoute `free+normal` retry. Ops without suppliers ABSTAIN.
Planted refuses stay ABSTAIN. Regression: `tests/test_gen_path_climb04.py`.
Platform leftover after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>17 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-05 (#214): break flat ontology_plan=17 after #212 KEEP_HOLD
(answered=17/26). Root cause: the 17 L0s were saturated; leftover
`cq_audit_overdue` never entered the frozen 26-pack denominator. Score Cortex
certified synonyms of those 17 (not PACK_METRICS). Retrieve lock
categories+sales. Harness fails if leftover L0s are not asked. Planted refuses
stay ABSTAIN. Regression: `tests/test_gen_path_climb05.py`. Platform leftover
after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>17 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-06 (#216): dual KEEP_HOLD after #212+#214 both stamped
ontology_plan=17 answered=17/26. Root cause: `--prove-path --url`
loads local `questions.yaml` (`score_pack_live`); Studio SHA does not
include the pack. Frozen 26 saturates at 17 L0s; remaining 9 are planted
traps. #214 fail-closed only required leftover ids *asked*, not
ontology_plan. Harness unions rise L0s from the script; live prove FAILs
on flat 17. `/health` `gen_path_climb` advertises pack identity.
Regression: `tests/test_gen_path_climb06.py`. Platform leftover after
deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>17 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-07 (#218): continue live ontology_plan rise past 21 after
#216 RISE_PASS (21/31 WRONG=0 on uncapped harness). Unused Cortex
certified synonyms of L0s live already answers (`Top 5 selling SKUs by
sales`, `show top 3 category sales`, `top 3 category sales`). Retrieve
lock `category sales`. Harness unions the new L0s; live prove FAILs
ontology_plan<=21. Frozen n=26 stays a floor. Planted refuses stay
ABSTAIN. Regression: `tests/test_gen_path_climb07.py`. Platform leftover
after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>21 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-08 (#220): continue live ontology_plan rise past 24 after
#218 RISE_PASS (24/34 WRONG=0 on uncapped harness). Unused Cortex leftover:
`top 3 categoty sales` plus Ops sku-count / sku-count-by-category (granted
inventory+locations, not PACK_METRICS). Retrieve aliases certified
`categoty` to `category`. Harness unions the new L0s; live prove FAILs
ontology_plan<=24. Frozen n=26 stays a floor. Planted refuses stay
ABSTAIN. Regression: `tests/test_gen_path_climb08.py`. Platform leftover
after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>24 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-09 (#222): continue live ontology_plan rise past 27 after
#220 RISE_PASS (27/37 WRONG=0 on uncapped harness). Unused Cortex leftover:
Ops sku-count synonyms (`How many SKUs in inventory?`, `SKU count in
inventory`) plus Ops `List chemicals in inventory` (granted inventory,
not PACK_METRICS). Retrieve/overlay lock chemicals onto stock_value_myr.
Harness unions the new L0s; live prove FAILs ontology_plan<=27. Frozen
n=26 stays a floor. Planted refuses stay ABSTAIN. Regression:
`tests/test_gen_path_climb09.py`. Platform leftover after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>27 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-10 (#224): continue live ontology_plan rise past 30 after
#222 RISE_PASS (30/40 WRONG=0 on uncapped harness). Unused Cortex leftover:
Ops expired / cold storage / capacity>90 (granted inventory+locations,
same parent SQL as Finance L0s, not PACK_METRICS expansion). Harness
unions the new L0s; live prove FAILs ontology_plan<=30. Frozen n=26 stays
a floor. Planted refuses stay ABSTAIN. Regression:
`tests/test_gen_path_climb10.py`. Platform leftover after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>30 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-11 (#226): continue live ontology_plan rise past 33 after
#224 RISE_PASS (33/43 WRONG=0 on uncapped harness). Unused Cortex leftover:
Ops capacity utilisation / CCTV WH-A / low-stock WH-A (granted
inventory+locations, same parent SQL as Finance L0s, not PACK_METRICS
expansion). Harness unions the new L0s; live prove FAILs ontology_plan<=33.
Frozen n=26 stays a floor. Planted refuses stay ABSTAIN. Regression:
`tests/test_gen_path_climb11.py`. Platform leftover after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>33 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

GEN-PATH-CLIMB-12 (#228): continue live ontology_plan rise past 36 after
#226 RISE_PASS (36/46 WRONG=0 on uncapped harness). Unused leftover:
Ops parent-SQL of certified L0s via ontology spine slots (`SKU count by
category`, `stock value by category`, `shipment cost by destination`).
Same SQL as cq_sku_count_by_category / cq_stock_value_by_category /
cq_cost_by_destination. Not PACK_METRICS. Not golden TARGET paraphrases.
Harness unions the new L0s; live prove FAILs ontology_plan<=36. Frozen
n=26 stays a floor. Planted refuses stay ABSTAIN. Regression:
`tests/test_gen_path_climb12.py`. Platform leftover after deploy:
`python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api`.
Need ontology_plan>36 + WRONG=0. Not COMPLETE. #178 NOT COMPLETE.

ONTOLOGY-AUDIT-01 (#235): `audit_receipt` on every ask envelope
(`packages/executor/dms_executor/envelope.py`). Include = executed rows;
exclude = SQL WHERE/HAVING/FILTER or N/A with why; unsure = ABSTAIN or none.
Invented totals / silent zero-pad demote (E13). Chat shows the three why
lines. Regression: `tests/test_ontology_audit.py`,
`tests/invariants/test_envelope.py` E13. Platform leftover: steward inspect
of >=3 Studio asks after deploy. Not COMPLETE. #178 NOT COMPLETE.

ONTOLOGY-COMPILE-01 (#234): ranked where-paths + importance when an ask
spans >=2 supply-chain grains (sku/supplier/plant/lane/day).
`Ontology.compile_grains` / `try_compile_multi_grain` locate then compile,
or Refusal `missing_join` / `missing_metric` naming the gap. bind_plan is
not the confident path. Plant aliases to location until #232. Day without
a calendar object abstains. Regression: `tests/test_ontology_compile.py`.
CI != Platform live. Not COMPLETE. #178 NOT COMPLETE.

ONTOLOGY-MULTIGRAIN-01 (#249): `maybe_generative_ask` prefers
`try_compile_multi_grain` **before** one-grain GEN-01 plan/SQL. Rebased
onto SC-ONTOLOGY-01 #232 @ `936d810`. Live ranking that fills a sku-only
`ontology_plan` no longer drops the second grain. Envelope carries
`where_paths`; assumptions stamp `ontology_compile:where+importance` when
that path wins. Day still ABSTAIN `missing_join`. bind_plan stays
non-confident. Coverage from #232 still stamps include/exclude/unsure.
Regression: `tests/test_ontology_compile.py` KEEP_HOLD cases. CI !=
Platform live. Not COMPLETE. #178 NOT COMPLETE.

ONTOLOGY-MULTIGRAIN-02 (#254): grant-aware multi-grain compile. SKU+plant
either emits a Space-granted plant join path or ABSTAINS `missing_join`
naming `plant` -- never bare `validate:ungranted:shipments`. Live leftover
after #249: Finance shipping-cost cited ungranted `shipments`. where_paths
/ WRONG=0 / #238 refuse stand. Regression:
`tests/test_ontology_compile.py` mg_sku_plant. CI != Platform live re-prove.
Bar (1) KEEP_HOLD. Rebased onto GEN-03 #194 @ `9b29c565`. Not COMPLETE.
#178 NOT COMPLETE.

AGI-BUYER-WALK-01 (#236): steward Studio buyer walk for the 5-day
supply-chain AGI-for-DB demo. Pack
`tests/fixtures/buyer_walk/questions.yaml`. Script
`python scripts/walk_buyer_studio.py` (self-check in CI; live needs
`STUDIO_ORIGIN` + `DMS_API_BASE`). Studio copy on `StudioPage` lists
five asks + one refuse/ABSTAIN why. Chat prefill via `draftQuestion`.
Artifacts = receipt / ask envelope / export.xlsx. No invented charts,
logos, ARR, or COMPLETE. Live leftover is Platform (post report on
#178). Regression: `tests/test_walk_buyer_studio.py`. Not #178 COMPLETE.

GEN-PATH-REFUSE-01 (#238): fail-closed ABSTAIN with a named gap when
Cortex ranks a metric the verified ontology cannot compile, or
`Ontology.compile` returns `unknown_measure` / `no_path`. Customer
text carries `gap:`. Does not bind_plan a nearby measure. Product
lane does not fall through to Cortex.ask on that miss. Transport
miss + known measure still binds on isolated gen (GEN-02). Not
GEN-03 `ask_path` 400. Regression: `tests/test_gen_path_refuse.py`.
Not COMPLETE. #178 NOT COMPLETE.

SC-ONTOLOGY-01 (#232): named supply-chain grains `sku` / `supplier` /
`plant` / `lane` / `day` on `demo_ontology` (`grain_aliases` + `day` object
when `transactions.ts` exists; `lane` only when origin+destination columns
exist). Join importance ranks paths from a measure grain; missing metric/join
is `missing_join` / `unknown_measure`, never a pad. Compiled numbers carry
`coverage` include/exclude/unsure (`NO_SILENT_PAD`). Ask path stamps that on
ontology_plan envelopes or ABSTAINS naming the gap. Spine `grains:` slot map.
Regression: `tests/test_sc_ontology.py`. Not COMPLETE. Not 1PB LIVE.

EPIC-020 SQLSRC-09 / SQLSRC-PG-01: Studio SQL Server/MySQL/PostgreSQL form posts
`POST /v1/studio/sources/sql` (`apps/ui/src/components/SqlSourcePanel.tsx`).
Receipt copy: `apps/ui/src/lib/sqlSourceReceipt.ts`. Does not wire ask/chat
to the ontology. #116 live certify is separate.

SCORE-BIRD-01 (#184): `scripts/score_bird.py` + `scripts/score_bird.md`.
Live A/B on Space `f0da7dd3-...` (source_count=1). Bronze grows in batches
past baseline `gender`. Leftover target 75. Not EPIC-020b COMPLETE.

EPIC-CCA constraint cascade: `packages/executor/dms_executor/cca/`. One
matching rule in `binder.py` (pack proposes, landed values decide, exact match
on a normalised form). Stage binders `sense.py`, `asset_class.py`, `geo.py`,
`segment.py`; each carries a `QUESTION_ALIASES`-style question lexicon that is
deliberately narrower than its value pack. `cascade.py` runs them on the ask
path before L0 from `Executor.live_ask`, after the verified-query hook and
before bronze-sheet and Cortex. The trace shape is CCA-01
(`constraint_cascade.py`); the envelope carries it as `constraint_trace`.
Regression: `tests/test_cca_*.py`. Surface: `apps/ui/src/lib/constraintTrace.ts`
plus `components/ConstraintTracePanel.tsx` on AuditPage.

RSF-02 typed artifacts: `packages/core/dms_core/rsf.py` (beside CCA, not inside
it; dms_core may not import dms_executor). Regression:
`tests/test_rsf_artifact.py`.
