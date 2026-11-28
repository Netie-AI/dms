# Finding: GEN-01 ontology-grounded generative ask

Date: 2026-09-13
Keywords: GEN-01, retrieve, ontology, compute, execute-validate, abstain, A/B, dms-179
Main idea: "Retrieve schema+ontology via SQL-filter, summarize to short context, Cortex/bind plan, validate, submit. A/B vs exact-match on curated_ceo. Unsure ABSTAIN. Not pack expansion. Not EPIC-019 COMPLETE."

## What landed

- `semantic_retrieve.retrieve_short_context` (schema SQL + ontology slice + DISTINCT encodings + summarize).
- `maybe_generative_ask` sends that short context to Cortex `POST /dms/query`, compiles typed plans only, validate then submit.
- A/B: `scripts/score_curated.py --ab` exact-match pack vs retrieve+bind. WRONG=0 both required.
- OpenVault keys stay in Cortex. Empty api_key is not replaced. No DB-GPT clone.

## Not this ticket

EPIC-019 COMPLETE, pack expansion, greening planted refuses, reopen #108, ticket close, GEN-02 live harness.
