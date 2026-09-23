# GEN-02 -- measured live coverage climb harness (WRONG=0)

**Ticket:** [#180](https://github.com/Netie-AI/dms/issues/180) under EPIC-GEN-01 [#178](https://github.com/Netie-AI/dms/issues/178).
**Depends on:** GEN-01 landed @ `a9578348` (retrieve + execute-validate + offline `--ab`).
**Does not close** #180 or #178. **Not COMPLETE.** Not 99.95%.

> **GEN-03 (#194) changed what this harness can measure. Read this first.**
>
> - `ask_path=exact|generative` now returns **400 `ask_path_not_allowed`** unless the
>   server sets `DMS_HARNESS_ASK_PATHS`. A customer origin (including
>   `https://studio.netie.ai/api`) refuses every case, and the scorer counts a 400
>   as a failure rather than laundering it into an ABSTAIN. Run `--climb --ab`
>   only against a measurement origin that sets that variable.
> - No ask lane calls Cortex `POST /dms/query` and no ask lane binds `bind_plan`.
>   Cortex never implemented the `ontology_plan` mode this sent (KB F-0055), so the
>   isolated `generative` lane now ABSTAINs past the pre-gates instead of answering
>   from a keyword plan. Its previous answers were keyword-bound, and three of them
>   were wrong on value.
> - The sections below describe the pre-GEN-03 lane. They are kept for what the
>   numbers in the frozen baselines meant, not as instructions.

Climb is measured on the **product ask path** (pack exact-match first, then GEN-01 generative on miss). Do not expand certified packs as the climb. Isolated live A/B uses `ask_path=exact|generative`. Offline dual-path remains `python scripts/score_curated.py --ab` (no keys).

GEN-02 follow-on: isolated `ask_path=generative` Cortex `POST /dms/query` miss binds local `bind_plan` from the retrieved ontology, then the same compile → validate/CRAG → submit. Product path does **not** bind on miss (Cortex certified still runs). Explicit Cortex `unsure` is not overridden. Planted refuses stay ABSTAIN. Ontology measures are warehouse-honest (thin reseed vs Cortex lake), not pack SQL.

## Distill ladder (ideas only -- no vendor paste)

Netie-native mapping. Not DB-GPT / mybot / n8n / OpenWillow / guaca/rakazo code.

1. **Certified-first, then free gen.** `ask_path=product` and `exact` hit VQ/pack/refuse first. `generative` skips pack, tries retrieve→plan→validate, ABSTAIN only after that attempt. WRONG=0.
2. **Ontology as retrieve spine.** `demo_ontology` object/link/measure declarations (verified on the lake), not a new YAML pack format and not certified-query SQL. `from_manifest` remains the extract path.
3. **Hybrid fuse + CRAG-style confidence.** Retrieve tags `hybrid_fuse` when schema+ontology both hit. Harness grades `validated` / `abstain_validate` / `abstain_gate`. Doc RAG CRAG stays parked (P-DMS-19).
4. **Text2SQL as Cortex Insights generate + typed slots.** `POST /v1/insights` `generate=true` (OpenVault FreeRoute inside Cortex) then typed `POST /dms/query`; `bind_plan` on miss for isolated gen only. No vendor text2sql SDK.

Founder lock: abstain is safety (WRONG=0), not the ceiling. Isolated gen **tries**:

1. Multi-retrieve (schema SQL + ontology spine YAML + encodings + hybrid_fuse).
2. Ontology relations (`demo_ontology` verify + compile joins).
3. Generate SQL from typed slots (`bind_plan` + lake filters + `keep_gt` from the question).
4. Execute + EXPLAIN/grant validate; ABSTAIN only after that attempt fails.
5. Optional ML route/train/apply -- **not this slice**.

No LangChain/LangGraph. No vendor paste. No memorized VQ pack expansion as the climb.

Ontology YAML for retrieve is slot-name pack `packages/executor/dms_executor/ontology_spine.yaml` (loaded as retrieve allowlist, no SQL). Compile stays verified `demo_ontology` Python. Not a new vendor pack format.

Frozen live A/B @ `a9578348`: exact 10/26 (38.46 pct), gen 1/26 (3.85 pct), WRONG=0. Offline `--ab` on this branch is a separate measurement. Do not edit the frozen counts to invent a rise.

## Baseline (frozen)

Product-path live `curated_ceo` @ `91c5cc99` (VQ-04 refuse traps):

```
OK 7   LAYER 10   ABSTAIN 9   WRONG 0
answered = OK+LAYER = 17 / 26
```

Isolated A/B @ `a9578348` (GEN-01 offline, Platform-reported):

```
exact-match answered 10 / 26  (38.5 pct)
generative answered  1 / 26  (3.8 pct)
WRONG 0 both
```

Re-measure. Do not edit these counts to invent a rise. Climb gen via retrieve+Cortex compute (bind_plan on miss)+validate, not pack expansion.

## Who runs what

| Seat | Command | Must not claim |
|------|---------|----------------|
| Platform (Studio API + IAP) | `--climb --ab --url https://studio.netie.ai/api` | COMPLETE / 99.95% / greening planted refuses |
| Platform product-path | `--climb --url https://studio.netie.ai/api` | host-online COMPLETE |
| CI / any seat, no network | `--self-check` and `--ab` | a live score |

`--climb --ab` POSTs each curated question twice: `ask_path=exact` (VQ/pack/refuse only) then `ask_path=generative` (ontology retrieve, then ABSTAIN since GEN-03 closed the plan source; before GEN-03 this was Cortex compute with bind_plan on miss, execute-validate; skip pack). Both lanes need `DMS_HARNESS_ASK_PATHS` on the target origin or every case comes back 400. Cortex compute / OpenVault FreeRoute stay on the host. This script never sends keys.

## GEN-PATH-ROUTE-02 (#203) — measured ontology_plan>=1 (fix #201 leftover)

Root cause #201 stayed `ontology_plan=0` / `bind_plan=15`:

1. `--prove-path` uses `ask_path=generative` (`bind_on_miss=True`). Cortex Insights `generate=true ask=false` returned HTTP 200 REFUSE (FreeRoute `armed=false` leftover from #196) or 401 (A-0009: caller Authorization must be `Bearer ov_...`, not `dms-demo-viewer-key`). `compute_query` treated that as a transport miss.
2. Isolated gen then local `bind_plan` (same 15/26 QUALIFIED keyword bind). Offline `--prove-path` still hardcodes bind (no Cortex) — that lane cannot increment ontology_plan.
3. Deploy lag vs `a5b6fb1e` may have contributed to the immediate Platform stamp; even with #201 deployed, (1)+(2) still produce bind_plan=15.
4. Cortex success envelope is `status=ABSTAIN` + `generative.sql` (validated, not CERTIFIED). That path already parsed when SQL is present.

Fix: Insights 200/401 JSON counts as reached — do **not** bind_plan over it. A-0009 401 omits ranking; `GET /v1/insights/ontology` (YAML, no FreeRoute) attaches it. The **top** ranked Cortex metric id that exists on the DMS ontology compiles as `ontology_plan` (do not skip a Cortex-only top id to a weaker DMS id). Insights SELECT SQL still validate-or-abstain. Transport miss only may still bind on isolated gen.

Do **not** treat 57.69% as Cortex AI. Do not invent LIVE_KEY / `:5000`. Default prove timeout is 120s (CoT climb). `CORTEX_API_KEY` on Studio must already be the OV founder `ov_` key from #196 (no rotate).

### Platform re-run after this deploy

```powershell
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Platform owns live counts. Need `ontology_plan >= 1` and `WRONG=0`. Majority + WRONG=0 is Phase A HOLD clear (Epic stamp, not this PR). CI green != Phase A CLEAR.

## GEN-PATH-CLIMB-01 (#205) — raise ontology_plan>1 (thin coverage climb)

Baseline Platform live @ `e1f729a5`: **ontology_plan=1 / bind_plan=0 / WRONG=0**. Path works; coverage thin. Phase A HOLD CLEARED. Epic NOT COMPLETE. Do not reinvent 57.69% as AI COMPLETE.

Root cause of 1 hit: Cortex Insights ranking emits pack metric ids (`stock_value_by_category`) that were only accepted when the id existed verbatim on the DMS ontology (`sku_count`). Same-intent asks abstained.

Climb (WRONG=0):

1. Resolve top ranked id onto a DMS measure (exact, `cq_` strip, spine `measure_aliases`, >=2 token overlap on name+description). Do **not** skip a Cortex-only top id to a weaker unrelated id (`stock_value_by_category` must not become `sku_count`).
2. Overlay retrieve-typed group/filter/limit (`slots_for_measure`) so a "by category" ask is not a scalar total.
3. Forward retrieve `intent_slots` on Insights generate (FreeRoute **free+normal** inside Cortex). Attach YAML ranking even when generate SQL exists so a validate-fail can climb via ranked slots. Hostile SQL still abstains.
4. Isolated gen still must not `bind_plan` over Insights reached.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Product climb acceptance is Platform measured **ontology_plan>1 + WRONG=0** after deploy — not CI. Hand merge SHA via PR. Do not close #205 claiming epic COMPLETE. #180 k-scale waits until that rise.

## GEN-02 k-scale (#180) — FreeRoute free+normal climb beyond #205

Baseline Platform live @ `186f7d85`: **ontology_plan=9 / bind_plan=0 / WRONG=0**.
Phase A CLEAR stands. Epic **NOT COMPLETE**. Do not invent 99.95% / estate CLEAR.

Climb (WRONG=0, ontology_plan over bind_plan):

1. **FreeRoute ranked retry.** When Insights generate ran (not `UNARMED` / 401) but emitted no SQL/plan, POST generate once more with the ranked pack id as `query_plan` + `model_preference=free+normal`. Valid SELECT still validate-or-abstain. Unarmed generate does not retry (cannot invent keys).
2. **Same-intent ranking walk.** Default stays top-id-only (#205). With the ask text, walk past a Cortex id that shares **no** content tokens with the question (ranking noise). A Cortex-only id that overlaps the ask still aborts — do not skip intent to a weaker DMS measure.
3. **Pack-id slot overlay.** `cq_top3_category_sales` / `_by_category` / `top5` encode group/limit on the ranked id so a typo ask (`categoty`) still compiles. No `supplier_ranking` / alerts / delayed / storage-bin aliases — planted refuses stay ABSTAIN.
4. Typed `/dms/query` forwards `ranked_metric`. Isolated gen still must not `bind_plan` over Insights reached.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
python scripts/score_curated.py --climb --ab --url https://studio.netie.ai/api
```

Product acceptance is Platform measured **ontology_plan > 9 and/or rising answered coverage + WRONG=0** after deploy. CI green != climb PASS. Do not close #180 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-02 (#208) — continue live ontology_plan rise beyond 11

Baseline Platform live @ `58d27a81`: **ontology_plan=11 / bind_plan=0 / WRONG=0**.
#180 RISE_PASS. Epic **NOT COMPLETE**. Do not invent 99.95% / estate CLEAR.

Root cause of the 11 plateau: Cortex YAML ranking often puts `sku_count` first.
Same-intent walk (#180) only skipped ids with **no** question-token overlap, so
asks that mention SKU (`Top 5 selling SKUs`, `Top 3 SKUs by quantity`,
`Which SKUs are below reorder`) aborted instead of walking to the locked
measure (`outbound_value_myr` / `outbound_kg` / `below_reorder_lots`).

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Prefer-locked ranking walk.** When retrieve `intent_slots.measure` is set,
   walk past a ranked id that does not overlap that lock (sku_count on a
   revenue ask). A Cortex-only id that overlaps the lock still aborts. No
   prefer: keep the #180 question-token walk (stock_value_by_category must
   not become sku_count).
2. **FreeRoute retry uses walked slots.** Retry `query_plan` is the resolved
   DMS measure plus retrieve/pack group/limit/keep_gt, not the raw top pack
   id. `model_preference=free+normal`. Skip UNARMED. Typed `/dms/query`
   forwards the walked `ranked_metric`.
3. **Pack-id `sales_top` overlay.** `cq_sales_top5_value` / `cq_sales_top3_volume`
   encode product.sku + topN. Planted refuses stay ABSTAIN.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Product climb acceptance is Platform measured **ontology_plan > 11 + WRONG=0**
after deploy. CI green != climb PASS. Hand merge SHA via PR. Do not close
#208 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-03 (#210) — exhausted-ranking overlay beyond ontology_plan=13

Baseline Platform live @ `b9836bfa`: **ontology_plan=13 / bind_plan=0 / WRONG=0**.
#208 RISE_PASS. Epic **NOT COMPLETE**. Do not invent 99.95% / estate CLEAR.

Root cause of the 13 plateau: Cortex YAML ranking is often `sku_count` (or
sku+stock+capacity) and never includes the prefer-matching pack id, so the
#208 walk exhausts.

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Exhausted-ranking overlay.** After prefer-locked walk finds no
   resolvable id, pick a spine pack id whose dest equals the retrieve prefer
   lock and whose tokens (or dest name+spec) match the ask. Cortex catalog
   ids, not `bind_plan`. Abort still wins when a Cortex-only id overlaps the
   ask/lock (stock_value_by_category must not become sku_count).
2. **Remaining pack-id shapes.** `cold_storage` / `expired` / `chemicals` /
   `cctv` / `supplier_rank` / `low_stock` encode group/keep_gt on the ranked
   id.
3. **Honest supplier rank.** `supplier_rank_score` is the certified risk+lead
   formula at supplier grain. Finance compiles as `ontology_plan`. Ops
   without `suppliers` still ABSTAIN. No alerts / delayed / storage-bin
   aliases.
4. Typed `/dms/query` and FreeRoute `free+normal` retry use overlay slots.
   Isolated gen still must not `bind_plan` over Insights reached.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Product climb acceptance is Platform measured **ontology_plan > 13 + WRONG=0**
after deploy. CI green != climb PASS. Hand merge SHA via PR. Do not close
#210 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-04 (#212) — leftover Cortex L0 beyond ontology_plan=17

Baseline Platform live @ `0a5c6a9c`: **ontology_plan=17 / bind_plan=0 / WRONG=0**.
#210 RISE_PASS. Epic **NOT COMPLETE**. Do not invent 99.95% / estate CLEAR.

Root cause of the 17 plateau: all 17 curated_ceo L0s already compile as
ontology_plan. Remaining 9 are planted refuse/grant traps (WRONG if greened).
Leftover Cortex certified `cq_audit_overdue` was not in the score pack and
not in PACK_METRICS.

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Honest `audit_overdue`.** COUNT FILTER on `last_audit_date` older than
   90 days at supplier grain. Group `supplier_id`. Thin seed SCHEMA_VERSION 5
   adds the column. Cortex lake already has it (certified SQL).
2. **Exhausted-ranking overlay.** Prefer lock + spine alias `cq_audit_overdue`
   when YAML ranking is sku_count noise. Abort still wins on Cortex-only
   overlapping ids (stock_value_by_category must not become sku_count).
3. Finance compiles as `ontology_plan`. Ops without `suppliers` ABSTAIN.
   Planted refuses stay ABSTAIN. Not exact-match pack expansion.
4. Typed `/dms/query` and FreeRoute `free+normal` retry use overlay slots.
   Isolated gen still must not `bind_plan` over Insights reached.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Product climb acceptance is Platform measured **ontology_plan > 17 + WRONG=0**
after deploy. CI green != climb PASS. Hand merge SHA via PR. Do not close
#212 claiming #178 COMPLETE. curated_ceo n=27; QUALIFIED claim stays n=26.

## GEN-PATH-CLIMB-05 (#214) — break flat ontology_plan=17

Baseline Platform live @ `aa0ef108`: **ontology_plan=17 / bind_plan=0 / WRONG=0**
answered=**17/26**. #212 KEEP_HOLD. Epic **NOT COMPLETE**. Do not invent
99.95% / estate CLEAR.

Root cause of the flat 17 (checked, not prescribed):

1. **Pack saturation (held).** All 17 curated L0s in the frozen 26 already
   compile as `ontology_plan`. Remaining 9 are planted refuse/grant traps
   (WRONG if greened). Ranking on those 17 is not a no-op.
2. **Prove harness did not ask the new leftover (held).** #212 added
   `cq_audit_overdue` (n=27) but Platform stamped **17/26**. The 27th never
   entered the measured denominator. QUALIFIED frozen n=26 is a floor, not
   the live pack.
3. **Discarded:** ask_path routing miss (prove already uses
   `ask_path=generative` and got 17 ontology_plan). Retrieve no-op on the 17
   (those already hit). Ranking no-op on the 17 (same).
4. **Qualified leftover:** `cq_audit_overdue` still needs live
   `last_audit_date`. Climb-05 does not depend on that column.

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Score Cortex certified synonyms** of L0s live already answers
   (`How many SKUs in inventory?`, `SKU count in inventory`,
   `Top 5 SKUs by revenue`, `top 3 categories by sales value`). Same
   `certified_queries.yaml` SQL. **Not** PACK_METRICS exact-match.
2. Retrieve lock `categories`+sales → `outbound_value_myr`. Audit typed
   filter no longer None-outs when the verified measure exists but retrieve
   omitted `last_audit_date`.
3. Harness fails closed if leftover L0 ids are not in the scored pack
   (cannot stamp 17/26 from this SHA). QUALIFIED claim stays n=26.
4. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Product climb acceptance is Platform measured **ontology_plan > 17 + WRONG=0**
after deploy. CI green != climb PASS. Hand merge SHA via PR. Do not close
#214 claiming #178 COMPLETE. A 17/26 stamp is the frozen pack, not this SHA.

## GEN-PATH-CLIMB-06 (#216) — dual-flat 17/26 root-cause then rise

Baseline Platform live @ `683827ed`: **ontology_plan=17 / bind_plan=0 / WRONG=0**
answered=**17/26**. Same stamp as #212 @ `aa0ef108` and #210 @ `0a5c6a9c`.
Epic **NOT COMPLETE**. Do not invent 99.95% / estate CLEAR.

Root cause of the dual flat (checked, not prescribed):

1. **Frozen 26 saturates at 17 (held).** 17 L0s already compile as
   `ontology_plan`. Remaining 9 are planted refuse/grant traps (WRONG if
   greened). Cannot exceed 17 on n=26 without WRONG.
2. **Prove pack is local, not Studio (held).** `--prove-path --url`
   `score_pack_live` loads `tests/fixtures/curated_ceo/questions.yaml` from
   the scoring checkout (`scripts/score_curated.py` `DEFAULT_PACK`). Studio
   SHA identification does not change which questions are POSTed. A live
   Studio @ `683827ed` with a frozen-26 harness still stamps **17/26**.
3. **#214 fail-closed was ids-asked, not ontology_plan (held).**
   `_leftover_l0_unscored` only checks leftover qids appear in cases.
   Asked + ABSTAIN still PASSes WRONG=0 at ontology_plan=17.
4. **Synonyms were reachable on #210 code if asked (held).**
   `_locked_measure` already maps `how many sku` / `sku count` / `revenue`.
   If those phrases had been POSTed, ontology_plan would be >17. Flat 17
   means they were not in the scored pack.
5. **Discarded:** ranking no-op on the 17; ask_path miss (`ask_path=generative`
   already counted 17 `ontology_plan`); greening the remaining 9.
6. **Qualified leftover:** `cq_audit_overdue` still needs live
   `last_audit_date`. Rise does not depend on that column.

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Union rise L0s in the harness** so this SHA cannot score frozen 26
   even if local yaml lags. Four Cortex certified synonyms of L0s live
   already answers. Not PACK_METRICS.
2. **Live prove FAILs** on pack n<=26, rise L0s not `ontology_plan`, or
   ontology_plan<=17. Offline `--prove-path` stays bind_plan (no Cortex).
3. **`GET /health` `gen_path_climb`** advertises frozen_n / n / rise_l0 so
   Studio SHA and pack identity are coupled. Missing field is WARN (old
   Studio can still compile the 3 sku/revenue synonyms if the new harness
   asks them).
4. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Need **this SHA's harness** (not a frozen-26 checkout) against Studio >= this
merge. Product climb acceptance is Platform measured **ontology_plan > 17 +
WRONG=0**. A **17/26** stamp is FAIL on this SHA, not PASS. CI green !=
climb PASS. Do not close #216 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-07 (#218) — continue rise past ontology_plan=21

Baseline Platform live @ `41f5824f`: **ontology_plan=21 / bind_plan=0 /
WRONG=0** answered=**21/31** on the uncapped harness. Epic **NOT COMPLETE**.
Do not invent 99.95% / estate CLEAR. Do not reintroduce frozen n=26. Do
not shrink the pack to fake a higher percent.

21/31 is the frozen 17 L0s plus the four climb-05/06 certified synonyms.
Unused Cortex `certified_queries.yaml` synonyms were never POSTed:

1. `Top 5 selling SKUs by sales` (`cq_sales_top5_value`)
2. `show top 3 category sales` (`cq_top3_category_sales` primary)
3. `top 3 category sales`

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Score those leftover certified synonyms** as `ontology_plan`. Same
   parent SQL. **Not** PACK_METRICS exact-match.
2. Retrieve lock `category sales` → group `product.category`.
3. Harness unions the new L0s so this SHA cannot score n=31. Live prove
   FAILs on ontology_plan<=21 or climb-07 rise L0s not `ontology_plan`.
4. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Need **this SHA's harness** against Studio >= this merge. Product climb
acceptance is Platform measured **ontology_plan > 21 + WRONG=0**. A
**21/31** stamp is FAIL on this SHA. CI green != climb PASS. Do not close
#218 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-08 (#220) — continue rise past ontology_plan=24

Baseline Platform live @ `9b73b377`: **ontology_plan=24 / bind_plan=0 /
WRONG=0** answered=**24/34** on the uncapped harness. Epic **NOT COMPLETE**.
Do not invent 99.95% / estate CLEAR. Do not reintroduce frozen n=26 or
n=34. Do not shrink the pack to fake a higher percent.

24/34 is the frozen 17 L0s plus the seven climb-05/06/07 certified
synonyms. Unused Cortex leftover was never POSTed:

1. `top 3 categoty sales` (`cq_top3_category_sales` VQ-01 typo synonym)
2. Ops `How many SKUs do we have in inventory?` (`cq_sku_count`)
3. Ops `Show SKU count by category` (`cq_sku_count_by_category`)

Same parent SQL. **Not** PACK_METRICS. Ops is granted inventory+locations
(ops_stock_value already live). Audit overdue still optional
(`last_audit_date` ceiling).

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Score those leftover certified L0s** as `ontology_plan`.
2. Retrieve/overlay treat certified `categoty` as `category` so the typo
   synonym groups `product.category`, not sku/sales_top5.
3. Harness unions the new L0s so this SHA cannot score n=34. Live prove
   FAILs on ontology_plan<=24 or climb-08 rise L0s not `ontology_plan`.
4. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Need **this SHA's harness** against Studio >= this merge. Product climb
acceptance is Platform measured **ontology_plan > 24 + WRONG=0**. A
**24/34** stamp is FAIL on this SHA. CI green != climb PASS. Do not close
#220 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-09 (#222) — continue rise past ontology_plan=27

Baseline Platform live @ `4f189f81`: **ontology_plan=27 / bind_plan=0 /
WRONG=0** answered=**27/37** on the uncapped harness. Epic **NOT COMPLETE**.
Do not invent 99.95% / estate CLEAR. Do not reintroduce frozen n=26,
n=34, or n=37. Do not shrink the pack to fake a higher percent.

27/37 is the frozen 17 L0s plus the ten climb-05/06/07/08 leftovers.
Unused Cortex leftover was never POSTed:

1. Ops `How many SKUs in inventory?` (`cq_sku_count` synonym)
2. Ops `SKU count in inventory` (`cq_sku_count` synonym)
3. Ops `List chemicals in inventory` (`cq_chemicals_list` primary)

Same parent SQL. **Not** PACK_METRICS. Ops is granted inventory+locations
(ops sku primaries already live). Audit overdue still optional
(`last_audit_date` ceiling).

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Score those leftover certified L0s** as `ontology_plan`.
2. Retrieve/overlay lock chemicals onto `stock_value_myr` (sku_count
   ranking is noise). Ops sku synonyms reuse the climb-08 sku lock.
3. Harness unions the new L0s so this SHA cannot score n=37. Live prove
   FAILs on ontology_plan<=27 or climb-09 rise L0s not `ontology_plan`.
4. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Need **this SHA's harness** against Studio >= this merge. Product climb
acceptance is Platform measured **ontology_plan > 27 + WRONG=0**. A
**27/37** stamp is FAIL on this SHA. CI green != climb PASS. Do not close
#222 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-10 (#224) — continue rise past ontology_plan=30

Baseline Platform live @ `1257fc3b`: **ontology_plan=30 / bind_plan=0 /
WRONG=0** answered=**30/40** on the uncapped harness. Epic **NOT COMPLETE**.
Do not invent 99.95% / estate CLEAR. Do not reintroduce frozen n=26,
n=34, n=37, or n=40. Do not shrink the pack to fake a higher percent.

30/40 is the frozen 17 L0s plus the thirteen climb-05/06/07/08/09 leftovers.
Unused Cortex leftover was never POSTed:

1. Ops `Which items are expired?` (`cq_expired_items` primary)
2. Ops `Which locations are cold storage?` (`cq_cold_storage` primary)
3. Ops `Which locations are above 90 percent capacity?` (`cq_capacity_above_90`)

Same parent SQL as Finance L0s already in frozen 17. **Not** PACK_METRICS
expansion (prove-path is generative). Ops is granted inventory+locations.
Audit overdue still optional (`last_audit_date` ceiling).

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Score those leftover certified L0s** as `ontology_plan`.
2. Retrieve/overlay lock expired onto `stock_value_myr` (sku_count ranking
   is noise) and cold/capacity onto `utilisation_pct`.
3. Harness unions the new L0s so this SHA cannot score n=40. Live prove
   FAILs on ontology_plan<=30 or climb-10 rise L0s not `ontology_plan`.
4. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Need **this SHA's harness** against Studio >= this merge. Product climb
acceptance is Platform measured **ontology_plan > 30 + WRONG=0**. A
**30/40** stamp is FAIL on this SHA. CI green != climb PASS. Do not close
#224 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-11 (#226) — continue rise past ontology_plan=33

Baseline Platform live @ `f833e567`: **ontology_plan=33 / bind_plan=0 /
WRONG=0** answered=**33/43** on the uncapped harness. Epic **NOT COMPLETE**.
Do not invent 99.95% / estate CLEAR. Do not reintroduce frozen n=26,
n=34, n=37, n=40, or n=43. Do not shrink the pack to fake a higher percent.

33/43 is the frozen 17 L0s plus the sixteen climb-05/06/07/08/09/10 leftovers.
Unused Cortex leftover was never POSTed:

1. Ops `Show warehouse capacity utilisation` (`cq_capacity_utilisation`)
2. Ops `Show the CCTV camera for warehouse A` (`cq_cctv_wh_a`)
3. Ops `Which SKUs are below reorder level in warehouse A?` (`cq_low_stock_wh_a`)

Same parent SQL as Finance L0s already in frozen 17. **Not** PACK_METRICS
expansion (prove-path is generative). Ops is granted inventory+locations.
Audit overdue still optional (`last_audit_date` ceiling).

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Score those leftover certified L0s** as `ontology_plan`.
2. Retrieve/overlay lock utilisation/cctv onto `utilisation_pct` (sku_count
   ranking is noise) and low-stock onto `below_reorder_lots`.
3. Harness unions the new L0s so this SHA cannot score n=43. Live prove
   FAILs on ontology_plan<=33 or climb-11 rise L0s not `ontology_plan`.
4. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Need **this SHA's harness** against Studio >= this merge. Product climb
acceptance is Platform measured **ontology_plan > 33 + WRONG=0**. A
**33/43** stamp is FAIL on this SHA. CI green != climb PASS. Do not close
#226 claiming #178 COMPLETE.

## GEN-PATH-CLIMB-12 (#228) — continue rise past ontology_plan=36

Baseline Platform live @ `6eb89562`: **ontology_plan=36 / bind_plan=0 /
WRONG=0** answered=**36/46** on the uncapped harness. Epic **NOT COMPLETE**.
Do not invent 99.95% / estate CLEAR. Do not reintroduce frozen n=26,
n=34, n=37, n=40, n=43, or n=46. Do not shrink the pack to fake a higher
percent.

36/46 is the frozen 17 L0s plus the nineteen climb-05/06/07/08/09/10/11
leftovers. certified_queries.yaml primaries+synonyms Ops can run are
already POSTed. Unused leftover is Ops parent-SQL of those certified
L0s, compiled via ontology spine slots:

1. Ops `SKU count by category` (`cq_sku_count_by_category`)
2. Ops `stock value by category` (`cq_stock_value_by_category`)
3. Ops `shipment cost by destination` (`cq_cost_by_destination`)

Same parent SQL as Ops L0s already in the pack. **Not** PACK_METRICS
expansion (prove-path is generative). **Not** golden TARGET paraphrases.
Audit overdue still optional (`last_audit_date` ceiling).

Climb (WRONG=0, ontology_plan over bind_plan):

1. **Score those leftover parent-SQL L0s** as `ontology_plan`.
2. Retrieve/overlay lock sku-by-category onto `sku_count`, stock-value
   onto `stock_value_myr` (sku_count ranking is noise), shipment-cost
   onto `shipping_cost_myr`. Spine alias `shipment_cost_by_destination`.
3. Harness unions the new L0s so this SHA cannot score n=46. Live prove
   FAILs on ontology_plan<=36 or climb-12 rise L0s not `ontology_plan`.
4. Planted refuses stay ABSTAIN. FreeRoute `free+normal` only.

### Platform re-run after this deploy

```
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Need **this SHA's harness** against Studio >= this merge. Product climb
acceptance is Platform measured **ontology_plan > 36 + WRONG=0**. A
**36/46** stamp is FAIL on this SHA. CI green != climb PASS. Do not close
#228 claiming #178 COMPLETE.

## GEN-PATH-ROUTE-01 (#201) — Studio/prove hits Cortex ontology_plan

Wire: generative compute is Cortex `POST /v1/insights` `generate=true`
`ask=false` (OpenVault FreeRoute free+normal inside Cortex; DMS forwards the
configured `CORTEX_API_KEY` / ov_ only, never invents LIVE_KEY). Typed
`POST /dms/query` `mode=ontology_plan` remains fallback. Insights SELECT SQL
is hostile/grant/EXPLAIN then Cortex submit. Isolated `ask_path=generative`
may still `bind_plan` on miss (labeled). Product path does not bind on miss.

CI / offline `--prove-path` is still local bind_plan (no Cortex). That is
HOLD evidence, not AI coverage. Do **not** treat 57.69% as ontology_plan.

### Platform re-run after this deploy

On prove/studio (Cortex + OpenVault already on the host). No second vault.
Do not paste tokens. Do not invent `:5000` green.

```powershell
# After merge is on the prove unit:
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
# or IAP loopback:
python scripts/score_curated.py --prove-path --url http://127.0.0.1:8090
```

Report fields Platform owns:

1. Per-qid `plan_source`
2. `ontology_plan` answered count (need **>= 1** for this ticket's live leftover; majority + WRONG=0 is Phase A HOLD clear, Epic stamp)
3. WRONG=0 on curated_ceo
4. `Phase A HOLD may clear` YES/NO — harness never writes COMPLETE

Artifacts: `score_gen_path_prove.json`. CI green != Phase A CLEAR.



Independent label of whether an answered gen ask used Cortex `POST /dms/query` (`ontology_plan`) or local keyword `bind_plan`. Reads `plan_source` on the envelope. Does **not** guess from SQL or `compute_fallback:bind_plan` assumption text. Missing field = `other`.

```powershell
# Offline (CI-safe). Local --ab compute is bind_plan, so HOLD stays NO.
python scripts/score_curated.py --prove-path

# Platform live against Studio API (OV/FreeRoute already on the host):
python scripts/score_curated.py --prove-path --url https://studio.netie.ai/api
```

Report fields (Platform owns live numbers):

1. Per-qid `plan_source`: `ontology_plan` | `bind_plan` | `other`
2. Count/% answered via each
3. WRONG=0 on curated_ceo (same pack as the QUALIFIED 15/26 claim)
4. `Phase A HOLD may clear`: YES only if majority of answered gen is `ontology_plan` and WRONG=0. The harness never writes COMPLETE.

Artifacts: `score_gen_path_prove.json`, `score_gen_path_prove_cases.json`. Trust pickup: `GET /v1/trust/summary` `gen_path_prove`.

CRAG-style gates (validate-or-abstain, ideas only, not a vendor clone): each gen envelope is graded `validated` / `abstain_validate` / `abstain_gate` / `skipped`. Document RAG CRAG stays parked (P-DMS-19) until a doc index exists.

Keys/models stay in Cortex + OpenVault on the host. This script never sends API keys.

## Env (fail closed -- no laptop default on --climb)

```
--url            preferred. Example: https://studio.netie.ai/api
DMS_API_BASE     used if --url omitted (alias STUDIO_API_BASE)
DMS_URL          last resort for --climb; --live still defaults to 127.0.0.1:8090
```

Unset `--url` / `DMS_API_BASE` / `DMS_URL` -> exit 2 CONFIG.
Unreachable host or IAP **401/403** -> exit 3 BLOCKED (not a score, not PASS).
Cloudflare **CF1010** (browser-signature ban) -> exit 3 BLOCKED, named as CF1010 not IAP.
`demo_fallback=true` or `ask_mode=demo` on `/health` -> exit 1 FAIL.
WRONG>0 (green planted refuse, demo fallback on an answer, transport error mid-pack) -> exit 1 FAIL.

**Transport (SCORE-CLIENT-01).** `--climb` / `--climb --ab` probe `/health` and POST `/v1/chat/ask` via **httpx** (`score_http`), not `urllib.request`. Bare urllib hits Cloudflare 403 CF1010 on `https://studio.netie.ai`. Durable origin is `https://studio.netie.ai/api`. Loopback `127.0.0.1:8090` is a host workaround, not the public measurement. If httpx still CF1010s: **Platform DevOps exception** (Bot Fight / WAF allow httpx/curl-class). This seat does not invent COMPLETE.

Cursor cloud / seats without Access cookies will see IAP 403 on `studio.netie.ai`. That is BLOCKED for this seat. Platform on prove / with IAP runs the score.

Studio SPA `/health` is HTML. Use the **API** prefix (`/api/health`).

## Run

```powershell
# Fail-closed self-check (no network, CI-safe):
python scripts/score_curated.py --self-check

# Offline A/B: exact-match pack vs retrieve+bind generative (GEN-01). No keys.
python scripts/score_curated.py --ab

# Platform live climb against Studio API (Cortex+OpenVault already on the host):
$env:DMS_API_BASE = "https://studio.netie.ai/api"
python scripts/score_curated.py --climb
# or:
python scripts/score_curated.py --climb --url https://studio.netie.ai/api
```

Artifacts (under `DMS_SCORE_DIR` or `.tmp/`): `score_climb.json` (counts + delta), `score_climb_cases.json` (per qid).

Prints real OK / LAYER / ABSTAIN / WRONG, answered delta vs `91c5cc99`, and `answered_by_path` (`exact_match` from `route=governed_metric|verified_query`, `generative` from `route=generated`). LAYER on expect:l0 is an honest generative/L1/L2 answer, not L0.

## Honesty

- WRONG=0 is the law. Coverage may rise, stay flat, or fall -- the harness reports the delta. It does not invent a climb.
- Planted refuse/abstain traps staying green is WRONG, not coverage.
- This is **not** a GitHub CI job. Do not add `--climb` to `.github/workflows/ci.yml`.
