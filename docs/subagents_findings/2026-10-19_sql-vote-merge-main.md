# SQL-VOTE merge of main 6daf59e

Keywords: SQL-VOTE, dms-463, 6daf59e, reconfirm, written_names, value index, route stamp, kept_stamp, grade52, stub-exec, insights_call_cap

Main idea: PR #463 head merges main `6daf59e` (#464 reconfirm-with-Yes, #458 armed grader, #450 value index, #441 CAST written_names, #439 route stamps, #428 kept_stamp). Equal-row agreement, the one call cap, and build_abstain stay. Prove pin `279cbd85` was not edited. Held-out n=348 was not run.

Measured on the merge, not PASS. `pytest tests` with `DMS_SKIP_CONTROL_PLANE_TESTS=1` and `DMS_DEMO_FALLBACK=0`: 3540 passed, 41 skipped, 33 xfailed, 0 failed. `grade52.py --main` stub-exec: correct=43 wrong=0 reconfirm=5 abstain=0 refusal_ok=3 refusal_wrong=0 gold_broken=1, split ai=0 rule=43 unattributed=0 abstain=9 n=52. ai=0 means the AI lane was not measured.
