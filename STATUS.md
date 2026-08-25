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
| **A-0007 CLOSED** (#72) | "Company (default ACL)" is a real scope, not a skipped check. `alerts` - granted by **no** Space - was served unscoped and refused under every named one; now refused under all. Enumeration oracle closed with it: missing and ungranted both answer 403 |
| **#73 + #74 CLOSED** | The boundary invariant classifies by what a route **reaches**, not by HTTP verb, and **ten** ungated data-revealing GETs are now gated (five were never in the reported list). No allowlist. A second test guards the guard - emptying the check's scope goes red |
| **F70 CLOSED** (#76) | A caller could assert its own certification: `is_signed` was three request fields, so `/v1/pipelines/run` passed the gold gate with nothing on the chain. Attestation is now refused on the request and produced server-side. **F52(b)** with it - `entry_hash` does not exist on the response, so the signature had degraded to the entry id |
| **#75 CLOSED** | An upload whose serving sync failed reported `ingested=N`. Three states now on the receipt, and "not attempted" is distinct from ok |

## Open next

| ID | Work |
|----|------|
| **NEEDS-YOU** | **F70** was fixed unparented on founder direction - still name its epic (EPIC-025, or under EPIC-003). **F36** (open since 2026-08-07) blocks EPIC-020 -> EPIC-021. **F41** split EPIC-021a. **F45** insights/brief epic. **F37** approve EPIC-024 (highest nearness - it renders data already computed). **F68** monetization has zero prior PRD coverage. **P-DMS-33** L4/L5 badge meanings. |
| **Not verified** | Every P0 fix this wave was proven **offline**. `verify_demo_live.py` (31/31) and `sync_bronze_to_serving.py --check` need a live stack and were **not run**, so no R-0005 check against the running product exists for any of them |
| **Still not closed** | **Nothing verifies a signature.** F70 stops an attestation being *asserted*; no read-back against the Cortex chain exists, so an entry removed or never durably committed still presents as signed. Also: a gold promote now requires a reachable Cortex |
| **Lane collisions** | Two agent lanes worked this queue in parallel and collided **three times**; #75 was fully duplicated and closed as superseded (#84). Claim a ticket by comment before starting - KB **F-0025** |
| **RUN NOW** | **#59 FF-03** Cortex-side one-liner: the L2 gate throws away its own `violations`. Blocks diagnosing free-form. **F40 P0**: `repro_refused_badge.py` exit 0 - a refusal renders `L2_VALIDATED` |
| Epics | In flight: **EPIC-003 (#6)** + **EPIC-017 (#33)**. **EPIC-018 (#35) -> QUEUED** (all three tickets closed; it held a slot with nothing seated; never close it - that orphans F42/F46). EPIC-003 verdict re-derived from code: **INCOMPLETE** |
| Truth to hold | **The product has served 91 rows.** Concurrency breaks at that size - one DuckDB writer excludes all readers. No scale claim may be made (P-DMS-34) |
| PRs open | none. #64 and #65 (Cursor lane) merged; the P0 wave merged as #78 #79 #80 #81 #82 #85 |
| #39 VQ-01 | Scoped, **blocked**: D:\Cortex has 47 files in flight (R-0006). Verified truth in the #39 comment |

## Agent models

PRD/epic/ticket/verify = Grok 4.5 high. Research/web = Composer 2.5.
