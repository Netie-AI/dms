# 2026-09-13 curated_ceo pack expand 14 -> 26

Keywords: curated_ceo, L0, certified_queries, refuse, Decision GO, EPIC-008 incomplete
Main idea: Expand the Genie walkthrough pack to 26. L0 only where Cortex certified SQL exists. Founder topics without that SQL (delayed count, storage bin, how-full synonym) and ungranted/cross-grant Cortex assets (alerts, high-risk pending) expect refuse. Not EPIC-008 COMPLETE.

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
