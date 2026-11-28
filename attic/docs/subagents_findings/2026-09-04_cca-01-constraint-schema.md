# 2026-09-04 CCA-01 constraint schema

Keywords: CCA-01, constraint, cascade, stage-trace, CERTIFIED, ABSTAIN, envelope, EPIC-CCA, dms#133

Main idea: Typed constraint objects (`constraint_id`, `type`, `candidate`, `binding`, `evidence`, `status`, `reasons`) and envelope `constraint_trace`. `parse_trace` fail-closed on missing schema. Later stages cannot be CERTIFIED unless priors are. `build_answer_envelope(..., cascade_path=True)` refuses before L0 when schema is missing. Does not invent SEA/class encodings. Orchestrator before L0 is CCA-05.

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_constraint_cascade.py -q` | schema + gating + envelope refuse |

`require_certified_priors` is the stage-order check. Do not name it `gate_trace`: `tests/invariants/test_boundaries.py` bans `def gate_*` outside `packages/cortex_client`.

Does not prove: sense/geo binders (CCA-02..04), live cascade before L0 (CCA-05), eval corpus (CCA-06).
