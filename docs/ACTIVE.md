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

INSIGHTS-HOST-01 (#196): hosted consume of Cortex `GET|POST /v1/insights`
(Cortex #213). Client: `packages/cortex_client/cortex_client/insights.py`.
HTTP: `apps/api/dms_api/routes/insights.py` forwards
`settings.cortex_api_key` (OV-custodied founder key on prove/studio). Fail
closed if Cortex or (generate) OpenVault is down. No LIVE_KEY invent;
`live_5000_ci` always false. Regression: `tests/test_insights_host.py`.
Live hosted walk = Platform after merge. Not COMPLETE.

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
submit. Thin seed SCHEMA_VERSION 4 adds `location_code` /
`is_cold_storage` / `cctv_camera_id` / `expiry_date` so pack SQL can run
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
`POST /v1/chat/ask`. Isolated gen: Cortex compute miss binds retrieved
ontology then validate/CRAG. Product path still Cortex-asks on compute
miss. Distill ladder in `scripts/score_climb.md` (certified-first,
YAML spine pack `ontology_spine.yaml` for retrieve, hybrid_fuse + CRAG,
Cortex text2sql -- no vendor SDK). Typed lake filters on isolated gen.
Baseline A/B @ `a9578348` exact 10/26 gen 1/26 WRONG=0.
Not a GitHub CI live job. Regression: `tests/test_score_climb.py`,
`tests/test_gen01_generative_ask.py`, `tests/test_gen02_bind_fallback.py`.
Not COMPLETE.

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
(timeout 120s). Live counts Platform. Not Phase A CLEAR. Regression:
`tests/test_gen_path_prove.py`.

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
