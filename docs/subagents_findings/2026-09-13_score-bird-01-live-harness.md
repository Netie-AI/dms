# Finding: SCORE-BIRD-01 measured live harness (batch bronze)

Date: 2026-09-13
Keywords: SCORE-BIRD-01, bird_minidev, gender, leftover 75, batch bronze, GEN-01 A/B, dms-184, EPIC-020b
Main idea: "Live OK/LAYER/ABSTAIN/WRONG on BIRD Space f0da7dd3. Baseline gender. Bronze grows in batches. Leftover target 75. Landed leftover traps SKIP. No 99.95. Not COMPLETE."

## What landed

- `scripts/score_bird.py` + `scripts/score_bird.md` + pack `tests/fixtures/bird_minidev/questions.yaml`
- `--self-check` CI-safe. `--live` lists Studio bronze; prints measured/leftover. `--ab` without live is BLOCKED for generative.
- Growing `attached_tables` is allowed. Leftover traps with `needs_table` SKIP once landed (no invented oracle). `trap_75_tables` stays refuse.

## Not this ticket

EPIC-020b COMPLETE, reopen #108 COMPLETE, GEN-02 curated climb, 99.95%, DB-GPT clone, ticket close, live counts from a cloud seat, Mini-Dev coverage from a partial batch.
