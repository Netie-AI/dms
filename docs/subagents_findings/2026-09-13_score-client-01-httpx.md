# Finding: SCORE-CLIENT-01 climb/A/B httpx vs urllib CF1010

Date: 2026-09-13
Keywords: SCORE-CLIENT-01, score_curated, climb, httpx, urllib, CF1010, studio.netie.ai, dms-187
Main idea: "GEN-02 --climb/--climb --ab probed /health with urllib and CF1010ed studio.netie.ai. score_http is httpx. CF1010 is BLOCKED not IAP/grant. Live remeasure is Platform. Not COMPLETE."

## What landed

- `scripts/score_curated.py`: `score_http` (httpx.request) for health probe and POST /v1/chat/ask.
- CF1010 named BLOCKED (Platform DevOps exception if httpx still 1010). Not grant-ABSTAIN.
- Tests: `tests/test_score_climb.py`, `tests/test_score_curated.py`. Runbook: `scripts/score_climb.md`.

## Not this ticket

#178 COMPLETE, live Studio coverage numbers, greening planted refuses, GitHub CI `--climb`, ticket close, keys in scripts.
