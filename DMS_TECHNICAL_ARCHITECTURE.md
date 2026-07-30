# DMS — Full Technical Architecture

**Status:** design reference · v0.1 · 2026-07-30
**Scope:** DMS consumer app (`D:\DMS`), its interaction with Cortex engine and OpenVault
**Companion docs:** `CORTEX_WHITEPAPER.md` · `PRODUCT_ROLES.md` · `VERSIONING.md` · `CONTRACT_COMPAT.md`

---

## 0. The one-sentence thesis

> Every number DMS shows can be clicked back to the cell it came from — across workbooks, sheets, tables and buckets — and every change to that number required a human to say yes twice.

Everything in this document exists to make that sentence true and fast. If a design decision does not serve traceability, safety, or speed-to-answer, it is out of scope.

---

## 1. System shape

Three products, one appliance. The boundary is absolute.

```
┌──────────────────────────────────────────────────────────────────┐
│  BROWSER  (only client — no desktop app, no local data)          │
│  Chat · Spaces · Library · Studio · Sources · Amend · Audit      │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTPS
┌───────────────────────────▼──────────────────────────────────────┐
│  CADDY   TLS termination · reverse proxy · rate limit · SSE       │
│          THE ONLY PUBLICLY BOUND PORT ON THE APPLIANCE            │
└───────────────────────────┬──────────────────────────────────────┘
                            │  internal docker network only
        ┌───────────────────┼───────────────────────┐
        │                   │                       │
┌───────▼────────┐  ┌───────▼────────┐  ┌──────────▼─────────┐
│  DMS API       │  │  DMS WORKER    │  │  DMS UI (static)   │
│  FastAPI       │  │  durable runs  │  │  React SPA         │
│  stateless     │  │  ingest/sync   │  │                    │
└───┬────────┬───┘  └────────┬───────┘  └────────────────────┘
    │        │               │
    │        └───────────────┴──────────┐
    │                                   │
┌───▼──────────────────┐   ┌────────────▼─────────┐
│  CORTEX  :8000/:8010 │──▶│  OPENVAULT  :5000    │
│  answer engine       │   │  key SoT             │
│  F5 gate · F1 ledger │   │  leave-machine gate  │
│  semantic layer      │   │  FreeRoute budget    │
│  executor submit()   │   │  P17a trust root     │
└───┬──────────────────┘   └──────────────────────┘
    │
┌───▼───────────────────────────────────────────────────────────────┐
│  DATA PLANE                                                       │
│  Postgres (control + catalog + doc index)                         │
│  DuckDB serving warehouse (single writer)                         │
│  Parquet lake  bronze / silver / gold                             │
│  Blob store    content-addressed originals                        │
└───────────────────────────────────────────────────────────────────┘
```

**Non-negotiables encoded in this diagram:**

| Rule | Enforced by |
|---|---|
| Cortex `:8000`/`:8010` never bound to the host | compose network config |
| OpenVault `:5000` never bound to the host | compose network config |
| DMS never imports `CortexOS` | `.importlinter` + AST invariant test |
| One ledger — Cortex owns the chain | DMS has `ledger_ref` pointers only |
| One Postgres, two schemas, no cross-schema FKs | migration review |

If `:8010` is reachable from the office LAN, any user bypasses every ACL in this document by talking to the engine directly. This is the single highest-severity misconfiguration in the product.

---

## 2. Deployment topology

Three modes, one codebase. The only difference is the compose file.

| Mode | Where it runs | Who connects | Cloud dependency |
|---|---|---|---|
| **A — Appliance** | one box in the customer office | LAN + VPN | none (local model) |
| **B — Hybrid** *(default)* | appliance + Netie cloud | LAN + VPN + remote exec | model routing, updates, multi-site catalog |
| **C — Central** | customer server room / their own cloud VM | company network | optional |

**Load balancing at SME scale is Caddy and nothing else.** One box, TLS, reverse proxy, static file serving, SSE pass-through. It stays trivial forever *provided the API holds no session state* — sessions live in Postgres, so a second API replica is a compose line, not a redesign.

Kubernetes and Helm are a later packaging change, not a rewrite, and only because of statelessness. Slurm is an HPC batch scheduler and has no place here.

**Windows note:** Docker Desktop licensing on Windows Server is a real procurement question. Plan for WSL2 + Docker Engine, or ship a native Windows service wrapper.

### Reference hardware

| Customer working set | Spec |
|---|---|
| < 500 GB | 64 GB RAM · 8 cores · 2 TB NVMe |
| 500 GB – 2 TB *(target)* | **128 GB RAM · 16+ cores · 4 TB NVMe** |
| 2–20 TB | 256 GB RAM · 32 cores · NVMe array · Postgres catalog mandatory |
| 20 TB+ | out of scope — resell, don't rebuild |

