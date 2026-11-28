# A1-02 BIRD Mini-Dev harness (#264)

Keywords: A1-02, bird_minidev, gold SQL, GOLD_ERROR, FreeRoute learn, served_local, setup_fingerprint, plan_origin, dms-264
Main idea: "500 Mini-Dev questions loaded at run time, graded against gold SQL. No target. Learn-off + fresh route store for Cortex runs. Envelope setup fields copied or unknown. plan_origin generate_sql vs ontology_ranking counted separately. Fingerprints must match to compare."

## What landed

- `scripts/bird_minidev.py` plus `--minidev` / `--compare` on `scripts/score_bird.py`
- Verifier pins: `norm_cell` 29+ digits, `gold_error_dominating` both branches, `pg_gold_error` dead_connection, relative+absolute numeric tolerance
- Live Cortex scoring refuses unless `CORTEX_FREEROUTE_LEARN=0` and a fresh `CORTEX_ROUTE_STORE` can be snapshotted
- Six envelope setup fields copied verbatim (`served_provider`, `served_model`, `served_local`, `learn_enabled`, `learn_source`, `route_store_id`) or `unknown`; never inferred. `plan_origin` counted separately. Fingerprint includes those setup fields; compare refuses cross-setup unless forced

## Not this ticket

Live Mini-Dev score, accuracy target, BIRD corpus in git, `score_curated.py`, EPIC-A1 COMPLETE.
