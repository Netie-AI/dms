# 2026-09-05 EPIC-025 chain read-back at the gold gate

Keywords: EPIC-025, ledger.verify, gold promote, is_signed, attestation, dms#87, R-0007

Main idea: `sign_gold_metric` already called Cortex `verify_ledger` after append. `_run_gold` still trusted `is_signed`, so a constructed signed metric (or a sign that verified then a later broken/unreachable chain) could promote. The gate now calls the same `POST /v1/contract/ledger/verify`. Contract 1.2.0 has no get-entry; chain verify recomputes payload hashes. Unreachable Cortex refuses gold. Attestation/actor surface re-derived by AST like #74.

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_gold_metric_cannot_be_forged.py tests/invariants/test_actor_trust_boundary.py tests/test_pipeline_promote.py tests/test_pipeline_receipts.py -q` | gate refuses missing/failed/unreachable verify; HTTP 4xx after sign-ok; invariant scans not vacuous |

Does not prove: live Cortex chain, per-entry get (not in cortex-contract 1.2.0), that a fabricated `ledger_entry_id` is on the chain when verify still returns ok=True.
