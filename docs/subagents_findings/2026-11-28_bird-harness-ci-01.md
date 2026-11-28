# BIRD-HARNESS-CI-01

**Status:** Not PASS. `tests/test_bird_bench.py` 14 passed with `--noconftest`. `python scripts/bench/bird/__main__.py self-check` passed. Full suite not run. The 2024-06 corpus was not downloaded. Stub grade52, flag off and `DMS_BIRD_HARNESS=1`: correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20. Rule-served did not grow. Armed and pack H not run.

## What

`DMS_BIRD_HARNESS` defaults off. It gates `scripts/bench/bird` only. Off, `run` sends no ask and writes no artifact. Prompt, envelope, and served route stay the empty triple. The ask path is unchanged.

Official EX is `set(predicted) == set(gold)` from bird-bench/mini_dev `evaluation_ex.calculate_ex`. Column order is the tuple order. Duplicate rows collapse. A normalised variant (case-fold, 6 decimal places) is reported beside it and does not decide CORRECT.

Outcomes are CORRECT, WRONG, ASKBACK, ABSTAIN, ERROR. EX is correct / n_total. An unresolved 402 or 429 is infra_unscored, stays in n_total, and marks the run INCOMPLETE. ex_scored_only is secondary and is not the headline. On 402 or 429 the dms arm retries `POST /v1/chat/ask` with role `escalate`. The graded body is the DMS envelope. A raw completion is not executed. One plain retry of the original POST is the last rung. The baseline arm still sends that role to OpenVault. AI versus rule uses `grade52.path_group`. p50 and p95 are nearest-rank.

The baseline arm sends OpenVault role `sql` with the schema and 3 sample rows. It does not send a provider or a model id. Gold runs read-only on the source file with no row cap and no statement timeout.

## Ceiling

The nightly job on the hosted runner exits 2 before download when the box URL is absent. A live Mini-Dev number is not this PR. The product extract still has its own row ceiling; gold does not use that copy.
