# SQL-VOTE: the second reading is a plan, then SQL

Keywords: SQL-VOTE, plan-then-solve, prompt substance, pre_run_refusal, explain_fail, empty_rows, validate_compiled_sql, held-out 348, stub, dms-463

Main idea: "A reading=2 suffix is not a second prompt. Reading 2 writes a plan of tables, joins, measure, and filters, then SQL from that plan. The second reading retries a pre-run refusal, an EXPLAIN failure, and an empty result through validate_compiled_sql. The held-out n=348 split was not run. Not PASS."

## Base

DMS #421 (`cursor/grant-struct-01`, GRANT-STRUCT-01) is open. This change is branched from that head, not from main. PR #463.

## What the code does

1. Reading 1 asks for one SELECT. Reading 2 first asks for a JSON plan from the ontology already on the ask context, then for one SELECT that follows that plan. Agreement requires those instruction templates to differ. A short prefix or suffix does not agree.
2. The insights body has `model_preference=free+normal` and no temperature or seed field. No second model is configured.
3. The second reading uses `run_model_loop` with `fresh_budget` and `recover_failures`. A reply with no SQL, a statement refused before execute, an EXPLAIN failure, and an empty result are fed back. The next SQL still goes through `validate_compiled_sql`. Hostile SQL is not pasted into the retry.
4. The customer submit and the ledger wait until the readings agree. A split returns `reconfirm` and does not serve the rows.

## Measure

`scripts/score_sql_vote.py --self-check` scores a four-row movement fixture and a 21-row recovery fixture: 10 `pre_run_refusal`, 7 `explain_fail`, 4 `empty_rows`, each `before_retry=failed` and `after_retry=recovered`. That fixture is not the held-out split.

`scripts/verify/sql_vote_heldout.py --live` is the n=348 comparison against main. It does not start unless `BIRD_MINIDEV_DIR`, `BIRD_HELD_OUT`, `DMS_API_BASE`, `DMS_API_BASE_MAIN`, `CORTEX_FREEROUTE_LEARN=0`, and an empty `CORTEX_ROUTE_STORE` are set. They are not set here. Not PASS.

## Not claimed

WRONG=0 is not shown. The prior Cortex finding (120 wrong to 34, 166 correct to 99, 21 second-reading failures, 49 identical-SQL abstentions) is that engine's measurement, not a number from this tree.
