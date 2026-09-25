# A1-02 BIRD Mini-Dev harness (#264)

Keywords: A1-02, bird_minidev, gold SQL, GOLD_ERROR, FreeRoute learn, setup_fingerprint, dms-264
Main idea: "500 Mini-Dev questions loaded at run time, graded against gold SQL. No target. Learn-off + fresh route store for Cortex runs. ROUTER-1 served_provider/served_model/served_local copied or unknown. Fingerprints must match to compare."


## What landed

- `scripts/bird_minidev.py` plus `--minidev` / `--compare` on `scripts/score_bird.py`
- Verifier pins: `norm_cell` 29+ digits, `gold_error_dominating` both branches, `pg_gold_error` dead_connection, relative+absolute numeric tolerance
- Live Cortex scoring refuses unless `CORTEX_FREEROUTE_LEARN=0` and a fresh `CORTEX_ROUTE_STORE` can be snapshotted
- Per-answer `served_provider` / `served_model` / `served_local` (or `unknown`), served mix, setup fingerprint; compare refuses cross-setup unless forced

## Not this ticket

Live Mini-Dev score, accuracy target, BIRD corpus in git, `score_curated.py`, EPIC-A1 COMPLETE, Cortex ROUTER-1.
