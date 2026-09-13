# Finding: GEN-02 measured live coverage climb harness

Date: 2026-09-13
Keywords: GEN-02, score_curated, climb, WRONG=0, 91c5cc99, studio.netie.ai, dms-180
Main idea: "--climb scores live curated_ceo against studio.netie.ai/api with real OK/LAYER/ABSTAIN/WRONG vs frozen 91c5cc99 OK7 LAYER10 ABSTAIN9 WRONG0. Route split exact vs generative. No invent 99.95/COMPLETE. Not pack expansion."

## What landed

- `python scripts/score_curated.py --climb --url https://studio.netie.ai/api`
- Fail closed: no laptop default; CONFIG/BLOCKED/FAIL distinct from a 26-WRONG invent.
- Baseline frozen @ `91c5cc99`. `answered_by_path` attributes LAYER/OK to `generated` vs pack routes.
- Offline `--ab` unchanged (GEN-01). CI still `--self-check` / pytest only; `--climb` is not a GitHub job.

## Not this ticket

EPIC-019 COMPLETE, pack expansion, greening planted refuses, ticket close, secrets in chat, weakening CI.
