# SCORE-BIRD-ROWS-01 score_bird judges rows against gold SQL (dms#300)

Keywords: SCORE-BIRD-ROWS-01, score_bird, oracle-db, gold_sql, NO_ORACLE, ORACLE_ERROR, multiset, judge_case, dms-300, dms-292, dms-264
Main idea: `score_bird.py --live/--ab --oracle-db <duckdb>` runs each pack case's `gold_sql` read-only and judges the answer rows against it with the dms#292/#299 judge (`judge_envelope_detailed`, imported). Without `--oracle-db`, or on a case with no `gold_sql`, an answer case is NO_ORACLE: no OK count, no answered count, no bound, no PASS line, exit 3. Gold SQL that errors is ORACLE_ERROR and fails the run. Refuse traps stay badge-judged. Not COMPLETE.

## Expected vs actual (parent `87a9497`)

- Expected: a confident answer whose rows differ from the gold rows is WRONG.
- Actual: `live()` judged the 9-case pack by badge only. A green answer with a doubled row (`[{"n":3},{"n":3}]` for "How many rows are in the gender table?") printed `PASS: WRONG=0 on both paths`, rc=0.

Repro: monkeypatch `score_bird._ask_live` to return that envelope and `fetch_bronze` to `["bronze.public_gender"]`, then call `live(url, 1.0, BIRD_SPACE)`. Parent: rc 0 and PASS. This branch: rc 3, `VERDICT: NOT SCORED. NO_ORACLE=6`. With `--oracle-db` on a seeded DuckDB: rc 1, `WRONG rows_mismatch:count=2/1`.

## Root-cause class

The verdict came from an intermediate artifact (the badge), not the returned rows. Same class as CLAUDE.md rule 10. The badge-only path was the default, so absence of an oracle became a green.

## Invariant

"Assert the artifact the user actually receives": the scorer now reads the rows. "Silent fallback is a lie": no oracle is printed as NO_ORACLE and exits 3.

## Ceiling

The committed pack (`tests/fixtures/bird_minidev/questions.yaml`) has no `gold_sql` yet. Out of scope here (#300 allows only `score_bird.py` plus tests). So a live `--oracle-db` run labels the 3 gender cases NO_ORACLE until gold SQL is added to the pack. The Mini-Dev path (`--minidev`, #264) already grades against gold SQL on the Space source and is unchanged. Oracle DB is DuckDB only (`run_oracle_select`). A LAYER from the l0 judge maps to OK: for BIRD a confident row match is OK, as before.
