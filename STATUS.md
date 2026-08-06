# STATUS.md — DMS

**Last updated:** 2026-08-05 afternoon  
**Remote:** https://github.com/Netie-AI/dms

## Direct interact

```powershell
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -EnableL2 -StartUi -OpenBrowser
python D:\DMS\scripts\verify_demo_live.py
python D:\DMS\scripts\verify_l2_vs_l1.py
# Hostile break-test (oracle no stack; live expects RED on synonym/empty-filter/Malay/RAG-sum)
python D:\DMS\scripts\score_answers.py --docs D:\DMS\tests\fixtures\hostile_score --oracle-only
pytest D:\DMS\tests\test_answer_oracle.py D:\DMS\tests\invariants\test_envelope.py -q
# Playground (tweak prompts in playground/my_questions.yaml)
python D:\DMS\scripts\gen_playground_data.py
python D:\DMS\scripts\playground_ask.py --list
# python D:\DMS\scripts\playground_ask.py --space <id>   # after upload playground/data/
```

Demo + AirGPT dual flow: `docs/DEMO_RUNBOOK.md`  
AirGPT MAX: `D:\AirGPT\tests\RAG\DEMO_RAG.md` (`python clipdrop.py` -> :8765)

## Shipped / verified

| ID | Result |
|----|--------|
| RAG-04 (#23 CLOSED) | envelope + SourcePanel/scope chip |
| RAG-05 (#22 CLOSED) | ask adversarial green (8 CP + 1 unit) |
| REVEAL-01 (#26 CLOSED) | `/v1/library/reveal` + SourcePanel Open original |
| Dense embed | lexical+char-trigram in `search_chunks` |
| SPACE-UI (#25) | ScopeChip live count; SpacesPage sources |
| E9-01 (#34 CLOSED) | E9 on ask path + invent-totals tests |
| E9-02 (#41 CLOSED) | F32 ambiguous Sales vs Wide_Fill ranking demote |
| Playground | `playground/` + `playground_ask.py` — edit `my_questions.yaml`; L4/L5 labels only (P-DMS-33) |
| SCORE-03 (#42 CLOSED) | F32 ambiguous + blank-row pack cases; falsified R-0007 |
| Demo | `verify_demo_live.py` **31/31** live (2026-08-06) — but see #43 |
| Local CI parity | `bash scripts/ci_local.sh all` — CI's gates on Python 3.11 in Docker |
| Change harness | `python scripts/try_changes.py [--live]` — 41 checks, each states what it does *not* prove |

## Open next

| ID | Work |
|----|------|
| **RUN NOW** | VQ-01 #39 — scoped, **blocked**: D:\Cortex has 22 files in flight on another lane (R-0006). Engine match already done; gap is one missing certified asset joining transactions→inventory. Truth + SQL + synonyms in the #39 comment. |
| #43 DEMO-COLD-01 | **first** run after cold start refuses fresh upload (403); warm runs pass — **NEEDS-YOU** PRD routing + permission to delete `data/dms_demo.duckdb` to confirm cause |
| #44 CI-03 | `protected-paths` flaked: claimed a trailer missing that its own log printed. Green on re-run. Cause unknown; not patched on purpose |
| Epics | #33 EPIC-017 **N**; #35 EPIC-018 **N**; #38 EPIC-019 **N** (F32) |
| L4/L5 | Aspiration only (P-DMS-33) — **NEEDS-YOU** confirm meanings or decline badges |
| After | EPIC-019 after 017/018; not EPIC-023 first |
| F31 | Chooser = Cortex P21; ask = linear verify + hard-rule-12 empty demote |
| #27 DUAL-EVAL-01 | optional Top-5/edge vs AirGPT (no merge) |
| #28 ENV-E4 | **post-demo** - reorder/low-stock E4; parent #8 |
| Cortex#34 | EPIC-015 PARTIAL; RAG-01..03 open |
| EPIC-016 #29 | Demo-2 Excel Copilot; **build later** |

## Agent models

PRD/epic/ticket/verify = Grok 4.5 high. Research/web = Composer 2.5.
