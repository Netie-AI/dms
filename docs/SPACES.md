# DMS Spaces — Company ChatGPT for Excel & Databases

**Status:** Product architecture LOCKED 2026-07-29  
**Scope:** Data Management Service demo / forward-deployed app. **Pointer is external** (Act client only).  
**Distill:** `skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md`

---

## One sentence

> Snowflake and Databricks sell you a lake and charge by the query. Netie installs in your office, reads the Excel swamp you already have, never invents a number, and never changes one without asking twice — with a hash-chained receipt for every answer and every edit. **Spaces** let you open a sandbox over just the files you pick; Company chat can see what ACL allows.

---

## 1. Product surfaces (demo focus)

| Surface | Role |
|---------|------|
| **Central chat** | ChatGPT-for-Excel/DB: ask, retrieve, propose amend, quality flags |
| **Spaces** | Named sandboxes: only selected personal/team/shared sources are in scope for ingest index, correlation, Q&A, amend |
| **Library** | All connected sources with `personal \| team \| company` + share ACLs |
| **Studio** | Ingest, promote bronze→silver, pipelines, quarantine (existing `/studio` grows here) |
| **Audit** | Hash-chained receipts for answers + applies |

Pointer / Netie Clicks = **out of this demo**. Warehouse AI agents on data only.

---

## 2. Space = sandbox (the cool part)

A **Space** is a first-class object:

```yaml
space_id: sp_demo_q3
name: "Q3 margin sandbox"
member_ids: [user_a, user_b]
sources:
  - { kind: excel, path: "...", scope: personal, owner: user_a }
  - { kind: excel, path: "...", scope: team, team_id: finance }
  - { kind: table, ref: silver.inventory, scope: company }
  - { kind: salesforce, object: Opportunity, scope: company }
acl: inherit_from_sources ∩ space_members
state: active | archived
```

**Invariants:**
1. Chat/retrieval/amend in a Space **cannot** see paths outside `sources[]` (data-plane enforce, not UI hide).
2. Ingest + index + correlate run **inside** the Space’s allowed set only.
3. Opening a Space with ~3GB mixed Excels is a **test scenario**: precise retrieval + amend assist + quality badges must hold.
4. Company chat without a Space = default ACL view (role × personal/team/company). Space = further **intersection**.
5. CEO “all company” still cannot leak personal rows the Space did not include; break-glass is a **separate**, ledgered mode with existence-leak policy (no-results ≡ not-authorized where required).

**UI:** left rail = Spaces + Library; center = chat; right = sources in this Space / SQL / proposal diff / audit chip.

---

## 3. Four-layer lake (same shape as Snowflake, smaller radius)

DuckLake ≈ Snowflake FoundationDB + object files:

| Layer | Netie |
|-------|--------|
| Files | Parquet (open) |
| Table semantics | DuckLake catalog (SQLite → **Postgres**) |
| Query engine | DuckDB (single node wedge); later Trino/etc only at 20TB+ |
| Governance | F7 RBAC + RLS + **signed path manifest** (local credential vending) |

**Three-way storage (mandatory):**
- **Blob tier** — PDFs/CAD/images/backups; content-addressed; never aggregate-scanned as rows
- **Hot analytical core** — silver/gold facts; DuckDB/`/dms/query`
- **Document index** — BM25 + dense over blobs; ACL per chunk; extraction arrow blob→silver for numbers

500GB–2TB = single fat node (128GB RAM target). 100TB claims = mostly cold blobs — do not design one system for 100TB analytics.

---

## 4. Rights (cannot retrofit)

- Authorize **before** LLM/context retrieval (Postgres RLS and/or sqlglot-injected predicates + path manifest).
- Namespace: `personal.team.company` ≡ catalog.schema.table pattern; fourth scope later = new namespace level.
- Share access = ACL edges on sources; Space membership ∩ source ACL.

---

## 5. Retrieval + amend (quality bar)

**Read path:** abstain-first **validation-time** gate (not only generation-time):
1. Schema retrieval (embed table/column docs → top-k) before any LLM SQL  
2. sqlglot static validate  
3. EXPLAIN dry-run  
4. Bounded self-correct (≤2) then abstain  
5. Result plausibility → badge downgrade  
6. Steward-approved ad-hoc → promote to L0 certified  

**Invariant:** 0 confidently wrong. Hybrid BM25+dense for docs/part numbers.

**Write path (flagship):** versioned **Proposal** object + idempotency token on confirm + txn apply + before/after hash + re-read verify + compensating rollback.  
**Excel = source-only**; emit generated export — **no bidirectional Excel write-back**.

---

## 6. Connectors / CDC (honest)

Demo ships: files (xlsx/csv/json), folder watch, Salesforce incremental stub (`SystemModstamp`).  
“Thousands of connectors / CDC” = **marketplace ambition**, not current claim. Pattern for AWS RDS/S3/etc = same bronze writer as Salesforce. Brokers last, same bronze contract.

---

## 7. Build order (binding — flips earlier amend-first doc)

| Phase | Work | Why |
|-------|------|-----|
| **0** | Postgres ops + ledger + RLS | Amend correctness under concurrent stewards |
| **1** | Amend loop (Proposal + confirm token + verify) | Product differentiator |
| **2** | Schema retrieval + validation gate | Break ~65% paraphrase ceiling safely |
| **3** | Column lineage (sqlglot → graph) | Regulated SME story |
| **4** | DuckLake catalog → Postgres; files → MinIO when swamp real | Scale control plane |
| **5** | Packaging (installer, backup, verify-ledger, license) | Forward deploy |
| **S** | Spaces MVP (scope intersection + sandbox Q&A tests) | Can ship UI/API in parallel with 0–1 once ACL exists |
| **Last** | Streaming brokers | Same bronze writer |

Hardware for 500GB–2TB customers: **128GB RAM, 16+ cores, 4TB NVMe**; 4070 for local 9B wording/diff only — SQL gen to larger model behind certify gate.

---

## 8. Harder demo scenarios (must test)

1. Space with 5–20 mixed Excels (~3GB), personal + team share — Q&A never leaks outside Space  
2. Correlate SKU across 2 sheets + 1 silver table — one governed answer + lineage  
3. Steward amend with revise→new proposal version; old confirm token dead  
4. Concurrent two-steward confirm (needs Phase 0 Postgres)  
5. Part-number lexical hit (`RS622XK`) via BM25 not pure vector  
6. Blob PDF in Space: can RAG quote; sum of contract values only after extraction→silver  

---

## 9. Decline to compete

Petabyte ETL, streaming at scale, connector breadth, marketplace data sharing. If the eval is “better cloud data platform,” walk away.