GPU (RTX 4070 class) is for local model inference only: routing, wording, diff explanation. Not for wide-schema SQL generation.

---

## 3. Storage tiers

An SME claiming 100 TB has ~100 TB of cold blobs and ~200 GB of analytical facts. Never let the first number into the lakehouse.

### Tier 1 — Blob store (cold, immutable, content-addressed)

Originals of every ingested file, keyed by SHA-256. Never scanned in aggregate. Backing: local filesystem → MinIO → S3 (port-abstracted).

```
blobs/sha256/ab/cd/abcd1234...ef  ← original Q3_sales_final_v2.xlsx bytes
```

This tier is what makes "open the original" possible three years later, after the user has renamed, moved, or deleted the source file.

### Tier 2 — Lakehouse (warm, analytical, medallion)

Parquet on disk, DuckLake catalog (SQLite → Postgres).

| Layer | Contents | Trust |
|---|---|---|
| **bronze** | raw ingested rows, typed loosely, provenance columns attached | never answers as truth |
| **silver** | typed, validated, deduplicated, quarantine applied | answerable |
| **gold** | governed metrics and aggregates | answerable, certified |

### Tier 3 — Document index (warm, retrieval)

Chunks + embeddings + lexical index over the blob tier, **stored in Postgres** — `pgvector` HNSW for dense, Postgres FTS for lexical. This keeps the port count at five: catalog, object store, model provider, serving engine, secrets. A document index is not a sixth port if it lives in the control-plane database.

Hybrid retrieval, not pure vector. Embeddings are unreliable on part numbers — `RS622XK` vs `RS622XKR` is a lexical problem, and lexical search solves it perfectly. Reciprocal rank fusion over both.

Upgrade to a dedicated index only if measured recall demands it.

### Tier 4 — Serving warehouse (hot)

`dms_serving.duckdb`, refreshed from `silver`/`gold`. **Single writer.** Read pools attach read-only.

### The arrow most people skip

```
document index ──extract──▶ silver
```

RAG can answer *"what does the contract say about penalties"*. Only extraction-into-silver lets you answer *"sum the penalty clauses across all 40 contracts"*. Build the extraction path or your document AI is permanently a quoting tool.

---

## 4. The provenance spine — how golden reference actually works

This is the differentiating machinery. Read this section twice.

### 4.1 Every bronze row carries its origin

Ingestion attaches provenance columns to every row of every table:

```sql
_src        STRUCT(ref_id UUID, row INTEGER)[]   -- array: joins produce multiple origins
_ingest_id  UUID                                  -- FK to ingest ledger
```

`_src` is an **array** because a silver row produced by joining two bronze tables has two origins. DuckDB handles struct arrays natively; drill-through unnests.

### 4.2 The `source_ref` registry

```sql
CREATE TABLE dms.source_ref (
  id            UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL,
  kind          TEXT NOT NULL,      -- xlsx | csv | sql | parquet | pdf | api
  blob_hash     TEXT,               -- content address; NULL for live SQL sources
  container     TEXT NOT NULL,      -- workbook name | table name | bucket
  member        TEXT,               -- sheet name | partition | page number
  header_row    INTEGER,            -- which spreadsheet row was the header
  col_map       JSONB,              -- {"revenue":"F", "invoice_date":"B"}
  origin_uri    TEXT,               -- \\fileserver\finance\Q3.xlsx | s3://... | salesforce://Opportunity
  row_count     BIGINT,
  ingested_at   TIMESTAMPTZ,
  ingested_by   UUID,
  UNIQUE (tenant_id, blob_hash, member)
);
```

`col_map` is the piece that makes A1 addressing possible. Captured once at ingest, cheap forever.

```
col_map["revenue"] = "F"  +  _src.row = 847   →   Data!F847
```

### 4.3 Source reference types

One union type covers every backend:

| `kind` | `container` | `member` | Address rendered as |
|---|---|---|---|
| `xlsx` | workbook filename | sheet name | `Q3_sales.xlsx › Data › F847` |
| `csv` | filename | — | `sales.csv › line 847` |
| `sql` | schema.table | — | `erp.public.invoices › id=88201` |
| `parquet` | s3 path | row group index | `s3://lake/sales/*.parquet › rg 3 › row 41` |
| `pdf` | filename | page number | `MSA_2025.pdf › p.14 › ¶3` |
| `api` | connector name | object type | `salesforce › Opportunity › 006xx…` |

### 4.4 Lineage must survive bronze → silver

The pipeline contract: **any silver table must propagate `_src`, or be explicitly marked otherwise.**

```yaml
# pipelines/silver_sales.yaml
target: silver.sales
lineage: propagate          # propagate | aggregate
sources: [bronze.sales_raw_2026q3, bronze.sales_raw_2026q2]
```

