# STATUS.md — DMS

**Last updated:** 2026-08-23  
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
| E9-01 (#34 CLOSED) | E9 on ask path + invent-totals tests |
| E9-02 (#41 CLOSED) | F32 ambiguous Sales vs Wide_Fill ranking demote |
| Demo | `verify_demo_live.py` **31/31** live, first run on a cold stack |
| #48 CLOSED | E10 — a grouped/ranked ask can't be settled by an ungrouped scalar |
| #43 CLOSED | cold-start timeout was rendered as 403; now 504+retryable. Launcher warms the answer path (measured 30-48s) |
| #44 CLOSED | protected-paths gate made deterministic; cause never found, ruled-out list in the issue |
| #58 | Cortex client timeout was a hardcoded 30s < the path it calls. Now `cortex_timeout_seconds`, default 120 |
| Free-form gate | `scripts/verify_freeform_demo.py` — oracle recomputed per run, conservation identities, must-abstain cases |
| AW lake | `.bak` restore → 114 tables parquet → `validate_lake.py` all 146 links held → `ontology.py --adventureworks` 110 objects verified. Proof: compiled revenue == oracle, conserves to 123.2M |
| Ontology | `scripts/ontology.py` — grain guard: measures fan-out, refuses ambiguous/unverified/unknown-column; multi-hop (3-link roll-ups), `via=` names role-playing hops, a blocked shorter route refuses rather than silently taking a longer clean one; m2m filters allowed and flagged `existential`. 51 tests |
| Engine bench | `scripts/ontology_bench.py` — 896 cases / **811 answerable over 494 distinct shapes, 0 wrong**, 3 AW dbs, 1/2/3-hop. Bound: ~0.37% on cases, **~0.61% on shapes** (R-0010). Deterministic layer only — NL understanding is the free-form gate (n=25, ~15.8%). 78 no-match probes counted apart as known-gap, not passes. Falsified: LEFT→INNER JOIN makes it exit 1 |
| Insights + brief | `scripts/insights.py` (declared measures, discovered dims, ranking over labelled same-signed values, every figure compiled+conserved, no model) → `scripts/brief.py` pptx/html; **main() reads the deck back** before PASS. `data/lake/_reports/aw_brief.pptx` |
| Lineage | `scripts/show_lineage.py` — row conservation bronze→silver; exits 1 on fan-out |
| Local CI parity | `bash scripts/ci_local.sh all` — CI's gates on Python 3.11 in Docker |
| Change harness | `python scripts/try_changes.py [--live]` — 41 checks, each states what it does *not* prove |

## Open next

| ID | Work |
|----|------|
| **NEEDS-YOU** | **Is F27 reversed?** On 2026-08-05 you declined "install as plugins inside MSSQL" and kept EPIC-020 extract-only. The 2026-08-07 ask ("appear in people's MS SQL Server") is the opposite. Extract-only keeps the Space boundary; live federation does not. Nothing SQL-Server-shaped starts until this is answered. **Also (PRD F41 / F45):** accept DR-0003 items 1-2 and split EPIC-021 -> EPIC-021a (grain-guarded compiler in the engine; `scripts/ontology.py` is the prototype), and decide insights + brief (thin deterministic epic vs the EPIC-022 precision gate). |
| **RUN NOW** | #57 FF-02 - a governed metric answered a *negated* question with its inverse (`NOT cold storage` -> `is_cold_storage = TRUE`), 4 vs 102,986 under L1_GOVERNED_METRIC. The only confidently-wrong answer in the gate. **Plus F40 P0** (EPIC-017 + Cortex#11): `python scripts/repro_refused_badge.py` exit 0 - a manifest refusal renders `L2_VALIDATED`, `abstained=false`; Cortex fix first, then the repro becomes a DMS envelope test. |
| #59 FF-03 | The L2 validation gate names why it refused the SQL and throws it away — `violations` never reaches `str(exc)`. Cortex-side; one line. Blocks diagnosing free-form coverage. |
| Free-form | Coverage is low and everything answered came from L0/L1, not L2. L2 generates SQL its own gate rejects — see #59 before concluding anything about the model. |
| #39 VQ-01 | Scoped, **blocked**: D:\Cortex has 47 files in flight (R-0006). Verified truth + SQL + synonyms in the #39 comment. Earlier numbers there were corrected — the first set was a 15x fan-out. |
| Epics | In flight: #33 EPIC-017 (#57, #59, F40) + #35 EPIC-018 (CI accuracy gate, promote-on-truth - F42/F46). Queued: #38 EPIC-019 (F32; value dictionary + pinned plans F46). PRD ledger F40-F48 routes the engine-side accuracy work; EPIC-020 stays blocked on F36. |
| L4/L5 | Aspiration only (P-DMS-33) — **NEEDS-YOU** confirm meanings or decline badges |
| #28 ENV-E4 | reorder/low-stock E4; parent #8 |
| EPIC-016 #29 | Excel Copilot — likely wrong-priority if the direction shifts; park not close |

## Agent models

PRD/epic/ticket/verify = Grok 4.5 high. Research/web = Composer 2.5.
