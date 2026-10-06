# STATUS.md - DMS

**Last updated:** 2026-10-06  
**Remote:** https://github.com/Netie-AI/dms

## Direct interact

```powershell
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -EnableL2 -StartUi -OpenBrowser
python D:\DMS\scripts\verify_demo_live.py
python D:\DMS\scripts\verify_l2_vs_l1.py
python D:\DMS\scripts\score_answers.py --docs D:\DMS\tests\fixtures\hostile_score --oracle-only
python D:\DMS\scripts\walk_buyer_studio.py --self-check
pytest D:\DMS\tests\test_answer_oracle.py D:\DMS\tests\invariants -q
python D:\DMS\scripts\ontology_bench.py      # 896 cases, 494 shapes
```

Demo + AirGPT dual flow: `docs/DEMO_RUNBOOK.md` (**read section 0 first**; prove IAP = section 2.1, origin `https://studio.netie.ai`, `:8090` loopback) - AirGPT MAX: `D:\AirGPT\tests\RAG\DEMO_RAG.md` (`python clipdrop.py` -> :8765)

## Shipped / verified

Archived: [docs/archive/2026-10-06_STATUS_shipped-and-open-next.md](docs/archive/2026-10-06_STATUS_shipped-and-open-next.md) (DOCS-01, [#371](https://github.com/Netie-AI/dms/issues/371)). Closed work lives on its issue.

## Truth to hold

- Product served **91 rows**. One DuckDB writer excludes readers. No scale claim (P-DMS-34)
- Demo: `verify_demo_live.py` 31/31 on a cold stack; bounds error ~3/31, not zero (R-0010)
- Engine bench is 3 variants of ONE schema family; on the honest coarse unit (3 databases) the bound is 100% - [#256](https://github.com/Netie-AI/dms/issues/256), [#265](https://github.com/Netie-AI/dms/issues/265)
- Free-form: **not a measurement**. Quote "no recorded green run" until [Cortex#11](https://github.com/Netie-AI/Cortex/issues/11) closes the engine half of F40 (R-0011)
- CCA ask-path hook ships OFF (`DMS_CCA_CASCADE=0`) until measured on a real question log - [#132](https://github.com/Netie-AI/dms/issues/132)

## Open next

GitHub issues are the source of truth. This table holds links only.

| Item | Issue |
|------|-------|
| STUDIO-RESULT-01 - Studio renders the ask envelope, UI only (payload [#364](https://github.com/Netie-AI/dms/issues/364)) | [#365](https://github.com/Netie-AI/dms/issues/365) |
| PROVE-SUBMIT-01 - live BLOCKED, vault / OV mint 401 | [#359](https://github.com/Netie-AI/dms/issues/359), [#362](https://github.com/Netie-AI/dms/issues/362), [#363](https://github.com/Netie-AI/dms/pull/363) |
| ORACLE-FIX-01 | [#301](https://github.com/Netie-AI/dms/issues/301) |
| EPIC-020b | [#173](https://github.com/Netie-AI/dms/issues/173) |
| F73 accuracy remainder - EPIC-019 | [#38](https://github.com/Netie-AI/dms/issues/38) |
| F73 delivery (gated) - EPIC-016 | [#29](https://github.com/Netie-AI/dms/issues/29) |
| SCALE-WAREHOUSE-01 - do not reseat | [#237](https://github.com/Netie-AI/dms/issues/237) |
| **NEEDS-YOU: issue not yet filed** (drafts in the DOCS-01 PR) | F41 EPIC-021a; F68 monetization; `app.netie.ai/cortex` 404; F73 surface cream/graphite; `verify_freeform_demo --self-check` not in CI; live BI connector after EXPORT-02 |

## Agent models
PRD/epic/ticket/verify = Grok 4.5 high. Research/web = Composer 2.5.