CI check: any pipeline producing a silver table without `_src` columns and without `lineage: aggregate` plus a documented reason fails the build. This is the invariant that keeps golden reference working as the pipeline count grows — it will silently rot otherwise.

### 4.5 Drill-through: reconstructing origins for an aggregate

An answer like `SUM(amount) = 4,203,881.44` contributes from 84,201 rows. You do not store that set. You **re-derive it on demand** by rewriting the answer SQL.

```
ANSWER SQL
  SELECT SUM(s.amount)
  FROM silver.sales s
  JOIN silver.customer c ON c.id = s.customer_id
  WHERE s.quarter = 'Q3' AND c.region = 'Northern'

DRILL-THROUGH SQL  (generated via sqlglot AST rewrite)
  SELECT s.amount, s._src AS src_sales, c._src AS src_customer
  FROM silver.sales s
  JOIN silver.customer c ON c.id = s.customer_id
  WHERE s.quarter = 'Q3' AND c.region = 'Northern'
  ORDER BY s.amount DESC
  LIMIT 5000
```

Rewrite rules:

1. Strip aggregate functions from the projection; **keep their arguments** (`SUM(s.amount)` → `s.amount`).
2. Drop `GROUP BY`. Convert `HAVING` to an equivalent `WHERE` where possible; if not possible, drop it and flag the drill-through as approximate.
3. Preserve every `WHERE` predicate and every join — unchanged.
4. Project `_src` for every base table in the `FROM` tree.
5. `ORDER BY` the aggregated measure descending — biggest contributor first, because when a number is wrong the culprit is usually the largest row.
6. `LIMIT 5000` + a separate `COUNT(*)` so the UI can say *"showing 5,000 of 84,201 contributing rows"*.

### 4.6 The security rule that is easy to miss

> **The drill-through query must execute under the same signed session manifest as the original answer.**

If it does not, clicking a number becomes an ACL bypass: the answer was filtered, the drill-through was not, and the user reads rows they were never granted. Same manifest, same predicates, same pool, or the click is refused.

Implementation: the answer envelope carries a `drillthrough_token` — HMAC-signed, binding `{answer_id, session_id, manifest_hash, expires_at}`. `POST /drillthrough` accepts only the token, never raw SQL from the client.

### 4.7 The answer envelope

Every answer, without exception, returns this shape:

```json
{
  "answer_id": "ans_01J…",
  "text": "Q3 revenue for the Northern region was RM 4,203,881.44.",
  "values": [
    { "id": "v1", "value": 4203881.44, "unit": "MYR", "label": "Q3 revenue" }
  ],
  "badge": "L1_GOVERNED_METRIC",
  "sql_used": "SELECT SUM(s.amount) …",
  "assumptions": ["Q3 = 2026-07-01 to 2026-09-30", "excludes cancelled orders"],
  "as_of": "2026-07-30T09:14:00Z",
  "contributing_sources": [
    { "ref_id": "…", "container": "Q3_sales_final_v2.xlsx", "member": "Data",
      "kind": "xlsx", "row_count": 1846, "contribution": 2891004.10 },
    { "ref_id": "…", "container": "KL_branch_sales.xlsx", "member": "Sheet1",
      "kind": "xlsx", "row_count": 402, "contribution": 811277.34 },
    { "ref_id": "…", "container": "erp.public.invoices",
      "kind": "sql", "row_count": 81953, "contribution": 501600.00 }
  ],
  "drillthrough_token": "dt_…",
  "audit_id": "aud_01J…"
}
```

`values[].id` is what makes every number in the rendered text individually clickable. The frontend tokenizes the text against this array — no regex over prose.

### 4.8 Navigating five workbooks

When `contributing_sources` has five entries, the source panel renders five cards, **sorted by contribution descending**:

```
┌─ SOURCES ────────────────────────── 3 files · 84,201 rows ──┐
│                                                              │
│  ▸ Q3_sales_final_v2.xlsx  ›  Data                          │
│    1,846 rows  ·  RM 2,891,004.10  ·  68.8%                 │
│    \\fs01\finance\2026\Q3_sales_final_v2.xlsx               │
│    ingested 2026-07-14 by aminah@   ·   sha 4f2a…           │
│    [ Preview ]  [ Open original ]  [ Copy path ]            │
│                                                              │
│  ▸ KL_branch_sales.xlsx  ›  Sheet1                          │
│    402 rows  ·  RM 811,277.34  ·  19.3%                     │
│    …                                                         │
│                                                              │
│  ▸ erp.public.invoices                                       │
│    81,953 rows  ·  RM 501,600.00  ·  11.9%                  │
│    postgres://erp-prod  ·  synced 2026-07-30 06:00          │
│    …                                                         │
└──────────────────────────────────────────────────────────────┘
```

