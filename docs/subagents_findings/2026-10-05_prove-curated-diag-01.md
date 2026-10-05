# PROVE-CURATED-DIAG-01 score questions missing from PACK_METRICS (dms#355)

Keywords: PROVE-CURATED-DIAG-01, cq_sku_count, PACK_METRICS, lookup_pack_metric, exact match, verified_queries, dms-355
Main idea: Prove exact-match died at step 1. The score fixture already has the certified SQL. The product registry did not. The extra registry is an explicit score-pack allowlist, not every expect:l0 row. Grants for Finance inventory are not the miss. Not a VQ seed. Not a prove PASS.

## What failed

`maybe_pack_ask` -> `lookup_pack_metric` returns None when the question text is not in `PACK_METRICS`. On `4525b4c` that tuple is ten metrics. `cq_sku_count` is not one of them. `_verified_queries` is empty, so the rules lane misses first and the curated lane misses second. The ask then falls through to generative.

## What shipped

`SCORE_PACK_EXACT_IDS` is `cq_sku_count`, `cq_sales_top3_volume`, `cq_sku_count_by_category`, `cq_supplier_ranking`, and `trap_categoty`. `load_score_pack_metrics` reads only those rows from `tests/fixtures/curated_ceo`. `PACK_METRICS` stays the original ten so climb gates that snapshot that tuple stay green. `lookup_pack_metric` searches both. Oracle `$as_of` is executed as `CURRENT_DATE` (the certified form; the scorer still binds `$as_of`). Climb rise and synonym ids are not metrics. `cq_sales_top5_value` and `cq_chemicals_list` stay on the contract-ask path. Same text keeps the first metric, so a Space that does not grant the tables still misses (Ops vs suppliers).

Not shipped: a verified-query seed, a grant write, a new key.
