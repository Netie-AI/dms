# VQ-02 Studio register (Space-scoped verified Q→SQL)

Keywords: VQ-02, Studio, verified-queries, L0_CERTIFIED, space isolation, compliance_gate
Main idea: Persist steward Q→SQL in DuckDB `main._verified_queries` per Space; live_ask hits that store before Cortex; foreign Space misses. Do not rewrite global pack YAML.

- Store is lake-local like `_promote_receipts`, not Postgres (Studio works without DATABASE_URL).
- Match is exact normalize + synonyms on the asset. Pack match stays VQ-01 / Cortex#125.
- GET list requires `space_id` (A-0007: omitting must not mean every Space).
