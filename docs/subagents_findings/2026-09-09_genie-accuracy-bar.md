---
keywords: [Genie, accuracy, precision-on-answered, curated, hostile, VQ-02, EPIC-019, EPIC-CCA, envelope, E9, hard-rule-12, trusted-assets]
main_idea: DMS already matches Genie's win law (0 WRONG on answered) on last measured packs. The remaining gap is product trusted-asset population plus a sealed vault, not a missing Text2SQL model. Cortex invent-SQL at live_ask:477 can still ship a non-empty wrong number. Smallest authorized move is #38: register the curated 11 L0 asks as Studio VQs.
---

# Genie accuracy/trust bar (2026-09-09)

PREFLIGHT: HIT. Reuse competitor bar, curated pack, hostile 12/14, CCA spelling-not-SQL, VQ-02 register, STATUS sealed vault.

Do not invent a percentage of "Genie completeness". Do not re-run live ask (vault sealed). Do not clone Genie UI / Genie Code / Unity Catalog / Vector Search / a second orchestrator.

## What Genie actually wins on

From `.cursor/agents/dms-accuracy.md` (not Spider 2.0, not notebook Genie Code):

1. Precision on answered near 100 percent.
2. Trusted assets / example queries (walkthrough 13 curated after 4 curation passes; UAT >80 percent).
3. Semantic model plus value/literal search, not regex intent.
4. Coverage grows only after precision holds.

DMS law is the same: precision-on-answered is 100.00 percent. Coverage is never bought with precision.

## Envelope: what POST /v1/chat/ask already asserts

Customer path: `apps/api` -> `Executor.live_ask` -> `map_ask_response_to_envelope` (`packages/executor/dms_executor/__init__.py:607`) -> `build_answer_envelope` -> `assert_envelope_valid`.

`assert_envelope_valid` (`envelope.py:1354`) is E1-E9 properties on the customer dict:

| Id | Field / rule |
|----|----------------|
| E1 | `abstained` and `badge` agree (`ABSTAIN` vs confident) |
| E2 | abstain: empty `values[]`, empty `contributing_sources[]`, null `drillthrough_token` |
| E3 | non-abstain requires `values[]`, `sql_used`, `audit_id`/`answer_id` |
| E4 | money/decimal literals in `text` must appear in `values[]` |
| E5 | `badge` in allowed set (L0/L1/L2/L2_ANOMALOUS/ABSTAIN) |
| E6 | `demo_fallback_used` requires `demo_fallback_banner` |
| E7 | sources require `drillthrough_token` |
| E8 | `as_of` required, parseable, not future |
| E9 | no executed query => no uncited figure in `text` |

E10/E11/E12/E9-02/E9-03/hard-rule-12 are **constructor demotes** in `build_answer_envelope`, not extra keys in `assert_envelope_valid`:

- E9 uncited figures (`envelope.py:1025`)
- hard rule 12 empty SQL result (`envelope.py:1049`)
- E9-03 confident badge, no rows, no SQL (`envelope.py:1064`)
- E9-02 / F32 competing sheet ranking (`envelope.py:1086`)
- E10 grouped ask got scalar (`envelope.py:1119`)
- E12 one-number ask got ranking (`envelope.py:1139`)
- xlsx-named ask hit demo warehouse (`envelope.py:1157`)
- E11 polarity invert (`envelope.py:1173`)
- F40 `route=refused` outranks engine badge (`__init__.py:629`)

Ask-path tests also assert rendered `text`, `rows`, `values`, `sql_used`, `route` (VQ-02, RAG-04, E9-02, extract-object). A SQL-only test would miss a green badge over wrong rows.

## live_ask order (code, not ACTIVE.md)

`packages/executor/dms_executor/__init__.py:390-507`:

1. `maybe_verified_ask` (VQ-02)
2. `maybe_metric_ask` (name/alias only)
3. `maybe_bronze_sheet_ask` (named workbook+sheet)
4. `tools_only_abstain` if `objects.*` grounding or extra `_metric_tools` (`:427`)
5. CCA only if `DMS_CCA_CASCADE` on (`cascade_enabled`, default 0)
6. Cortex `self._cortex.ask` (`:477`) then map (`:498`)

