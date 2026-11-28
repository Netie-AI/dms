# Accuracy levers on the AI floor

PREFLIGHT: PARTIAL. SQL-vote agreement is on file. Value match (#596) and feedback (#604) are open drafts, not merged.

`DMS_SELF_CONSISTENCY`, `DMS_VALUE_LINK`, and `DMS_LEARNED_MEMORY` are off unless the value is the digit 1. They run inside `run_floor`. Off, the floor loop is the same harness.

Self-consistency samples three diverse prompts only after a sanity miss or `ambiguous: true`. Temperature is on the catalog and stripped before the Insights POST, because Cortex 422s an unknown field. A majority is the SQL-vote bar. No majority clarifies. `SELECT 3` does not agree.

Value link profiles distinct text and numeric ranges once per database file and calls PR #596 `match_prompt`. The block is `<values>`. Person columns are not profiled.

Learned memory stores SQL only when the confirmer is the asker and the question plus intent match the governed answer. A Yes on a generic reading stores nothing. Retrieval is `<examples>`. An exact question is re-executed. Stored rows are not kept.

Scripted delta (`scripts/verify/accuracy_levers_delta.py`): control wrong, each lever correct, generic Yes refused. Not a live Claude call. Not PASS.
