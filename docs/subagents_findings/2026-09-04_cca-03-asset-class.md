# 2026-09-04 CCA-03 asset-class binder

Keywords: CCA-03, asset_class, commercial, residential, encodings, landed dim, dms#135

Main idea: Default commercial/residential encodings are empty. CERTIFY only when injected encodings sit on a landed dim. Missing encoding or dim miss abstains and cascade_path will not ship L0. Does not invent class membership. Cortex pack rewrite HELD.

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_asset_class_binder.py tests/test_sense_binder.py tests/test_constraint_cascade.py -q` | missing encoding abstain + certify-on-dim + no L0 |

Does not prove: live_ask cascade (CCA-05), SEA pack (CCA-04), eval corpus (CCA-06).
