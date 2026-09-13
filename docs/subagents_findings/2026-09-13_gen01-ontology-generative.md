# Finding: GEN-01 ontology-grounded generative ask

Date: 2026-09-13
Keywords: GEN-01, ontology, compute, execute-validate, abstain, dms-179
Main idea: "Non-exact-match asks compile a typed ontology plan from Cortex POST /dms/query, validate, then submit. Unsure/validate-fail ABSTAIN. Not pack expansion. Not EPIC-019 COMPLETE."

## What landed

- `dms_executor.generative_ask.maybe_generative_ask` after VQ-04 refuse and bronze, before contract ask.
- SQL is only `Ontology.compile` output. Raw model SQL is not executed.
- Cortex compute is off-contract `POST /dms/query` (`cortex_client.compute`). OpenVault keys stay in Cortex. Empty api_key is not replaced.
- Tests: abstain-when-unsure, compile refuse, ungranted skip execute, L2 after validate, planted traps not WRONG, contract ask still runs when compute is absent.

## Not this ticket

EPIC-019 COMPLETE, pack expansion, greening planted refuses, reopen #108, ticket close, GEN-02 harness.
