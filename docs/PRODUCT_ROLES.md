# PRODUCT_ROLES — DMS · Cortex · OpenVault · Pointer

**Audience:** Cursor and Claude Code. Do not blur these boundaries.

## One sentence each

| Product | Sentence |
|---------|----------|
| **DMS** | Install-in-office ChatGPT for Excel/databases: Spaces, ingest, governed Q&A, confirm-gated amend, audit. |
| **Cortex** | Governed orchestration **engine** (answer, F5, ledger, semantic layer, execution). Verticals are HTTP consumers. |
| **OpenVault** | Sovereign **key vault + LLM/vision proxy + leave-machine gate**. No business lake. |
| **Pointer** | Screen Ask/Act client; fail-closed Act via Cortex. Not the lake UI. |

## Runtime wiring

```
User → DMS UI → DMS API
                  ├─ Postgres schemas: cortex | dms  (no cross-schema FKs)
                  ├─ packages/executor (DuckDB / lake serving)
                  ├─ HTTP → Cortex  (cortex-contract 1.x; engine tag floats)
                  └─ HTTP → OpenVault  (keys / FreeRoute / leave-machine)
```

**DMS never imports CortexOS.** Appliance runs HTTP. Compose pins the engine image.

## DMS owns

- Product UX (central chat, Spaces, Library, Studio, Audit)
- Tenant accounts, roles, personal/team/company scopes
- Lakehouse serving, ingest, pipelines, amend proposals (confirm-gated)
- Deploy presets (Compose day-1)

## DMS does not own

- Model weights or API key custody → OpenVault
- Hash-chained ledger → Cortex (DMS appends via HTTP only)
- Generic multi-agent engine / DAG compiler → Cortex
- Desktop click automation → Pointer

## Pitch (SME)

> Snowflake and Databricks sell you a lake and charge by the query. Netie DMS installs in your office, reads the Excel swamp you already have, never invents a number, and never changes one without asking twice — with a hash-chained receipt for every answer and every edit.

## References

- [VERSIONING.md](VERSIONING.md)
- [ARCHITECTURE.md](ARCHITECTURE.md) (may lag T0 — prefer CLAUDE.md)
- [SPACES.md](SPACES.md)
- Cortex north star: `D:\Cortex\docs\strategy\CORTEX_FINAL_GOAL.md`
