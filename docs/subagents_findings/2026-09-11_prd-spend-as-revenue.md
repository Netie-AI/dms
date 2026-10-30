---
keywords: [prd-agent, F107, spend-as-revenue, revenue_total, query_skills, E9, verify_demo_live, SCALAR_Q, EPIC-019, EPIC-008, glossary, categoty, unseal]
main_idea: F107. Spend-as-revenue is KEEP under OPEN #38 plus Cortex query_skills, not a new epic and not a reopen of E9. Envelope demote REFUSE. Harness spend!=revenue is EPIC-008 honesty not the product fix. Glossary/about/strong/OK PARK as honest ABSTAIN. Slice none. NEEDS-YOU which patch.
---

# PRD Mode B -- spend-as-revenue vs honest coverage-miss (2026-09-11)

PREFLIGHT: HIT `2026-09-11_unseal-accuracy-scale.md`.
reuse: `2026-09-10_categoty-incomplete.md`, `2026-09-10_prove-23-30-pack-writer.md`, `2026-09-10_prd-ai-chat-genie-code.md` (F95 KEEP ABSTAIN until steward VQ; no glossary mega-epic), `2026-09-09_prd-cortex-genie-l1l2.md` (F89 Cortex L2 after gate).
spawn: skip

Laptop-ASCII. PRD Agent Mode B. No tickets. No product code. No Docker. Do not recycle `:8090`/`:8010`. Do not close GitHub issues. No PRD widen.

Live defect 2026-09-11, vault unsealed, `DMS_DEMO_FALLBACK=0`. Cite: `docs/subagents_findings/2026-09-11_unseal-accuracy-scale.md`. Next ledger id is **F107**. Do not reuse F106.

## Nearness (artifacts named, no percentage)

Buyer Chat can certify grouped spend-by-country and still ship outbound revenue as "total spend" under `L2_VALIDATED`. `31/31` is not a zero bound. Not nearer to auditor-ready CEO Chat. Landing not re-fetched this run (Marketing).

| Artifact checked | What is true on this disk |
|---|---|
| Live finding `2026-09-11_unseal-accuracy-scale.md` | Finance `What is our total spend?` HTTP 200 `badge=L2_VALIDATED` `route=sql` assumptions `query skill match score=1.000 -> revenue_total` SQL `SUM(quantity_kg * unit_cost_myr) FROM transactions WHERE txn_type='OUT'` alias `revenue_myr=80787598.3`. `assert_envelope_valid` PASSES (E9: query ran). Certified spend-by-country is inventory JOIN suppliers (MY 448.8M). Four other asks honest ABSTAIN. |
| `STATUS.md` 2026-09-11 | `verify_demo_live` 31/31. R-0010 bound is not zero. EPIC-017 #33 and EPIC-018 #35 CLOSED. EPIC-019 #38 OPEN. Do not kill `:8090`/`:8010`. |
| `scripts/verify_demo_live.py` `SCALAR_Q` | Exact phrase `What is our total spend?`. `scalar_ok` is one numeric value, not spend!=revenue. Follow-up `add 2000` is `envelope_compute_or_abstain`. |
| `packages/executor/dms_executor/envelope.py` E9 | Provenance of a figure, not metric identity. E12 is grouped-vs-scalar, not spend-vs-revenue. |
| `packages/executor/dms_executor/envelope.py` + `__init__.py` | Cortex `query_skill` maps to `L2_VALIDATED`. |
| Cortex `query_skills.py` `find` | Score `1.0` is exact `normalize_trigger` match (lower+split), not cosine. Ops DB captured this phrase onto `revenue_total`. |
| Cortex `metrics.yaml` `revenue_total` | Synonyms are revenue/sales, not spend. SQL is outbound `txn_type='OUT'`. |
| Cortex `metrics.yaml` `spend_by_country` + `certified_queries.yaml` `cq_spend_by_country` | Inventory JOIN suppliers. No scalar `spend_total` / `cq_spend_total`. L1 `route_to_metric` does not bind bare `spend` to revenue. |
| Cortex `catalog_answer.py` `is_catalog_intent` | Capability / metadata / browse. Does not match "What is this database about?", "Is this database strong?", "What does OK mean in this data?". |
| `gh` 2026-09-11 | #33 CLOSED, #35 CLOSED, #38 OPEN, #39 CLOSED, #8 OPEN, Cortex#11 OPEN. WIP DMS #108/#116 and #38. |

## Analog table

