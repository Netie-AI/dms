# Finding: GEN-02 live A/B + CRAG validate-or-abstain

Date: 2026-09-13
Keywords: GEN-02, ask_path, live A/B, CRAG, validate-or-abstain, a9578348, 3.8%, bind_plan, compute miss, distill, dms-180
Main idea: "Isolated ask_path=exact vs generative. Distill: certified-first, ontology spine, hybrid_fuse+CRAG, Cortex text2sql. Bind on compute miss only for generative. Baseline exact 10/26 gen 1/26 WRONG=0 @ a9578348. Not pack expand. Not COMPLETE."

## What landed

- `POST /v1/chat/ask` body `ask_path=product|exact|generative` (not x-dms, DR-0004).
- Platform: `python scripts/score_curated.py --climb --ab --url https://studio.netie.ai/api`.
- Distill mapping (ideas only, no vendor paste): certified-first; `demo_ontology` spine (not a new YAML pack); retrieve `hybrid_fuse` + CRAG grades; Cortex `POST /dms/query` + `bind_plan` slots (no text2sql SDK).
- Isolated gen Cortex compute miss -> `bind_plan`, then compile/validate/submit. Bind miss is ABSTAIN after retrieve (try-harder), not silent None. Product path does not bind on miss. Explicit unsure not overridden.
- Thin-reseed ontology verifies. Slot-name YAML `tests/fixtures/curated_ceo/ontology_spine.yaml` (no SQL). P-DMS-19 doc CRAG stays parked.

## Not this ticket

EPIC-019 COMPLETE, pack expansion, greening planted refuses, ticket close, secrets, DB-GPT/mybot/n8n/OpenWillow paste, invent 99.95%, invent live Studio coverage from this seat.
