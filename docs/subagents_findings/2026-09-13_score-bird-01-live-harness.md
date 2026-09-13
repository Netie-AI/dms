# Finding: SCORE-BIRD-01 measured live harness (bounded gender)

Date: 2026-09-13
Keywords: SCORE-BIRD-01, bird_minidev, gender, leftover 75, GEN-01 A/B, dms-184, EPIC-020b
Main idea: "Live OK/LAYER/ABSTAIN/WRONG on BIRD Space f0da7dd3. Attach is gender max_rows=50. 75-table extract leftover. A/B exact-miss vs GEN-01 live_ask. No 99.95. Not COMPLETE."

## What landed

- `scripts/score_bird.py` + `scripts/score_bird.md` + pack `tests/fixtures/bird_minidev/questions.yaml`
- `--self-check` CI-safe. `--live` requires `DMS_API_BASE` (no 127.0.0.1:8090 default). `--ab` without live is BLOCKED for generative.
- Honesty: source_count=1, data_source 12b6f170, leftover 75 tables. WRONG=0 law. Precision n/a when 0 answered.

## Not this ticket

EPIC-020b COMPLETE, reopen #108 COMPLETE, GEN-02 curated climb, 99.95%, DB-GPT clone, ticket close, live counts from a cloud seat.
