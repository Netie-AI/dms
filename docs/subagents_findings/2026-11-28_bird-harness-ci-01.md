# BIRD-HARNESS-CI-01

**Status:** Not PASS. `tests/test_bird_bench.py` 12 passed with `--noconftest`. `python scripts/bench/bird/__main__.py self-check` passed. Full suite not run. The 2024-06 corpus was not downloaded. Stub grade52 at `8683ea24`, flag off and `DMS_BIRD_HARNESS=1`: correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20. Armed and pack H not run.

## What

`DMS_BIRD_HARNESS` defaults off. It gates `scripts/bench/bird` only. Off, `run` sends no ask and writes no artifact. Prompt, envelope, and served route stay the empty triple. The ask path is unchanged.

Official EX is `set(predicted) == set(gold)` from bird-bench/mini_dev `evaluation_ex.calculate_ex`. Column order is the tuple order. Duplicate rows collapse. A normalised variant (case-fold, 6 decimal places) is reported beside it and does not decide CORRECT.

Outcomes are CORRECT, WRONG, ASKBACK, ABSTAIN, ERROR. 402 and 429 are contaminated, retried on the next run, and left out of the denominator. AI versus rule uses `grade52.path_group`. p50 and p95 are nearest-rank.

The baseline arm sends OpenVault role `sql` with the schema and 3 sample rows. It does not send a provider or a model id. Gold runs read-only on the source file with no row cap and no statement timeout.

## Ceiling

The nightly job on the hosted runner exits 2 before download when the box URL is absent. A live Mini-Dev number is not this PR. The product extract still has its own row ceiling; gold does not use that copy.
