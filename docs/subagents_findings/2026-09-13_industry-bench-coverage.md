---
keywords: [industry-bench, warehouse, banking, insurance, storage, curated, L0, ontology, scale]
main_idea: Offline industry ontology benches 4/4 (warehouse 19, banking 10, insurance 7 with 3-hop, storage 10), 0 compiler disagreements. Curated pack 15->26 with L0 SQL backing. Live stack DOWN; support probe 1.48 GB. Cortex lake verify PASS, fan-out 16x refused, ranks FOOD_DRY/ELECTRONICS/PPE.
---

# Industry benches + curated coverage (2026-09-13)

PREFLIGHT: PARTIAL. Reused host-full-rerun, l1l2-categoty-spend, NEXT_COMPANY_ONTOLOGY_SCALE, F93/F96/F98. Suite skip-CP skipped (RAM). Did not start Docker/OV/DMS.

## What shipped

1. scripts/industry_ontology_bench.py + tests/test_industry_ontology_bench.py -- fixture lakes, not packs/insurance.
2. Curated CEO pack 26 cases (was 15). Every expect=l0 has Cortex certified SQL or a lake VQ.
3. Cortex cq_delayed_count, cq_stock_by_bin, capacity synonym how full are the warehouses.
4. VQ seed extras (expired / cold storage / stock by bin) skip on the thin 15-row DMS lake (missing columns).

## Benches (oracle vs compiler)

| Family | pass | fail | shapes | hops |
|--------|------|------|--------|------|
| warehouse | 19 | 0 | 14 | 1 and 2 |
| banking | 10 | 0 | 8 | 1 and 2 |
| insurance | 7 | 0 | 6 | 1, 2, 3 |
| storage | 10 | 0 | 4 | 1 and 2 |

CLI: PASS 4/4 families, 0 compiler disagreements. Lying insurance claim key fails verify. Storage lot grouped by zone conserves.

## Warehouse scrape (Cortex dms_demo.duckdb, read_only)

7433 inventory lots, 20 locations, 12000 shipments, 5001 txns, 200 suppliers. ontology.py --demo PASS. txn_of_lot FAN-OUT 16x refused. Allowed category ranks FOOD_DRY 9.091M / ELECTRONICS 9.056M / PPE 8.697M.

DMS data/dms_demo.duckdb is the thin seed (inventory 7, no category/country/storage_bin). Do not seed Cortex-grain SQL onto it.

## Tests

- DMS targeted: 15 passed (industry + curated + seed).
- Cortex test_spend_not_revenue 8 passed; Q1 load/execute/tables 3 passed (new CQs run).
- Oracle-only exit 0.
- Live score_curated --live / verify_demo_live not run.

## Support probe (2026-09-13 18:46)

SUITE: support DOWN elapsed_s 14.2. :5000/:8010/:8011/:8090/:8040 all WinError 10061. Ask POSTs skipped. Free RAM 1.48 / 13.67 GB. Do not start peers at this free RAM.

## Not claimed

EPIC-019 COMPLETE. verify_l2_vs_l1 exit 0. 100% coverage. Best DBMS. Banking/insurance production packs. Skip-CP 831/0.
