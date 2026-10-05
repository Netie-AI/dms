---
keywords: [CCA-GEO-FLAKE-01, test_cca_geo, SELECT DISTINCT, threads, order, duckdb, dms-349, dms-257]
main_idea: "test_cca_geo's exact ('MY','SG','TH') assert follows DuckDB's unordered parallel DISTINCT. threads=8 returned ('SG','TH','MY') on 72/80 calls. Not timezone, not network, not a cross-test file race. Test now checks membership. Product ORDER BY stays post-baseline. Not COMPLETE."
models: [grok-4.7]
workflow: ticket-runner
reuse: golden_rule
status: raw
cite: agent: cca-geo-flake-01
repo: DMS
date: 2026-10-05
---

# CCA-GEO-FLAKE-01: test_cca_geo order race

PREFLIGHT: PARTIAL (cca-certifies-spelling-not-sql, parallel-browse-duckdb-attach). No prior note names this flake.

## Golden rule

> Do not assert `BinderResult.values` tuple order. `binder._distinct_values` is `SELECT DISTINCT ... LIMIT` with no `ORDER BY`. DuckDB's parallel hash aggregate permutes that stream. Membership is the contract. A product `ORDER BY` is post-baseline (#349).

## Class

**Order**, from one query's parallel merge. Not timezone. Not network. Not a race between tests.

| Candidate | Evidence |
|-----------|----------|
| Order | `packages/executor/dms_executor/cca/binder.py` `_distinct_values` has no `ORDER BY`. `BinderResult.values` walks `matched` in the order distinct rows arrived. |
| Race (inside that query) | duckdb 1.5.6, same 5-row iso2 file, `SET threads`. threads=1: one order, `('MY','SG','TH','Japan','Australia')`. threads=4 (this machine's default, same width as ubuntu-latest): 2 distinct orders; `unmatched_sample` flipped `('Japan','Australia')` 161/200 vs `('Australia','Japan')` 39/200 inside `bind_geo`. Matched triple stayed `('MY','SG','TH')` on those 200. threads=8: 6 distinct orders; `bind_geo` values were `('SG','TH','MY')` on 72/80 and `('MY','SG','TH')` on 8/80. `assert res.values == ("MY", "SG", "TH")` fails the 72. |
| Timezone | No clock, `datetime`, or `pytz` on this path. Country codes do not depend on `TZ`. |
| Network | Fixture is a `tmp_path` duckdb file. No socket, no Cortex, no HTTP. A failure is an assertion, not a timeout. |
| Cross-test file race | Each fixture builds its own file and closes the writer before `bind_geo` opens it read-only. CI pytest is one process, no xdist. The permutation happens on a single connection. |

`res.absent` is pack insertion order (`_SEA_MEMBERS`), so the "Brunei" / "and 2 more" sentence does not move when the scan does. `PYTHONHASHSEED` does not govern either side: dict order here is insertion order, and the flake is DuckDB's aggregate, which has no seed.

## What this change does

`tests/test_cca_geo.py` compares filter membership and prints the tuple it actually got. It does not set `threads=1` (that would hide the race and invent a stable order). It does not add `ORDER BY` to the product scan.

## Not claimed

Not a green scoreboard. Not COMPLETE. Does not close #349 or #257. Merge only after #337.