Expanding a card opens an inline virtualized grid rendered from the original blob, scrolled to the first contributing row, with contributing cells highlighted and non-contributing rows dimmed. Jump-to-next-contribution stepper at the top.

### 4.9 Opening the real file — the honest engineering answer

Be realistic about desktop deep-linking:

| Approach | Works? |
|---|---|
| In-browser preview grid with highlighted cells | **Yes — this is the primary path** |
| Download original from blob store (hash-verified) | **Yes** |
| Copy UNC path to clipboard | **Yes** |
| `ms-excel:ofe|u|file://…` protocol handler | Opens the *file*, not the cell. Windows + desktop Excel only. Offer it, don't rely on it. |
| Deep-link to a specific cell in desktop Excel | **No reliable mechanism exists.** Do not promise it. |

So the product promise is: *"see the exact cell in the browser, and open the original file if you want it."* That is deliverable, honest, and already better than anything the incumbents give an SME.

### 4.10 The Data Map

A Library view answering *"where does everything physically live"* for the whole tenant — one row per source: logical name, kind, physical URI, tier, size on disk, last refresh, row count, owning scope, downstream silver tables. This is the view that ends the "is our data in the US cloud?" conversation in thirty seconds.

---

## 5. Backend services

### 5.1 DMS API — FastAPI, stateless

| Group | Routes |
|---|---|
| **Auth** | `POST /auth/login` · `/auth/refresh` · `/auth/logout` · `GET /auth/me` |
| **Spaces** | `GET,POST /spaces` · `GET /spaces/{id}` · `POST /spaces/{id}/members` · `POST /spaces/{id}/sources` |
| **Sources** | `POST /sources/upload` · `GET /sources` · `GET /sources/{id}` · `POST /sources/{id}/promote` · `GET /sources/map` |
| **Connectors** | `GET,POST /connectors` · `POST /connectors/{id}/sync` · `GET /connectors/{id}/status` |
| **Chat** | `POST /chat/ask` *(SSE)* · `GET /chat/{id}` · `GET /chat/{id}/messages` |
| **Provenance** | `POST /drillthrough` · `GET /sources/{ref_id}/preview` · `GET /blobs/{hash}` · `GET /lineage/{table}` |
| **Amend** | `POST /proposals` · `GET /proposals/{id}` · `POST /proposals/{id}/versions` · `POST /proposals/{id}/confirm` · `POST /proposals/{id}/cancel` |
| **Audit** | `GET /audit` · `GET /audit/{id}` · `POST /audit/verify` |
| **Runs** | `GET /runs` · `GET /runs/{id}` · `POST /runs/{id}/cancel` · `POST /runs/{id}/retry` |
| **Admin** | users · roles · departments · `acl_grants` · `compute_pool` |
| **Health** | `GET /health` · `/health/features` · `/health/deps` |

**Streaming is SSE, not WebSocket.** One-directional is all an answer stream needs, it passes through Caddy with no special config, and it reconnects natively. Reserve WebSocket for a future collaborative-editing surface that does not yet exist.

### 5.2 DMS Worker — durable runs

A run is a **Postgres state machine**, not an in-memory task. It survives an appliance restart mid-ingest, which will happen at a customer site.

```sql
run(id, tenant_id, kind, status, created_by, space_id,
    payload JSONB, current_step, total_steps,
    started_at, finished_at, error_class, error_detail)

run_step(run_id, seq, name, status, started_at, finished_at,
         output JSONB, retry_count)
```

Run kinds: `ingest` · `promote` · `connector_sync` · `extract` · `index` · `apply_proposal` · `verify_ledger` · `agent_run`.

Claiming uses `SELECT … FOR UPDATE SKIP LOCKED` so multiple workers are safe from day one.

### 5.3 Compute pools

Not "warehouses" — that word already means the physical building in this vertical. Call them **pools** or **lanes**.

```sql
compute_pool(pool_id, name, department_id NULL, mode 'read'|'write',
             max_memory_mb, max_threads, max_concurrency,
             queue_timeout_s, statement_timeout_s, idle_evict_s)
```

**Three axes stay orthogonal, permanently:**

| Axis | Question | Carried by |
|---|---|---|
| Identity | who are you | user · role · department |
| Data scope | what may you read | signed manifest — **bound to session** |
| Compute pool | where does it run | pool spec — **bound to nothing else** |

Department is a *routing default* on axis 3 and a *grant source* on axis 2. It is never itself an access boundary. Coupling pool to permission creates the escalation path "route my query to the Finance pool" → "read Finance data."

**Global memory broker:** pool activation fails if `SUM(max_memory_mb)` across live pools would exceed the appliance budget. Snowflake adds VMs; you cannot. Refusing activation beats letting pools OOM each other.

