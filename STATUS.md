# STATUS.md - DMS

**Last updated:** 2026-08-27  
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
| Engine bench | 811 answerable over 494 shapes, **0 disagreements with an independent oracle**. Bound ~0.61% on shapes (R-0010) - **but the corpus is 3 variants of ONE schema family**: AdventureWorksLT2022 shares 9 of its 12 table names with AW2025, and DW2025 is the same fictional company as a star schema. Every declared key is correct **by construction** (110 PKs unique, 146 FKs no orphans), so the four failure classes that decide customer viability - FK on the wrong column, orphan rows, duplicate business key behind a clean surrogate, a column whose name lies - **cannot occur in it**. On the honest coarse unit (3 databases) the bound is 100%. Falsified: LEFT->INNER exits 1 |
| Free-form | **Not a measurement.** Quote "no recorded green run" until Cortex#11 closes the engine half of F40 (R-0011). DMS half is closed (#66): `map_ask_response_to_envelope` demotes `route=refused` even when badge is `session`. `repro_refused_badge.py` still prints P0 because it calls `build_answer_envelope` with no route |
| Bench in CI | **No.** Neither `ontology_bench.py` nor `verify_freeform_demo.py` runs in `ci_local.sh` or `try_changes.py`; `bench.json` is a 2026-08-23 snapshot nothing re-derives, so a regression in `scripts/ontology.py` turns nothing red |
| AW lake | 114 tables, 146 links held, 110 objects. Compiled revenue == oracle, conserves to 123.2M. **Never read by `apps/` or `packages/`** |
| Insights + brief | `insights.py` -> `brief.py`; `main()` reads the deck back before PASS (R-0001) |
| Local CI parity | `bash scripts/ci_local.sh all`; `python scripts/try_changes.py [--live]` - 41 checks, each states what it does *not* prove |
| **CSV-01 (#18)** | Download CSV: BOM + RFC 4180 + answer_id name; no clock/locale/model |
| **A-0007 CLOSED** (#72) | "Company (default ACL)" is a real scope, not a skipped check. `alerts` - granted by **no** Space - was served unscoped and refused under every named one; now refused under all. Enumeration oracle closed with it: missing and ungranted both answer 403 |
| **#73 + #74 CLOSED** | The boundary invariant classifies by what a route **reaches**, not by HTTP verb, and **ten** ungated data-revealing GETs are now gated (five were never in the reported list). No allowlist. A second test guards the guard - emptying the check's scope goes red |
| **F70 CLOSED** (#76,#92) | Caller cannot assert certification; after append, `sign_gold_metric` `verify_ledger`s before signed. **F52(b)** hash not entry_id |
| **#75 CLOSED** | An upload whose serving sync failed reported `ingested=N`. Three states on the receipt |
| **F40 DMS half (#66)** | `route=refused` does not ship as `L2_VALIDATED`. Engine half is Cortex#11 |
| **#28 ENV-E4 (#91)** | Listing shortfall no longer 500s; unciteable money abstains. qty×100 cannot launder invent |
| **#25 SPACE-UI (#90)** | Runs/Amend send `space_id`; Library/Studio clear on Space switch |
| **#23 RAG-04 (#94)** | Customer envelope asserts text, rows, sources on `POST /v1/chat/ask` |

## Open next

| ID | Work |
|----|------|
| **NEEDS-YOU** | F70 epic name. **F36** blocks EPIC-020->021. **F41** EPIC-021a. **F37** EPIC-024. **F68**. Constructor live-run needs a VM (`app.netie.ai/cortex` 404) |
| **This tick** | Landed #99 (E9-02, E12, health abort) and #102 (Playwright 10/10 demo stack). Cortex#11 F40 remains |
| **F71** | Re-ask of F39. No new epic. Do not merge Constructor with `scripts/ontology.py` |
| Epics | **EPIC-003 (#6) INCOMPLETE** + **EPIC-017 (#33)** in flight. **EPIC-018 QUEUED** - do not close |
| Truth to hold | Product served **91 rows**. One DuckDB writer excludes readers. No scale claim (P-DMS-34) |
| CI / PRs | No open product PRs. Closed superseded #97/#98/#101 |

## Agent models

PRD/epic/ticket/verify = Grok 4.5 high. Research/web = Composer 2.5.
