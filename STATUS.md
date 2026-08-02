# STATUS.md — DMS

**Last updated:** 2026-08-02  
**North star:** [DMS_TECHNICAL_ARCHITECTURE.md](DMS_TECHNICAL_ARCHITECTURE.md) §15  
**Remote:** https://github.com/Netie-AI/dms

## Rule

Architecture sequence T3 → T7-at-ingest → T4 → T5/T6 → T8 → **T12 → T13**.  
Demo-ready = `DMS_ASK_MODE=live` + `DMS_DEMO_FALLBACK=0` (both are the defaults).

Desktop: `scripts\windows\Start-DMS.bat` (ASCII-only; PowerShell 5.1 breaks on em-dash).

## Demo is green (verified live 2026-08-02)

`python scripts/verify_demo_live.py` → **18/18** against DMS + Cortex + OpenVault.

| Check | Result |
|-------|--------|
| xlsx into a fresh warehouse | receipt names the table and the true row count |
| grounding on one upload | manifest holds exactly that table; UI count matches |
| ungrantable selection | refused by name, never widened |
| "total stock value by category" | answers in **both** Spaces, `L0_CERTIFIED` |
| "total spend by supplier country" | answers in **Finance**, abstains in **Warehouse Ops** |
| demo fallback | off everywhere; no answer is a fallback |

**Before the demo, restart the stack.** A Cortex left running overnight returns
500 on submit; every certified question fails until it is restarted.

```powershell
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -StartUi -OpenBrowser
python D:\DMS\scripts\verify_demo_live.py
```

## Closed this session

| ID | What |
|----|------|
| **#4** P0-DEMO-01 | receipt no longer denies rows that landed; swap+record is one transaction |
| **#2** ACL-01 | Space boundary holds on the serving path per DR-0002; second leak found and fixed (bound session id ignored Space) |
| **#5** P0-DEMO-03 | manifest minted from the selection; refuses rather than widens |
| Demo Spaces | renamed to DR-0002 `Finance` / `Warehouse Ops` |
| Postgres | host-reachable — `Start-DMSStack` names the hostdb overlay; 18 control-plane tests run instead of erroring |

Corpus: **188 passed**, 0 xfail. Ruff clean on `apps packages tests`.

## Open

| ID | Blocker |
|----|---------|
| **#3** CI-02 | `CORTEX_CONTRACT_TOKEN` is set but still 404s — the PAT cannot see `Netie-AI/Cortex`. Check it is a **fine-grained token with `Netie-AI` as resource owner**, `Contents: read`, `Netie-AI/Cortex` explicitly selected, org approval granted, not expired. |
| P-DMS-26 | an uploaded table is grantable from any Space (ingest carries no `space_id`) |
| P-DMS-27 | 19 mypy errors + 1 broken import-linter contract, both pre-existing and hidden by CI-02 |
| C7-full / C7-prod | schema retrieval + FreeRoute SQL gen |
| C10 | claim_n 47 → 310 via `verify_gold --review` |

## Design

Paper / navy / teal / Figtree+Fraunces.
