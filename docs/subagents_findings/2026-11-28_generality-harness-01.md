# GENERALITY-HARNESS-01

**Status:** Not PASS. `tests/test_generality.py` passed with `--noconftest` (11). The offline slice wrote a scoreboard from recorded fixtures. Full packs were not loaded. No provider call. Not PASS.

## What

`scripts/verify/generality.py` is the one scoreboard. It calls `scripts/fde/eval_pack.py` to execute gold SQL and compare result sets, and `scripts/bench/bird/baseline.py` for the plain one-shot control. It does not keep a second comparison.

Rows are DMS under each digit-1 flag profile, plus `control`. rule-served is a failure even when the rows match. A stored alternate reading is `correct_alt` (not a pass, not wrong). Unsafe SQL is not executed.

`DMS_GENERALITY` defaults off. Only the digit 1 runs the live job. CI runs the recorded slice on every PR and uploads `generality.json` and `generality.md`. The slice says `mode=offline-slice` and is not a DMS measurement. The live job is `workflow_dispatch` with a hard budget cap. A missing cost stops the run. A remote server's flags cannot be flipped per request, so live profile rows stay `not_run` and the one DMS row is `dms:server`.

`scripts/bench` on this branch is the tree already on main, so the control row imports `baseline_body` instead of a second prompt. PR #612 does not contain that tree.

## Ceiling

ponytail: live flag profiles are `not_run` because one server process cannot change flags per row. Upgrade: a local process per profile, still the same grader, still under the same cap.
