# BENCH-MATRIX-01

**Status:** Not PASS. `tests/test_bench_matrix.py` 13 passed with `--noconftest` (this VM has no cortex-contract wheel, so `tests/conftest.py` cannot import). `--self-check` passed. Full suite not run. No live dataset. grade52 armed and pack H not run.

## What

`DMS_BENCH_MATRIX` defaults off. Only the digit `1` starts a live slice. Off, `main` returns before any ask, writes no artifact, and the observed prompt, envelope, and route stay empty. `--self-check` is allowed with the flag off: five synthetic SQLite questions, a stub ask, Chinese and Malay header re-ingest, and a mocked armed-process refusal.

The artifact records served correct, WRONG, reconfirm, abstain, gold errors, the Clopper-Pearson 95 percent upper bound on wrong-served (`score_heldout.wrong_upper_pct`), nearest-rank p50/p95 seconds, and OpenVault USD for the run label only. `pass` is false. `target` is null.

Question text is copied. Header translation is the operator's table and column map. Gold SQL is one read-only statement. Row values are not stored. An armed `grade52` or `ab_run.sh` refuses the start via `pgrep -f "[a]b_run.sh|[g]rade52.py --armed"`.

## Ceiling

ponytail: usage is one page (limit 1000) and percentiles are nearest-rank. A longer OpenVault ledger under-counts USD until a cursor is followed. Spider and BIRD files are not in the repo. The first live numbers are a baseline, not a pass.
