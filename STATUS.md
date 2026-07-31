# STATUS.md — DMS

**Last updated:** 2026-07-31  
**North star:** [DMS_TECHNICAL_ARCHITECTURE.md](DMS_TECHNICAL_ARCHITECTURE.md) §15  
**Remote:** https://github.com/Netie-AI/dms

## Rule

Architecture sequence T3 → T7-at-ingest → T4 → T5/T6 → T8 → **T12 → T13**.  
`demo_ask` = offline only (`DMS_ASK_MODE=demo`). **`DMS_DEMO_FALLBACK` defaults off**; if on, UI shows a permanent banner. Demo-ready = `DMS_ASK_MODE=live` + `DMS_DEMO_FALLBACK=0`.

Desktop: `scripts\windows\Start-DMS.bat` (ASCII-only; PowerShell 5.1 breaks on em-dash/ellipsis).

## Contract bridge

| Check | Result |
|-------|--------|
| Vendored OpenAPI | `openapi-1.2.0.json` sha256 matches Cortex |
| pyproject pin | `cortex-contract>=1.2.0,<2` + **real wheel** install |
| Wheel | `D:\Cortex\scripts\windows\Build-CortexContractWheel.ps1` → `cortex_contract-1.2.0-py3-none-any.whl` |
| CI | builds wheel from sparse checkout (no editable) |
| Secrets | `D:\NetieSecrets\Cortex.env.local` |

## This session (2026-07-31)

| Item | Status |
|------|--------|
| Start-DMSStack.ps1 parse error | **Fixed** — ASCII-only (em-dash was the `}` parser failure) |
| **C7-min** | **Shipped** — `sql_validate_gate.py` + submit EXPLAIN + L2 stub; tests green |
| **C10** | **Harness + 11 cats** — `bench/adversarial.py` + gated CI; paraphrases grown; value_normalization golden |
| **P-DMS-24** | **Closed** — DuckLake orphan snapshots; recipe `docs/POWERBI_DUCKLAKE.md` |
| **P-DMS-25** | **Closed locally** — wheel build + release.yml asset + DMS pin/CI/README |

## Progress

| ID | Status |
|----|--------|
| T0–T8, T12/T13 | done / done+ |
| C3/C4-min | done |
| **C7-min** | **done** (EXPLAIN gate; L2 model still abstains — correct) |
| **C8** | **done on Cortex** (2026-07-31) — `data/engine/query_run.db`; DMS Runs UI still needs Postgres |
| **C7-full / C7-prod** | open — schema retrieval + FreeRoute SQL gen + product hardening |
| **C10** | **in progress** — adversarial harness live; claim_n 47 → 310 via `verify_gold --review` |
| T14 | next after C7-prod / claim floor |

## Near-term (aligned with Cortex `DMS_ANCHORED_SEQUENCE.md`)

```
YOU     push when ready (explicit paths)
        │
        ├─► C7-prod   schema gate + envelope asserts
        ├─► claim_n   verify_gold --review toward 310
        ├─► Postgres  host-reachable → Amend → Spaces persist
        └─► Engine H-depth queued AFTER DMS floor (ontology/Act/Distill)
```

Hardware: **T1** default. Demo gates G1–G13 in prior STATUS section still apply.

## Try

```powershell
D:\DMS\scripts\windows\Start-DMS.bat
# or:
powershell -NoProfile -ExecutionPolicy Bypass -File D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -StartUi -OpenBrowser
```

## Design

Paper / navy / teal / Figtree+Fraunces.