Write pool concurrency is **1** — the serving DuckDB is single-writer. Amend applies serialize behind a Postgres advisory lock keyed on `(tenant_id, target_table)`.

### 5.4 Database layout

**One Postgres instance, two schemas, no cross-schema foreign keys.** Reference by ID, resolve in the application. Cross-schema FKs are what make splitting to two databases impossible later.

| Schema | Owner | Contents |
|---|---|---|
| `cortex` | Cortex | ledger (hash chain), semantic layer, vocabulary, tool registry, memory |
| `dms` | DMS | tenants, users, roles, departments, memberships, sessions, api_keys, spaces, space_members, data_sources, source_ref, acl_grants, proposals, proposal_versions, ledger_ref, query_run, compute_pool, run, run_step |

RLS on every tenant-scoped table, keyed on `SET LOCAL dms.tenant_id`, deny-by-default.

---

## 6. The AI query path, end to end

Twelve stages. Every guarantee in the product is enforced at a specific one.

```
 1  USER TYPES          "what was Q3 revenue up north"
        │
 2  SESSION RESOLVE     user → roles → departments → space (if any)
        │               scope = space_members ∩ source_acl_grants
        │
 3  MINT MANIFEST       DMS resolves scope → allowed parquet paths
        │               + compiled row predicates + pool_id + TTL
        │               signed with OpenVault-issued intermediate key
        │
 4  DESTRUCTIVE CHECK   "delete", "drop", "wipe" → refuse + audit, stop
        │
 5  VOCABULARY NORM     paraphrase → router words
        │               NUMBERS AND IDENTIFIERS COME FROM ORIGINAL TEXT ONLY
        │
 6  ROUTE               L0 certified query   → skip to 10
        │               L1 governed metric   → skip to 10
        │               L2 generate          → continue
        │               no match             → ABSTAIN
        │
 7  SCHEMA RETRIEVE     embed question → top-k relevant tables + columns
        │               (at 200+ tables the full schema does not fit a prompt —
        │                this single step is the largest coverage unlock)
        │
 8  GENERATE            LLM proposes SQL against the REDUCED schema
        │
 9  VALIDATE            sqlglot parse → tables/columns exist?
        │               DDL/DML rejected · row limit forced
        │               manifest predicates injected
        │               EXPLAIN dry-run for cost sanity
        │               fail → feed error back, retry ×2 → else ABSTAIN
        │
10  EXECUTE             Cortex submit(sql, manifest, pool_id, timeout)
        │               Cortex re-verifies manifest signature and paths
        │               ── DMS MINTS, CORTEX ENFORCES, OPENVAULT ROOTS ──
        │
11  PLAUSIBILITY        magnitude vs history · null rate · row count
        │               anomaly → downgrade badge, never suppress
        │
12  ENVELOPE            answer + values[] + badge + sql + assumptions
                        + contributing_sources + drillthrough_token + audit_id
                        → query_run row → ledger append
```

### 6.1 Badges

| Badge | Meaning | Shown as |
|---|---|---|
| `L0_CERTIFIED` | steward-approved exact query | green · "certified" |
| `L1_GOVERNED_METRIC` | semantic-layer metric definition | green · "governed" |
| `L2_VALIDATED` | generated, passed the full gate | amber · "generated — check sources" |
| `L2_ANOMALOUS` | passed the gate, failed plausibility | amber · "unusual result — verify" |
| `ABSTAIN` | no safe path to an answer | grey · not an error |

**`0 confidently wrong` is a hard product constraint, not a target.** A wrong number with a green badge destroys the entire value proposition. Every incident is a P0.

### 6.2 Abstain is a feature, and must be useful

An abstain returns three things, never a bare refusal:

1. **Why** — plain language. *"I don't have a governed definition of 'north' — the region column has values KL, Penang, Johor, Sabah."*
2. **Nearest certified questions** — three, clickable.
3. **Ask a steward** — files a request with the original question attached, routed to whoever owns the relevant source.

That third button is also your L0 growth engine: steward answers → query gets certified → coverage rises from usage instead of from hand-authoring.

### 6.3 The promotion loop

```
L2 validated answer  →  used 5+ times  →  surfaced to steward
                     →  steward approves  →  becomes L0 certified
```

Frequency signal comes from `query_run`. The certified library grows itself.

### 6.4 Model routing

| Job | Model |
|---|---|
| Intent routing, vocabulary | local small (7–9B on the 4070) |
| Diff explanation, answer wording | local small |
| SQL generation over wide schema | **large** — local 30B+ if the box allows, else cloud via OpenVault FreeRoute |
| Embeddings | local |
| Document extraction (PDF → structured) | large + vision |

