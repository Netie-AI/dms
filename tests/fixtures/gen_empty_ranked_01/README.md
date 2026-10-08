# GEN-EMPTY-RANKED-01 replay fixtures

Byte copy of run 2's `oracles.json`, plus the 16 generated L2 rows that matched it.

- source run: `mr-20261008-1931-38afd6bd`
- served at: `38afd6bd`
- `oracles.json` sha256: `008f5bb1b8ac775a677501937a9cd8c444682986cf8008a04beb9fd27a162a12`
- score run id: `d119f2d04039409681ac88b974c829fb`
- score commit: `38afd6bdd28255c296cb909ab42a2739b994a39a`

`replay_cases.json` is the 16 `route=generated`, `badge=L2_VALIDATED`, `matches_oracle=true` rows from that run's `per_case.csv` and `score_cases` jsonl.

The 8 counted ontology-ranking corrects are the binder three plus the empty five. Two further ontology-ranking matches were not counted. Six matches were `generate_sql`.

Set compare, not row order: `cq_sku_count_by_category_per` ("how many SKUs per category"). `sku_count` ties at 2 for RAW, PARTS, and PACKAGING.
