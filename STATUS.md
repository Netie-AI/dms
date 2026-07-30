# STATUS.md — DMS

**Last updated:** 2026-07-30  
**North star:** [DMS_TECHNICAL_ARCHITECTURE.md](DMS_TECHNICAL_ARCHITECTURE.md) §15  
**Remote:** https://github.com/Netie-AI/dms

## Rule

Architecture sequence T3 → T7-at-ingest → T4 → T5/T6 → T8 → **T12 → T13**.  
`demo_ask` = offline/fallback only (`DMS_ASK_MODE=demo` or live fail + `DMS_DEMO_FALLBACK=1`).

## Progress

| ID | Status |
|----|--------|
| T0–T2 | done |
| C3/C4-min | done (Cortex) |
| T3 Postgres Spaces + seed + migrate-on-boot | done (memory fallback if no DB) |
| Live ask default + smoke script | done |
| T7 bronze `_src[]` + `_ingest_id` + Studio | done (upgraded to Appendix A array) |
| T4 amend HTTP + F5 HTTP gate call-through | done (soft when gate catalog miss) |
| T5 lite actor headers | done |
| T6 Caddy-only compose | done |
| T8 Library/Studio/Amend/Audit UI | done (Runs/Admin still stub; UI pages may be uncommitted locally) |
| **T12 promote pipelines** | **done** — `a74ec80` |
| **T13 ingest triage** | **done** — `ba744e6` |
| T14 signed answer receipts + verify | next (plan only) |
| T13b Repair Desk | after T13 fingerprints (plan only) |
| T15 contribution rollup + export | after lineage + preferably T14 |
| T9–T11 external/multi-pool/MinIO | demand-gated |
| C6 | Cortex kickoff packet — after live smoke |

## Commits this session

- `a74ec80` `feat(pipelines): contract-gated bronze→silver promotion with quarantine.`
- `ba744e6` `feat(ingest): sheet triage classifier and honest ingest receipts.`

## Try

```powershell
cd D:\DMS
$env:DMS_ASK_MODE="live"; $env:DMS_DEMO_FALLBACK="1"
python -m uvicorn dms_api.app:app --app-dir apps/api --reload --port 8090

# Stress gates
python -m pytest -q --tb=line
python -c "from importlinter.cli import lint_imports; raise SystemExit(lint_imports())"
python scripts/smoke_live_ask.py
```

## Design

Paper / navy / teal / Figtree+Fraunces.
