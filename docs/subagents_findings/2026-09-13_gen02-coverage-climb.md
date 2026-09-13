# Finding: GEN-02 live A/B + CRAG validate-or-abstain

Date: 2026-09-13
Keywords: GEN-02, ask_path, live A/B, CRAG, validate-or-abstain, a9578348, 3.8%, bind_plan, compute miss, dms-180
Main idea: "Isolated ask_path=exact vs generative on curated_ceo. Cortex compute miss binds retrieved ontology then validate. Baseline exact 10/26 gen 1/26 WRONG=0 @ a9578348. Not pack expand. Not COMPLETE."

## What landed

- `POST /v1/chat/ask` body `ask_path=product|exact|generative` (not x-dms, DR-0004).
- Platform: `python scripts/score_curated.py --climb --ab --url https://studio.netie.ai/api`.
- Cortex compute miss -> `bind_plan` from retrieved demo ontology, then compile/validate/submit. Explicit unsure not overridden.
- Thin-reseed ontology verifies (no Cortex-only storage_bin / shipment supplier_id claims).
- CRAG-style grades on gen envelopes. Ideas only. P-DMS-19 doc CRAG stays parked.

## Not this ticket

EPIC-019 COMPLETE, pack expansion, greening planted refuses, ticket close, secrets, DB-GPT clone, invent 99.95%, invent live Studio coverage from this seat.
