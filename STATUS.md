# STATUS.md — DMS

**Last updated:** 2026-08-22  
**Remote:** https://github.com/Netie-AI/dms

## Direct interact

```powershell
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -EnableL2 -StartUi -OpenBrowser
python D:\DMS\scripts\verify_demo_live.py
python D:\DMS\scripts\verify_freeform_demo.py
pytest D:\DMS\tests\invariants\test_envelope.py D:\DMS\tests\test_space_acl_boundary.py D:\DMS\tests\test_compliance_gate.py -q
```

Demo runbook: `docs/DEMO_RUNBOOK.md`

## Shipped / verified

| ID | Result |
|----|--------|
| #57 E11/FF-02 | Negated ask + inverse SQL (`NOT cold storage` -> `is_cold_storage=TRUE`) demotes to ABSTAIN. Tests go red if polarity is dropped. Correct `NOT` SQL and positive asks still certify. |
| #2 ACL | Live ask already mints DR-0002 grants: revenue answers in Finance, abstains in Warehouse Ops; predicate key sets differ (`transactions` vs `shipments`). |
| Ingest 403 | Gate POST was anonymous (no `X-API-Key`, actor `"user"`). Now forwards `cortex_api_key` + seeded actor. No-Cortex ingest is still 403 `gate_unavailable` (fail-closed). Catalog miss (`gate_task_unknown`) is Cortex-side (P-DMS-4). |
| Warehouse | Studio writes `DMS_WAREHOUSE_DB` (default `data/dms_demo.duckdb`). Live ask reads Cortex's own DuckDB (`txn_type=OUT` vs demo `outbound`). An uploaded sheet stays silent to chat until Cortex points at the same file. Do not merge the files as-is. |
| #48 E10 | Grouped ask cannot be settled by an ungrouped scalar |
| E9-01/02 | Invent-totals + F32 scope demote |
| #43/#58 | Cold-start 504; Cortex timeout 120s |

## Open next

| ID | Work |
|----|------|
| **NEEDS-YOU** | F27: EPIC-020 stays extract-only (21 Aug). No SQL Server plugin / live federation. |
| #59 FF-03 | L2 gate drops `violations` - Cortex-side, one line. |
| Free-form | Coverage is low; answered cases were L0/L1. See #59 before blaming the model. |
| Warehouse | Cortex must read `DMS_WAREHOUSE_DB` or ingest stays invisible to chat. |
| #39 VQ-01 | Blocked on Cortex in-flight (R-0006). |
| L4/L5 | Aspiration only (P-DMS-33). |

## Agent models

PRD/epic/ticket/verify = Grok 4.5 high. Research/web = Composer 2.5.
