# Finding: GEN-02 live A/B + CRAG validate-or-abstain

Date: 2026-09-13
Keywords: GEN-02, ask_path, live A/B, CRAG, validate-or-abstain, a9578348, 3.8%, dms-180
Main idea: "Isolated ask_path=exact vs generative on curated_ceo. CRAG grades validate-or-abstain. Baseline exact 10/26 gen 1/26 WRONG=0 @ a9578348. Demo ontology retrieve, not pack expand. Not COMPLETE."

## What landed

- `POST /v1/chat/ask` body `ask_path=product|exact|generative` (not x-dms, DR-0004).
- Platform: `python scripts/score_curated.py --climb --ab --url https://studio.netie.ai/api`.
- Offline `--ab` now demo_ontology retrieve+bind. Planted refuses stay ABSTAIN (how-full, delayed, stock-by-bin).
- CRAG-style grades on gen envelopes. Ideas only. P-DMS-19 doc CRAG stays parked.

## Not this ticket

EPIC-019 COMPLETE, pack expansion, greening planted refuses, ticket close, secrets, DB-GPT clone, invent 99.95%.
