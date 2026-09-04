# 2026-09-04 CCA-05 cascade orchestrator

Keywords: CCA-05, live_ask, cascade_path, before L0, SEA commercial rental, dms#137

Main idea: `run_constraint_cascade` on `live_ask` after verified-query hit, before bronze/Cortex. No cue (SKU/revenue) skips cascade. Mid-stage ABSTAIN returns envelope and does not call Cortex. Happy path needs injected landed packs; default empty packs abstain (do not invent SEA/class). Grain/ontology not fake-CERTIFIED.

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_cascade_orchestrator.py tests/test_live_ask.py tests/test_geo_binder.py tests/test_asset_class_binder.py tests/test_sense_binder.py tests/test_constraint_cascade.py -q` | skip ordinary ask; SEA rental abstain no Cortex; injected packs L0+trace |

Does not prove: eval corpus (CCA-06), live MSSQL+MySQL, C7-05 serve L2, grain/ontology cascade stages.
