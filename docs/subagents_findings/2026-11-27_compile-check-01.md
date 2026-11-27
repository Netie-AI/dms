# COMPILE-CHECK-01

**Status:** Not PASS. Unit file `tests/test_compile_check_01.py` 4 passed. Nearby ask-path files 176 passed. Stub `grade52 --main` before `f286c4a` and after the checker: correct=43 wrong=0 reconfirm=5, paths ai=0 rule=43 L1=23 compile=20, stamp_contradiction=0. Pack H not measured. Armed grade52 not run.

PREFLIGHT: PARTIAL. No COMPILE-CHECK-01 row. Closest: WRONG-TRAP-01 (compile abstains when the question does not name the measure), AI-SQL-SHIFT, CHECKER-FANOUT shadow, ONTO-DERIVE-01 join rule on model SQL.

## What

Compile serves (rank window, multi-grain, one-grain) called `validate_compiled_sql` and `_submit_validated`, and skipped the extract loop's checker. Model SQL on a Space already ran `unverified_join_reason` before EXPLAIN. Compile did not, so a fan-out join could execute and come back L2.

`ai_lane_sql_check` is that checker, one function. The extract loop calls it. Compile calls it before submit. A pass still serves. A non-direct miss is not submitted. The question goes to `_run_extract_loop` or `_hand_compile_to_ai` (one generate with the rejected SQL and the checker reason, then the loop). Schema repair and escalate stay behind their existing flags. A ladder miss abstains with the checker reason and empty rows. Ungranted, hostile, and non-SELECT stay direct abstains.

Demo asks keep `space_onto` None, so the join rule does not run, same as the AI lane. That is why the stub 52 did not move.

## Not a word rule

No new needle, no per-question lane, no new flag. The join rule is the existing allowlist of verified links.
