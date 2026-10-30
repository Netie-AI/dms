---
keywords: [L1, L2, categoty, cq_top3_category_sales, spend-as-revenue, cq_spend_total, verify_l2_vs_l1, DMS_L2_ENABLED]
main_idea: Recycled :8010 onto patched D:\\Cortex with match-certified-before-unknown, DISTINCT categoty YAML, cq_spend_total, skill refuse spend->revenue, L2 flag on. Live categoty L0_CERTIFIED. Scalar spend is inventory JOIN 643.0M not outbound revenue. verify_l2_vs_l1 exit 2 (L0/L1 green, hazmat honest L2 NO_CANDIDATE).
---

# L1/L2 categoty + spend (2026-09-13)

PREFLIGHT: PARTIAL. Reused `2026-09-10_categoty-incomplete.md`, `2026-09-11_prd-spend-as-revenue.md`, `2026-09-09_prd-cortex-genie-l1l2.md`.

Founder asked continue build and solve L1/L2. That authorized Cortex recycle. Did not kill `:8090` or `:5000`. Did not start Docker. Did not seat Cortex#104.

## What shipped (this checkout)

`D:\\Cortex` was serving unknown-noun before certified match and lacked `cq_top3_category_sales`. origin/main already had both. Applied on the live tree (not the dirty crew files):

1. `CortexOS/dms/answer_engine.py` -- `match_certified` then `undefined_subject`.
2. `packs/dms/semantic/certified_queries.yaml` -- `cq_top3_category_sales` (DISTINCT sku,category) + `cq_spend_total` (inventory JOIN suppliers).
3. `packs/dms/semantic/query_skills.py` -- `spend_bound_to_revenue` skip/capture refuse.
4. DMS `tests/test_live_ask.py` extra-tools still reach Cortex (F89). `scripts/verify_l2_vs_l1.py` oracle snapshot + one 503 retry.

## Live Chat after recycle (`DMS_L2_ENABLED=1` on :8010)

| Ask | Badge | Notes |
|-----|-------|-------|
| Top 5 selling SKUs by revenue | L0_CERTIFIED | First post-recycle POST can 503; retry 200 |
| show top 3 categoty sales | L0_CERTIFIED | FOOD_DRY 9.091M / ELECTRONICS 9.056M / PPE 8.697M |
| spend by supplier country | L0_CERTIFIED | MY 448.8M ... sum 643.0M |
| What is our total spend? | L0_CERTIFIED | `total_spend_myr=642969499.25` not `revenue_myr=80.8M` |
| top 11 | L1_GOVERNED_METRIC | 11 rows |
| hazmat on-time | ABSTAIN | L2 ran; gate NO_CANDIDATE |

Independent oracle on a warehouse copy: DISTINCT ranks match Chat; sum DISTINCT = OUT 80,787,598.30; mixed sku/category = 0; naive lot JOIN ~14.8x. Old harness oracle ELECTRONICS/CHEMICALS/FOOD_COLD was a prior lake build.

`python scripts/verify_l2_vs_l1.py` **exit 2**. l0_l1_fails empty. hazmat honest abstain. Exit 0 needs generated L2 SQL for hazmat (FreeRoute / Cortex#104, not this tick).

## Tests

- Cortex `tests/dms/test_spend_not_revenue.py` 7 passed (no DuckDB).
- DMS `test_extra_metric_tools_do_not_block_ungrounded_cortex` 1 passed.
- Skip-CP pack not re-run (0.5 GB free RAM).

## Not claimed

EPIC-019 COMPLETE. F83 still mints lake VQ L0 with `cortex.asks==[]`. `verify_l2_vs_l1` exit 0. Glossary/about/OK. Envelope demote. Cortex#11 closed. UI Chat on :5174 was ATE Control, not DMS -- envelope verified via HTTP.