TAS-DMS Chat envelope. TAS-CORTEX plane 3 C7 + pack + query_skills. Prefer extend-live certified spend + skill refuse. Do not DISTILL a glossary chatbot. Palantir P1 PARK. BAN n8n / grok-bot / AGPL / leaked Claude Code / Activepieces 665.

| Piece | KEEP / OPEN / REFUSE / PARK / NO epic | Live analog | Why |
|---|---|---|---|
| (a) Spend must not bind to outbound revenue | KEEP existing 0-wrong | Cortex pack + query_skills; DMS envelope only asserts E9 | Law already in PRD success + Wave 7. New mega-epic would contend with #38. |
| (a) L0 scalar spend asset | OPEN #38 | `cq_spend_by_country` grain; no scalar certified | VQ-01 #39 CLOSED without scalar spend; fall-through is the miss. |
| (a) Query-skill exact-hit spend -> `revenue_total` | Cortex engine (OPEN Cortex#11 completeness) | `query_skills.find` then `compile_metric(revenue_total)` | How the answer is derived. Same L2_VALIDATED-lie class as F40, different cause. |
| (b) DMS envelope demote on spend + `revenue_myr` | REFUSE | F40 was refusal-with-nothing-read; F28/F83 refuse product-intent regex | Number has executed SQL. Heuristic is a second semantic layer in DMS. |
| (c) `verify_demo_live` assert spend!=revenue | KEEP instrument, not the fix | `SCALAR_Q` + `scalar_ok` | Makes 31/31 honest. Chat still lies until Cortex/#38. Parent **EPIC-008 #8**. |
| (d) about / strong / OK / glossary | PARK | `catalog_answer` is capability prose, not these asks | Founder coverage ask. 0-wrong says KEEP ABSTAIN until steward VQ. No mega-epic. |
| (e) `categoty` typo | OPEN #38 already | VQ-01 remainder; live `:8010` still unknown-noun first | Honest ABSTAIN. Do not treat as new defect. |
| Reopen EPIC-017 E9 | REFUSE | E9 held | Widen would turn provenance into metric-name identity. |
| Reopen EPIC-018 | REFUSE | `score_answers --oracle-only` exit 0 | Demo harness is not the 018 instrument. |
| Reopen EPIC-012 | REFUSE | CLOSED L2 bakeoff | Do not reopen to free a skill-match ticket. |
| Two lakes split | not this row | `2026-09-10_prove-23-30-pack-writer.md` | Thin DMS seed vs rich Cortex warehouse. |

## Routing (not a PRD widen)

No mega-epic. No tickets from this row. WIP already two: EPIC-020 #108 on #116, and EPIC-019 #38. Do not seat a third.

| Piece | Route |
|---|---|
| (a) WRONG-SEMANTIC total spend = revenue | **KEEP.** DMS parent **EPIC-019 #38** (L0/certified scalar spend). Cortex parent **Cortex#11** completeness (query_skills must not exact-bind spend to `revenue_total`). Not 017. Not 018. |
| (b) Envelope demote | **REFUSE** unless founder overrides (F40-style fail-closed). |
| (c) Harness spend!=revenue | **KEEP** as honesty gate under **EPIC-008 #8**. Not sufficient alone. |
| (d) Glossary / about / strong / OK | **PARK.** KEEP ABSTAIN. Unlock = steward VQ under #38. |
| (e) categoty | **KEEP OPEN #38.** No extra epic. |

## First irreversible slice this session

**None.** Epic-agent files only after the founder pick below.

Parent must not: patch `envelope.py` as the product fix; mint a glossary epic; reopen #33/#35; close #38; recycle `:8090`/`:8010`; treat 31/31 as precision-on-answered.

## NEEDS-YOU

Three files would contend if all three ship. Pick one product lane:

1. **Cortex skill/pack (recommended)** -- deactivate or refuse the captured `What is our total spend?` -> `revenue_total` skill; add certified/L1 scalar spend as inventory JOIN if that is the gold (same grain as `cq_spend_by_country`). Then optional #38 Studio VQ.
2. **DMS envelope demote** -- refuse unless you want a temporary F40-style fail-closed while Cortex is queued. Default no.
3. **Harness only** (`SCALAR_Q` must not equal outbound `revenue_myr`) -- honesty for 31/31, does not stop Chat.

Recommended pair: **1 then 3**. Not 2.

## Founder sentence

Spend-as-revenue is a Cortex query-skill exact bind of the CEO spend ask to outbound revenue, not an E9 miss; keep about/OK/strong as honest ABSTAIN and do not mint a glossary epic.
