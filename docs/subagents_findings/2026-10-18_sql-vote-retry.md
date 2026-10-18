# SQL-VOTE: the second reading retries through the same checker

Keywords: SQL-VOTE, second reading, prompt hash, validate_compiled_sql, reconfirm, equivalent SQL, held-out 348, stub, dms-421

Main idea: "The second SQL reading uses the extract loop's error-fed retry and validate_compiled_sql. Equivalent SQL agrees only when the prompt hashes differ. A split is a reconfirm, not a served number. The held-out n=348 split was not run. Not PASS."

## Base

DMS #421 (`cursor/grant-struct-01`, GRANT-STRUCT-01) is open. This change is branched from that head (`8f5ce7f`), not from main.

## What the code does

1. A model SQL that runs with rows is a candidate. A second reading asks the same model with `reading=2` prepended, so the prompt hash differs.
2. The second reading calls `run_model_loop` with `fresh_budget=True`: its own count against `insights_call_cap` and `MAX_SQL_RETRIES`. Every attempt, including a retry, goes through the same `check`, which calls `validate_compiled_sql`.
3. Agreement is equal result rows from those two prompts. Normalized identical SQL is included. The same prompt is not a second reading.
4. The customer submit and the ledger wait until the readings agree. A split returns `reconfirm` (`why`, `closest_question`, `reconfirm_yes=yes`) and does not serve the rows.

## Measure

`scripts/score_sql_vote.py --self-check` scores a four-row fixture: correct 2, wrong 1, reconfirm 1, precision 0.667, reconfirm rate 0.250, recovered `q2`, new wrong `q3`, both sign-test p-values 1. That fixture is not the held-out split.

The held-out BIRD split (n=348) was not run. This environment has no Mini-Dev databases and no model route. No question-level comparison against main. Not PASS.

## Not claimed

WRONG=0 is not shown. The prior Cortex finding (120 wrong to 34, 166 correct to 99, 21 second-reading failures, 49 identical-SQL abstentions) is that engine's measurement, not a number from this tree.