Cloud routing is safe here *because the parser validates the output anyway* — the LLM proposes, sqlglot decides. But it passes the OpenVault leave-machine gate first, and in Mode A it never happens.

---

## 7. The amend path

Read is table stakes. Safe write is the product.

```
 1  PARSE       natural language → structured proposal
                {target_table, target_rows, column, before, after, reason}

 2  AI CHECK    type · unit · FK integrity · business rules
                PII touched? · destructive? · magnitude sanity

 3  SECOND ASK  plain-language diff FIRST, SQL behind a disclosure
                "Change SKU-9 quantity in Warehouse A from 12 to 21.
                 This affects 1 row in silver.inventory. Confirm?"

 4  REVISE      user corrects → NEW ProposalVersion
                → old idempotency token DIES
                → re-check → ask again

 5  CONFIRM     token must match the active version
                steward role required · F5 gate must pass

 6  APPLY       advisory lock → transaction → write pool
                before/after hashes captured

 7  LEDGER      actor · before_hash · after_hash · sql · gate_result
                · parent_proposal_version · audit_id

 8  VERIFY      re-read the row
                mismatch → compensating rollback + alert + ledger entry
```

**Schema:**

```sql
proposal(id, tenant_id, created_by, space_id, target_table, status, created_at)

proposal_version(id, proposal_id, seq, diff JSONB, sql_preview,
                 idempotency_token, gate_result JSONB, status, created_at)
-- partial unique index: only ONE version per proposal may be 'active'
```

The versioned-token design eliminates the entire class of *"user confirmed a diff that had already changed"* bugs. It is worth the extra table.

**Excel is source-only.** No bidirectional write-back — formulas, merged cells, concurrent editors, and no transactionality make it a corruption swamp with no ACID story. The user outcome is delivered by generating a clean export instead. An AST invariant test fails the build if `to_excel`, `openpyxl.Workbook.save`, or `xlsxwriter` appears anywhere in the DMS tree.

---

## 8. Agentic layer

Build the safety surface first. An agent is exactly as trustworthy as its tool registry.

| Tool class | Side effects | Who may invoke |
|---|---|---|
| `read` | none | agent, freely, inside the manifest |
| `propose` | creates a ProposalVersion | agent, freely |
| `apply` | mutates data | **human confirm or dual-control only** |

> **Agents are never issued an `apply` tool.** Ever.

That one rule means increasing agent capability never increases blast radius — which is the property that makes autonomy sellable to a regulated SME.

Build order: typed tool registry with class enforcement → single-step tool calling with provenance → durable runs (§5.2) → bounded multi-step planning (step count, wall clock, tool-class budget) → agent proposals landing in the same confirm UI as human ones → scheduled routines → multi-agent only if someone pays for it.

---

## 9. Frontend architecture

**Browser only.** No fat desktop client, no local cache of customer data. A desktop client that caches rows destroys both the ACL story and the audit story — the only two things being sold.

**Stack:** React + TypeScript · Vite · TanStack Query (server state) · TanStack Virtual (grids) · SSE for streaming · PDF.js for document highlighting · Tailwind. No SSR — it is an authenticated appliance app.

### The ten surfaces

| # | Surface | Job |
|---|---|---|
| 1 | **Chat** | central. ask, stream, every number clickable |
| 2 | **Source panel** | golden reference. slides in beside chat, never a modal |
| 3 | **Preview grid** | virtualized sheet render, contributing cells highlighted |
| 4 | **Spaces** | create, attach sources, switch scope, manage members |
| 5 | **Library** | every source, ownership tags, Data Map, physical locations |
| 6 | **Studio** | drop zone, connectors, promote bronze→silver, quarantine review |
| 7 | **Amend** | plain-language diff, revise, confirm |
| 8 | **Audit** | ledger view, filter by actor/table/date, verify chain |
| 9 | **Runs** | ingest and sync progress, retry, error detail |
| 10 | **Admin** | users, roles, departments, ACL grants, compute pools |

### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  netie                    [Space: Q3 Audit ▾]      aminah@ ▾     │
├────────┬─────────────────────────────────────┬───────────────────┤
│        │                                     │                   │
│ Chat   │   Q3 revenue for the Northern       │  SOURCES          │
│ Library│   region was RM 4,203,881.44.       │  3 files          │
│ Studio │        ╰─ clickable ─╯               │  84,201 rows      │
│ Amend  │                                     │                   │
│ Audit  │   ● governed   ⟨sql⟩  ⟨lineage⟩     │  ▸ Q3_sales…xlsx  │
│ Runs   │                                     │    68.8%          │
│ Admin  │   Assumptions                        │  ▸ KL_branch…    │
│        │   · Q3 = Jul 1 – Sep 30             │    19.3%          │
│        │   · excludes cancelled orders        │  ▸ erp.invoices  │
│        │                                     │    11.9%          │
│        ├─────────────────────────────────────┤                   │
│        │  Asking: Q3 Audit · 3 sources       │                   │
│        │  ┌───────────────────────────────┐  │                   │
│        │  │ Ask about your data…          │  │                   │
│        │  └───────────────────────────────┘  │                   │
└────────┴─────────────────────────────────────┴───────────────────┘
```

The source panel is **always docked, never modal**. Provenance that interrupts reading is provenance nobody uses.

---

## 10. Usability rules — the "braindead" spec

These are requirements, not suggestions. Each one has a test.

**1. Never a blank page.** Empty chat shows six suggested questions drawn from the certified library, scoped to the current Space. An empty screen is an invitation to act, not a void.

**2. Scope is always visible.** A chip sits directly above the input: `Asking: Q3 Audit · 3 sources`. The user never has to guess what the AI can see. Clicking it opens the source list.

**3. Every number is a button.** No exceptions. If a value in an answer cannot be clicked back to its origin, it is not an answer — it is a claim, and claims do not ship.

**4. Four clicks to bedrock.** `answer → badge → sources → cells`. Progressive disclosure: SQL and the lineage graph are available but never in the default view.

**5. Plain language leads, SQL follows.** Confirm dialogs open with *"Change quantity from 12 to 21"*, with `⟨show technical detail⟩` collapsed underneath. A user who cannot read SQL must be able to safely operate the entire product.

**6. Ingest returns a receipt.** Drop a folder → *"47 files · 41 ingested · 6 quarantined"* with each quarantine reason named and a one-click fix path. Never a silent partial success.

**7. Errors name the file and the fix.** *"KL_branch_sales.xlsx — column 'Amt' has text in 14 rows. Review them, or ingest as text."* Never a stack trace, never an apology, never vague.

**8. Actions keep their name through the whole flow.** The button says "Confirm change", the toast says "Change confirmed", the ledger says "change confirmed". A single vocabulary is how people learn their way around.

**9. Nothing blocks reading the answer.** No modal, ever, over a rendered answer.

**10. Show work while working.** During execution, stream the plan and the SQL. Perceived latency drops and trust rises — the user watches the system be careful.

**11. Name things by what people control.** "Sources", not "ingestion pipelines". "Who can see this", not "ACL grants". System vocabulary belongs in admin, not in chat.

**12. Abstain is never a dead end.** §6.2.

---

## 11. Security and rights

**Authorization is enforced in the data plane, before the model sees anything.** Not in the answer. Not in the UI.

| Control | Mechanism |
|---|---|
| Row filtering | manifest predicates injected by sqlglot into every query |
| Path restriction | manifest `allowed_paths`, verified by Cortex via AST walk — not string matching |
| Tenant isolation | Postgres RLS, deny-by-default |
| Key custody | OpenVault trust root → short-lived intermediate → per-session manifest signature |
| Offline tolerance | Cortex caches OpenVault's public key to disk; verification never makes a network call on the hot path |
| Memory scope | every memory entry and RAG chunk carries the scope that wrote it; retrieval filters `entry_scope ⊆ session_scope` **in the storage query**, not as a Python post-filter |

**Scope the retrieval, not the response.** A model that reads forbidden rows and then declines to mention them has already leaked — through paraphrase, through ranking, through timing.

**Existence-leak policy, decided explicitly:** `no results` and `not authorized` are **indistinguishable** at personal scope. Otherwise CEO break-glass has no meaning, because absence itself becomes a signal.

**Auth:** OIDC from day one (Entra ID for the M365 majority), local password fallback for air-gapped installs. Short JWT access tokens; refresh tokens in a Postgres sessions table with revocation. LAN, VPN and cloud-relay then share one code path.

---

## 12. Observability

| Signal | Store | Used for |
|---|---|---|
| Structured JSON logs, correlated by `audit_id` | files → stdout | debugging one user's one question |
| `query_run` rows | Postgres | capacity planning · plausibility baseline · L0 promotion signal |
| `run` / `run_step` | Postgres | ingest and sync visibility, retry |
| F1 ledger | Postgres (`cortex` schema) | tamper-evident audit, customer-facing |
| OpenTelemetry traces | optional exporter | latency breakdown across API → Cortex → DuckDB |

No Grafana stack in a one-box SME install unless the customer asks. Postgres tables and a good Runs screen cover it.

---

## 13. Scaling ladder

| Stage | Trigger | Change |
|---|---|---|
| 1 | first install | one box, everything in compose |
| 2 | read contention | multiple read pools + memory broker |
| 3 | ingest volume | separate worker container |
| 4 | concurrent writers to the lake | DuckLake catalog SQLite → Postgres |
| 5 | real blob swamp | filesystem → MinIO/S3 |
| 6 | multi-site | k8s + Helm; stateless API makes this packaging, not rewrite |
| 7 | > 20 TB analytical | Trino/StarRocks behind the same manifest contract — or resell |

Streaming brokers (Kafka/NATS) stay last, and only behind the same bronze writer contract. Never a parallel ingest path.

---

## 14. Deliberately not built

Saying no is architecture.

| Not building | Why |
|---|---|
| Bidirectional Excel write-back | no ACID story; generate exports instead |
| Hot/cold lifecycle tiering | DuckDB buffer manager + NVMe already does this |
| Petabyte ETL | wrong product, wrong buyer |
| Streaming at scale | not the SME problem |
| Thousands of connectors | future pattern behind the same bronze writer, not a demo claim |
| Marketplace / data sharing | incumbent territory |
| Fat desktop client | destroys ACL and audit stories |
| Freeform LLM SQL as default | certify or abstain |
| Second hash chain in DMS | ordering between engine and app becomes unprovable |
| Trained JEPA · MemPalace · MinIO 500GB | not claimed |

---

## 15. Mapping to the build queue

| Section | Task | Repo | Required for D1 |
|---|---|---|---|
| §11 manifest verification | C3 | Cortex | ✅ |
| §5.3 submit + pools + telemetry | C4 | Cortex | ✅ |
| §8 tool registry + classes | C5 | Cortex | |
| §11 scope-tagged memory | C6 | Cortex | ✅ |
| §6.7–6.9 schema gate | C7 | Cortex | |
| §5.2 durable runs | C8 | Cortex | |
| §4.4 column lineage | C9 | Cortex | |
| skeleton + invariants | T0 | DMS | ✅ done |
| §5.4 control plane + RLS | T1 | DMS | ✅ |
| §11 manifest minting | T2 | DMS | ✅ |
| §5.1 Spaces + scoped chat | T3 | DMS | ✅ |
| §7 amend loop | T4 | DMS | ✅ |
| §11 OIDC + sessions | T5 | DMS | ✅ |
| §2 compose bundle, Caddy, port isolation | T6 | DMS | ✅ |
| §4 **provenance spine** | **T7** | DMS + Cortex | ✅ |
| §9–10 frontend surfaces | T8 | DMS | ✅ |
| §3 external tables | T9 | DMS | |
| §5.3 multi-pool activation | T10 | DMS | |
| §3 MinIO + Postgres catalog | T11 | DMS | |

**T7 moves up.** The provenance spine was not in the earlier queue and it is the differentiator — but §4.1 and §4.4 must land *at ingest and pipeline authoring time*, because provenance cannot be retrofitted onto rows that were written without it. Every day of ingest without `_src` is a day of data that can never be traced.

---

## Appendix A — provenance columns, canonical definition

```sql
-- attached to EVERY bronze table at ingest
_src        STRUCT(ref_id UUID, "row" INTEGER)[]  NOT NULL
_ingest_id  UUID                                  NOT NULL