ACTIVE.md still says CCA before bronze. Code does not. Trust the function.

## Curated pack vs Genie 13

- Pack: `tests/fixtures/curated_ceo/questions.yaml` -- **14 cases** (self-check requires >=13).
- Split in YAML: **11 expect l0**, **3 expect abstain** (`ops_spend_boundary`, `trap_last_month`, `trap_short_paraphrase`). `trap_categoty` expects L0 (VQ-01 Cortex pack synonym).
- Instrument: `scripts/score_curated.py` (walkthrough analog). WRONG is the only P0. Expected-L0 that abstains is ABSTAIN, not WRONG.

Last measured **live** (do not re-run; vault sealed):

- `docs/subagents_findings/2026-08-28_three-subagent-scale.md`: **10/14 L0, 4 expected abstains, 0 WRONG**.
- Same 10/14 cited in `2026-09-03_hostile-coverage.md`.
- 2026-09-08 `live-ask-after-unseal.md` on dirty :8090: `Show top 3 categoty sales` -> **ABSTAIN** (tools-only / semantic). That does **not** overwrite 10/14; it means the Aug-28 live number is stale vs a Space with extra metric tools. New live score needs unseal.

## Hostile pack last numbers

Pack size: **14** ids in `scripts/score_answers.py` `QUESTION_PACK`.

- 2026-08-28 live: waves 0/11 then 6/11, later 71.43 then 64.29, serial confirm **71.43 percent coverage, 100.00 percent precision, 0 WRONG**. BETA/KL/F32/RAG abstain; exact SKU-BETA / Kuala Lumpur certify.
- 2026-09-03 live: **10/14 correct, 0 WRONG**.
- 2026-09-08 intercept (pytest, not live HTTP): **12/14 correct, 2 None** (F32 no sheet, RAG no-SQL), **0 WRONG**. Unique encodings L0. Cite `2026-09-08_bronze-unique-encoding.md`. Oracle-only still lists SKU-BETA=1,500.75 and Kuala Lumpur=1,700.75 and does **not** score the ask path.

## VQ-02: customer path, match, Studio

On the customer path: **yes**. First hook in `live_ask` (`__init__.py:390`). HTTP: `POST /v1/studio/verified-queries` + `GET ...?space_id=` required (`studio.py:387`). Ask test: `tests/test_vq02_verified_register.py` asserts L0, executed rows, steward SQL, Cortex not called; foreign Space misses.

Match: **exact normalize** of question **plus synonyms stored on the asset** (`verified_queries.py:203` `_hit`). Not a product regex cascade. Grounded-file asks skip VQ (`:227`).

Studio register: **yes** (`StudioPage.tsx:949` "Certified question", question+SQL). `compliance_gate` before write.

Synonyms gap: store and API accept `synonyms` (`VerifiedQueryIn`, `_synonyms_norm`). Studio `registerVerifiedQuery` **does not send synonyms** (`api.ts:489`). `test_vq02` never plants a synonym hit. VQ-01 synonyms live on the **Cortex pack**, not DuckDB (`test_vq01_ask_envelope.py`).

STATUS: VQ-02 **#40 CLOSED**. Epic **#38 OPEN**.

## CCA: ships OFF

`DMS_CCA_CASCADE` default **0** (`cascade.py:87`). STATUS + CHANGELOG: binders sound; engagement rule 46/106 false engage, 35/37 false miss. Do not gate a customer ask.

Certifies: filter **spellings** against landed DISTINCT (hard rule 12 class). Missing encoding abstains before L0 and discloses spellings.

Does **not** certify: executed SQL. `live_ask` still passes the question to Cortex unmodified when the flag is on. Cite `2026-09-05_cca-certifies-spelling-not-sql.md`. Route 2 (check `sql_used` vs `BinderResult.values`) is **outside** EPIC-CCA.

## Metric tools / objects tools-only

Coverage vs invent-SQL: extra registered tools or `objects.*` grounding -> **ABSTAIN**, name the tools, Cortex not called (`metric_tools.py:385` `tools_only_abstain`). Hit only on **tool name or authored alias** (`match_metric_tool:104`). Free-text BI misses on purpose (F28). Named workbook+sheet still runs **before** that abstain (`CHANGELOG` 2026-09-08).

