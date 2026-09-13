# 2026-09-13 curated_ceo pack expand 14 -> 26 (SCORE-PACK-01 #168)

Keywords: curated_ceo, L0, certified_queries, refuse, SCORE-PACK-01, dms-168, EPIC-008 incomplete
Main idea: SCORE-PACK-01 #168. Expand the Genie walkthrough pack to 26. L0 only where Cortex certified SQL exists. Refuse without that SQL. Not parented under EPIC-008 (#8). Not COMPLETE. Does not close tickets.

## Source

Cortex `packs/dms/semantic/certified_queries.yaml` (19 certified). Golden TARGET
`delayed_incoming_per_wh` and paraphrase `how full is each warehouse` are not
certified synonyms. DR-0002: Finance = locations/inventory/transactions/suppliers;
Ops = locations/inventory/shipments; alerts granted by none.

## Honest split

L0: cold storage, capacity>90, expired, chemicals, supplier rank, CCTV WH-A
plus the prior 10 L0 (including trap_categoty synonym).
Refuse: alerts, high-risk pending, Ops supplier rank, delayed count, stock by bin,
how-full synonym. Keep last-month / short paraphrase / Ops spend boundary.

Does not prove: live `--live` against :8090. Does not close tickets.