-- silver: propagated by the pipeline, or explicitly waived
-- gold:   propagated where cardinality allows, else derived via drill-through
```

## Appendix B — manifest, canonical definition

```json
{
  "session_id": "ses_01J…",
  "tenant_id": "…",
  "allowed_paths": ["lake/silver/sales/**", "lake/silver/customer/**"],
  "row_predicates": {
    "silver.sales":    "region_id IN (3,7)",
    "silver.customer": "owner_team_id = 12"
  },
  "pool_id": "pool_read_default",
  "issued_at":  "2026-07-30T09:14:00Z",
  "expires_at": "2026-07-30T09:29:00Z",
  "issuer_key_id": "ov-int-2026-07-30-a"
}
```

Signed detached over a canonical serialization (sorted keys, no whitespace). Verified by Cortex against OpenVault's published JWKS, cached to disk with its own TTL.

## Appendix C — the invariants, in one list

1. DMS never imports `CortexOS` — HTTP only, contract major 1
2. One ledger; DMS holds pointers
3. One Postgres, two schemas, no cross-schema FKs
4. Cortex and OpenVault ports never bound to the host
5. Excel is source-only
6. Manifest signature verified at the executor, AST-level path enforcement
7. Drill-through runs under the same manifest as its answer
8. Agents never receive an `apply` tool
9. No write without steward role + F5 gate + explicit confirm on the active proposal version
10. Every apply is reversible or it does not ship
11. Numbers and identifiers come from user text, never from a paraphrase rewrite
12. `0 confidently wrong` — a green badge on a wrong number is a P0
13. Five ports abstracted: catalog, object store, model provider, serving engine, secrets. No sixth.
14. `tests/invariants/**`, `contract/**`, `.importlinter` are protected paths