This is Genie-class trusted SQL for published tools. It **cuts coverage** of the curated walkthrough on a Space that has extra tools (categoty / SKU ranking become ABSTAIN unless registered as a tool or VQ).

## Remaining EPIC-017 / 018 / 019 vs Genie trusted-asset loop

Agent file `.cursor/agents/dms-accuracy.md` is **stale** on 017/018.

| Epic | STATUS (2026-09-08) | vs Genie loop |
|------|---------------------|---------------|
| 017 #33 | CLOSED. E9/F32/E12/refused-badge on customer envelope. | Envelope fail-closed is shipped. Residual: Cortex#11 engine half of F40 (`repro_refused_badge.py` LINK 1). DMS half closed (#66). |
| 018 #35 | CLOSED. F42 `score_answers --oracle-only` is a CI step that can fail. F46 bakeoff pins wrong==0 then precision then coverage. | Instrument exists. Not a substitute for a 13-question steward walkthrough. `verify_freeform_demo --self-check` still not CI. |
| 019 #38 | OPEN. Children VQ-01 #39 and VQ-02 #40 CLOSED. | Machinery exists; **the 13-click trusted-asset loop is not populated**. Curated YAML is a scorer, not `main._verified_queries` rows. Genie 100 percent on 13 after 4 passes is a **steward practice**, not a missing model. |
| CCA #132 | OPEN. Cascade OFF. | Value/literal search exists behind a flag that must stay 0 until engagement is measured on a real log. |

## The one place a confident wrong number can still exit

**File:line:** `packages/executor/dms_executor/__init__.py:477` `self._cortex.ask`, mapped at `:498`.

Envelope demotes empty results, uncited figures, refused routes, shape/polarity/scope. It **cannot** tell a wrong JOIN or wrong grain from a right one when SQL executed and rows are non-empty. That is the class Genie beats with example queries, not with a better LLM.

CCA OFF means encodings are not bound on that path. Tools-only Spaces never reach it. Demo Finance **without** extra tools still does.

VQ registered wrong SQL is a second, steward-owned exit (`verified_queries.py:264` L0). Same class as a bad Genie example query.

## Smallest accuracy-shaped improvement already authorized

**Under #38, not #132, not a new Text2SQL model:**

Register the 11 YAML `expect: l0` curated questions (plus `trap_categoty` synonym) as Studio VQ-02 assets with steward SQL. Optional: wire Studio to POST `synonyms` (API already has the field). Score with `score_curated.py --live` after unseal. Fail closed if a green trap appears.

That is Databricks "example queries beat every other iteration" on the path that already runs first.

Not this: flip `DMS_CCA_CASCADE`, Distil Text2SQL, Vector Search, Genie Space UI, Unity Catalog.

#132 remainder (OpenVault ModelProviderPort + pack-certify proposer, flag stays 0) does **not** move the customer Genie bar. Leave it queued.

## Root cause of remaining Genie gap

**Product + measurement, not missing envelope law.**

- Product: trusted-asset loop is unpopulated. Walkthrough questions still reach Cortex invent-SQL unless a VQ/tool/named-sheet hits.
- Measurement: vault **sealed** (STATUS NEEDS-YOU). Last live curated 10/14 is 2026-08-28. Cannot certify a new walkthrough tonight.
- Not: "we are X percent as complete as Genie."

## What parent may ship now

- Claim: last measured packs **0 WRONG** (precision-on-answered held). Coverage 10/14 curated live (Aug-28), 12/14 hostile intercept (Sep-08). Cite findings. Do not quote a Genie-completeness percent.
- Claim: E1-E12 fail-closed on the customer envelope; F40 DMS half closed.
- Claim: VQ-02 + metric tools are the trusted-asset **mechanism**. Not that 13 steward questions are loaded.
- Do not claim CCA "never a confident wrong filter." Flag is 0; even on, it certifies spellings not SQL.
- Do not claim live Chat equals Aug-28 while extra `_metric_tools` force tools-only.
- Next writer under #38: seed curated VQs. Human blesses SQL. That is the 4-pass analog. Needs vault unseal to score live.

Does not prove: customer UAT >80 percent; Cortex#11; #116 SQL hosts; CCA engagement <=5 percent.
