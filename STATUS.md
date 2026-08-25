# STATUS.md - DMS

**Last updated:** 2026-08-25  
**Remote:** https://github.com/Netie-AI/dms

## Direct interact

```powershell
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -EnableL2 -StartUi -OpenBrowser
python D:\DMS\scripts\verify_demo_live.py
python D:\DMS\scripts\verify_l2_vs_l1.py
python D:\DMS\scripts\score_answers.py --docs D:\DMS\tests\fixtures\hostile_score --oracle-only
pytest D:\DMS\tests\test_answer_oracle.py D:\DMS\tests\invariants -q
python D:\DMS\scripts\ontology_bench.py      # 896 cases, 494 shapes
python D:\DMS\scripts\gen_playground_data.py # then playground_ask.py --list
```

Demo + AirGPT dual flow: `docs/DEMO_RUNBOOK.md` (**read section 0 before any demo**)  
AirGPT MAX: `D:\AirGPT\tests\RAG\DEMO_RAG.md` (`python clipdrop.py` -> :8765)

## Shipped / verified

| ID | Result |
|----|--------|
| Demo | `verify_demo_live.py` **31/31** live on a cold stack. Bounds error at ~3/31, not zero (R-0010) |
| Envelope | E9 invent-totals, E10 grouped-ask, E11 negation (#57 CLOSED) - all asserted on the customer envelope |
| **A-0005 CLOSED** (#70) | The ledger actor is resolved server-side. `sign_gold_metric` requires it, with no fallback to caller data, so `/gold/sign` and `/run` both close at the binding (R-0004) |
| **DR-0004 accepted** (#71) | **Option A** - identity from config, never a request. `x-dms-*` headers are **refused** with 400, not ignored. 7 invariants; 4 go red against a pass-through (R-0007) |
| Predictive (#67) | A literal-list guard certified 4 forecast asks with historical numbers under `L2_VALIDATED`. Now intent-based. KB **F-0021** |
| Ontology (#68) | `scripts/ontology.py` grain guard - refuses fan-out, ambiguous and unverified roll-ups; multi-hop; `via=`; a blocked short route refuses rather than silently taking a longer one |
| Engine bench | 896 cases / **811 answerable over 494 shapes, 0 wrong**. Bound **~0.61% on shapes** (R-0010). Deterministic layer only; NL is the free-form gate (n=25, ~15.8%). Falsified: LEFT->INNER exits 1 |
| AW lake | 114 tables, 146 links held, 110 objects. Compiled revenue == oracle, conserves to 123.2M. **Never read by `apps/` or `packages/`** |
| Insights + brief | `insights.py` -> `brief.py`; `main()` reads the deck back before PASS (R-0001) |
| Local CI parity | `bash scripts/ci_local.sh all`; `python scripts/try_changes.py [--live]` - 41 checks, each states what it does *not* prove |
| **CSV-01 (#18)** | Download CSV: BOM + RFC 4180 + answer_id name; no clock/locale/model |

## Open next

| ID | Work |
|----|------|
| **NEEDS-YOU** | **F70 / #76** is filed **unparented** - open EPIC-025 or re-parent under EPIC-003; it may not be worked until you name a parent. **F36** (open since 2026-08-07) blocks EPIC-020 -> EPIC-021. **F41** split EPIC-021a. **F45** insights/brief epic. **F37** approve EPIC-024 (highest nearness - it renders data already computed). **F68** monetization has zero prior PRD coverage. **P-DMS-33** L4/L5 badge meanings. |
| **P0 #72** | `space_id` omitted skips the Space check on both Library preview routes. **Exploit run:** unscoped preview returns 15 rows of `transactions` and 5 of `shipments`; the same ask *with* a Space returns 403. Trap: the UI sends `space_id` only `if (spaceId)` (R-0005) |
| **P0 #73** | The boundary invariant sets `MUTATING_METHODS` to the four write verbs, so it inspects no GET - green on the surface that leaks. Protected path; needs `INVARIANT-CHANGE` and must be proven able to fail |
| **#74** | Re-derive every data-revealing GET. Two of five previously reported are fine (`space_id` required -> 422); five others were never named |
| **#76 F70** | A caller can forge a steward-signed gold metric: `is_signed` is `bool(signature and steward_id and signed_at)`, all three settable on `/v1/pipelines/run`, nothing verified against the chain. Distinct from F50 - that wrote a false name *into* the ledger; this bypasses it |
| **#75** | An upload whose serving sync failed still reports `ingested=N` (R-0011). Happens whenever Cortex holds the DuckDB file, which is always |
| **RUN NOW** | **#59 FF-03** Cortex-side one-liner: the L2 gate throws away its own `violations`. Blocks diagnosing free-form. **F40 P0**: `repro_refused_badge.py` exit 0 - a refusal renders `L2_VALIDATED` |
| Epics | In flight: **EPIC-003 (#6)** + **EPIC-017 (#33)**. **EPIC-018 (#35) -> QUEUED** (all three tickets closed; it held a slot with nothing seated; never close it - that orphans F42/F46). EPIC-003 verdict re-derived from code: **INCOMPLETE** |
| Truth to hold | **The product has served 91 rows.** Concurrency breaks at that size - one DuckDB writer excludes all readers. No scale claim may be made (P-DMS-34) |
| PRs open | #64, #65 (Cursor lane) both **CONFLICTING** with main after the merge wave; #65 is EPIC-020 work, blocked on F36 |
| #39 VQ-01 | Scoped, **blocked**: D:\Cortex has 47 files in flight (R-0006). Verified truth in the #39 comment |

## Agent models

PRD/epic/ticket/verify = Grok 4.5 high. Research/web = Composer 2.5.
