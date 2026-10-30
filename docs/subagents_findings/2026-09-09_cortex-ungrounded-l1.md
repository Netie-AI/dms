---
keywords: [Cortex, Genie, live_ask, tools_only, L1, L2, DEMO_TOOLS, F89]
main_idea: Extra metric tools no longer block Cortex on ungrounded Finance asks. Object-ground stays tools-only. Live spend -> Cortex L0 certified query. top 11 -> L1_GOVERNED_METRIC. L2 hazmat honest abstain (Cortex process L2 flag). Did not kill :8010.
---

# Cortex ungrounded L1 path (2026-09-09)

PREFLIGHT: HIT prd-cortex-genie-l1l2.

## Change

`live_ask` only calls `tools_only_abstain` when `grounded_tables` includes `objects.*`. Extra `_metric_tools` no longer skip `_cortex.ask`.

Plant: `tests/test_extract_object_ask.py` ungrounded Finance free-text now hits MarkerCortex. Object-ground miss still ABSTAIN tools-only.

## Live :8090 after recycle (left :8010)

- VQ "Top 5 selling SKUs by revenue" Finance -> L0_CERTIFIED verified_query 5 rows (demo lake).
- "What is our total spend by supplier country?" -> L0_CERTIFIED route=sql, Cortex certified query `cq_spend_by_country`, 4 countries. Chat assumption "live Cortex ask".
- "top 11 selling SKUs by revenue" -> L1_GOVERNED_METRIC 11 rows.
- Object-ground invent-join -> ABSTAIN tools-only (`sales_sku_metric`).
- `verify_l2_vs_l1.py`: top11 L1 ok; hazmat honest abstain; categoty still abstain on this Cortex pack (VQ-01 remainder). Do not restart :8010 to flip `DMS_L2_ENABLED`.

## Not this week

#116 no SQL host. #132 flag 0. #29 #8 Cortex#11. L4/L5 PARK. Week-ship-all REFUSE.
