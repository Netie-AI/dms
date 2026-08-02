# STATUS.md — DMS

**Last updated:** 2026-08-03  
**North star:** [DMS_TECHNICAL_ARCHITECTURE.md](DMS_TECHNICAL_ARCHITECTURE.md) §15  
**Remote:** https://github.com/Netie-AI/dms

## Rule

Sequence T3 → T7-at-ingest → T4 → T5/T6 → T8 → **T12 → T13**. Demo-ready =
`DMS_ASK_MODE=live` + `DMS_DEMO_FALLBACK=0` (both defaults). Launcher scripts are
ASCII-only; PowerShell 5.1 breaks on em-dash.

## Now

Shipped on `feat/grounding-promote-spaces-boundary`: Studio ingest carries
`space_id`; bronze list + grants are Space-scoped (#10/#12); live demo hides
offline Company fixtures (#11); PREVIEW-01/02/03 UI (#13-#15). Still open:
DEMO-PATH-01 (#16) follow-up half; EPIC-011 (Cortex#19-#22); #7 CI.

## Demo is green (verified live 2026-08-02; re-run after restart)

`python scripts/verify_demo_live.py` was **18/18** before this sweep; script now
also asserts Space-scoped ingest list + grant boundary + hidden fixtures
(**24 checks** when stack is up).

**Before the demo, restart the stack.** Cortex left running overnight returns 500
on submit.

Spaces run on the **in-process store** (`backend=memory`). Do not point
`DATABASE_URL` at compose Postgres for demo (test residue).

```powershell
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -StartUi -OpenBrowser
python D:\DMS\scripts\verify_demo_live.py
```

Stranger path (manual, <10 min): pick Finance Space -> Studio + upload xlsx ->
tick file -> Ask -> Library preview rows. Follow-up (`average of them`) blocked
on Cortex#19.

## Open

| ID | Blocker |
|----|---------|
| **#16** | DEMO-PATH-01 verifier follow-up asserts (Cortex#19) |
| **#8** | EPIC-008 completeness until #16 + stranger path green |
| **Cortex#19-#22** | Multi-turn follow-ups |
| **#7** | CI mypy / contract token |

Design: paper / navy / teal / Figtree+Fraunces.
