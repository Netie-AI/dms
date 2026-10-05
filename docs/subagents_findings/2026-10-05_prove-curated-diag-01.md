# PROVE-CURATED-DIAG-01 score questions missing from PACK_METRICS (dms#355)

Keywords: PROVE-CURATED-DIAG-01, cq_sku_count, PACK_METRICS, lookup_pack_metric, exact match, verified_queries, dms-355
Main idea: Prove exact-match died at step 1. The score fixture already has the certified SQL. The product registry did not. Grants for Finance inventory are not the miss. Not a VQ seed. Not a prove PASS.

## What failed

`maybe_pack_ask` -> `lookup_pack_metric` returns None when the question text is not in `PACK_METRICS`. On `4525b4c` that tuple is ten metrics. `cq_sku_count` is not one of them. `_verified_queries` is empty, so the rules lane misses first and the curated lane misses second. The ask then falls through to generative.

## What shipped

`load_score_pack_metrics` reads `tests/fixtures/curated_ceo` and appends `expect: l0` rows whose question text is new. Oracle `$as_of` is executed as `CURRENT_DATE` (the certified form; the scorer still binds `$as_of`). Refuse and abstain rows are not added. Same text keeps the first metric, so a Space that does not grant the tables still misses (Ops vs suppliers).

Not shipped: a verified-query seed, a grant write, a new key.
