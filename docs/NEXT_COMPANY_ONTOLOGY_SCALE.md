# Next-company ontology scale (agent playbook)

Warehouse SKUs (`inventory` / `suppliers` / `shipments`) are the **demo pack**, not the product.
The product is install-in-office ChatGPT for Excel/databases (`docs/PRODUCT_ROLES.md`).
Hosted multi-tenant PaaS is **REFUSED** (PRD F93/F96). Recreate per company = this file.

Insurance / banking / finance / retail are **the same six steps**. Do not fork Chat.
Do not mint `packs/insurance/` until EPIC-021 has one real bronze schema (F96).

cite: `docs/subagents_findings/2026-09-10_prd-insurance-industry-paas.md`
cite: `docs/subagents_findings/2026-09-10_prd-banking-paas-intake.md`

## 0. Agent start here

1. Route a new industry paste to `prd-agent` before coding (F39 class).
2. Never fork `apps/ui` for a vertical. Cream/graphite Chat stays.
3. Never live-ATTACH the customer engine (DR-0005). Extract to bronze.
4. AI may draft YAML. `verify()` blesses keys. The model does not.
5. Buyer proof is `POST /v1/chat/ask` envelope (badge, text, rows, sources). SQL-only green is not enough.

## 1. What never changes vs what you swap

| Never change | Swap per company |
|---|---|
| Chat `POST /v1/chat/ask` | Cortex pack YAML trio (`object_types` / `link_types` / `action_types`) |
| Envelope E1-E12 | `relation=` / `key` on DMS grain objects after DESCRIBE |
| Extract-only `db_connector` | Studio `_verified_queries` for that Space |
| Five ports, no CortexOS import | CCA aliases only after encodings exist on landed tables |
| F1 ledger HTTP, F5 `compliance_gate` | Space grants (which bronze tables this team may ask) |
| SimpleChart + Excel MCP last mile | Constructor catalog as **source**, not engine (F71) |

Cortex already scaffolds a new consumer: `python -m scripts.new_pack --demo crm`
(`D:\Cortex\scripts\new_pack.py`, demo `packs/crm/`). Same engine, new names.
`packs/crm/` is a scaffold, not production CRM. O6 builder is not shipped.

## 2. Six steps (the only pipeline)

```
ingest -> bronze copy -> objects (DESCRIBE) -> verify() -> Studio VQ -> Chat envelope
```

1. **Ingest.** Excel/CSV today. MSSQL/MySQL/Postgres via Studio `POST /v1/studio/sources/sql`
   (extract-only). No CDC stream. No live GPS. Incremental = dump + watermark (EPIC-020).
2. **Bronze.** Copy lands in DuckDB with `_src` / `_ingest_id`. Metadata-only is the
   **proposer** (catalog, claimed PK/FK, preview). The twin is the copy.
3. **Objects.** Author object + link + measure whose `relation=` matches
   `DESCRIBE SELECT * FROM {rel}` on **their** tables. Compiler:
   `packages/executor/dms_executor/ontology.py` (CLI `scripts/ontology.py`).
   Cortex pack YAML is a separate trio under `packs/<name>/ontology/`. Do not
   require `demo_ontology()` product / `txn_of_product` names.
4. **`verify(con)`.** Uniqueness, nulls, link cardinality on landed rows.
   AdventureWorks / demo lake cannot prove a customer. Unverified links refuse.
   `compile()` stays off Chat until F41 fires. Do not start EPIC-021 until #116
   has one real schema.
5. **Verified queries.** Steward registers Q->SQL in Studio
   (`POST /v1/studio/verified-queries`, Space-scoped `main._verified_queries`).
   Repeating buyer asks become L0 here (EPIC-019), not a new vertical UI.
6. **Ask.** `POST /v1/chat/ask`. Apps and charts consume the envelope only.
   Last mile: certified CSV -> Excel MCP (`docs/EXCEL_COPILOT_GOVERNED_PROMPT.md`)
   or SimpleChart. Power BI = single-file export (`docs/POWERBI_DUCKLAKE.md`).

## 3. Insurance worked example (not a SKU)

Do **not** add `packs/insurance/` in this repo until bronze exists. This sketch is
how an agent **names** objects after DESCRIBE. Ids below are examples. If the
customer table is `dbo.PolHdr`, `relation=` is that name, not `policy`.

### 3a. Tabular objects (after extract)

| Object (example id) | Key claim | Typical links | `agent_visible: false` |
|---|---|---|---|
| policy | policy_id | holder, coverage | none unless number is PII |
| party | party_id | policyholder / claimant | email, phone, nric |
| claim | claim_id | many_to_one -> policy | none |
| coverage | coverage_id | many_to_one -> policy | none |
| payment | payment_id | many_to_one -> claim | account numbers |

Sensitive columns use Cortex `agent_visible: false` (already on `packs/dms` suppliers
contact fields). Prompt redact today: NRIC, email, phone, credit card
(`packs/dms/security/pii.py`). US SSN / PHI de-id is **not** a product (F96).

Grain: a claim-total measure grains at `claim`, not `policy` joined 1:N without
`verify()`. Fan-out refuses.

### 3b. Buyer asks that stay on Chat (tabular)

Steward VQs after encodings exist, for example:

- open claim count this month
- incurred vs paid by policy
- duplicate claim_id / overlapping payment

Cross-ref "new claim vs history" = join on landed bronze after `verify()`, not a
fraud SKU.

### 3c. Insurance marketing copy -> route (do not build these)

| Paste | Route |
|---|---|
| NL queries across policies | KEEP Chat after bronze |
| Claims vs history (tables) | KEEP; EPIC-021 after #116 |
| Duplicate / typo encode | KEEP CCA + hard rule 12. REFUSE writeback into their DB |
| PDF as citation | KEEP envelope `kind=pdf` |
| OCR claims / notes / contracts | REFUSE OCR-in-DuckDB. Text-doc RAG = queued EPIC-015 |
| Accident photo-video cost estimate | PARK P-DMS-30 (H6 media) |
| Email/chat sentiment as a number | REFUSE L0 (E9). Table of scores after extract could later KEEP Chat |
| Live telematics / GPS | REFUSE (no CDC/stream) |
| EHR / HL7 parse | PARK |
| Unusual DB access / SIEM | REFUSE (F1 is audit, not threat detect) |
| Insurance chrome / second Chat | REFUSE |

## 4. Banking / finance / retail (same file)

Do not write a second playbook. Change names in step 3a. Examples:

| Industry | Example objects (after DESCRIBE) | Same refusals |
|---|---|---|
| Banking | account, party, txn, loan | vector-as-truth, AML stream SKU, live ATTACH (F93) |
| Finance / ops | sku, location, shipment (today's demo) | warehouse IoT / WMS clone (F98) |
| Retail | receipt, sku, store | OCR labels, CCTV fraud, clickstream MDM (F97) |

Scaffold dry-run (still not production):

```
cd D:\Cortex
python -m scripts.new_pack --name crm --from-dir packs/crm/ontology
```

`--name insurance` only after YAML whose `relation=` points at **that** bronze.

## 5. Developers creating apps (what "platform" means here)

Today:

- HTTP consumer of `POST /v1/chat/ask`. Assert envelope, not generated SQL.
- Chart from envelope rows. Do not open DuckDB from a customer app.
- Cortex pack via O7. Constructor canvas is a **source** (`scripts/constructor_source.py`).

Not today:

- EPIC-022 generative dashboards (precision-gated).
- O6 `builder.py` (not shipped).
- Foundry CLI / Marketplace / SuperRepo (P-DMS-31).
- Agents-as-a-service marketplace (F93). Insights-only Spaces omit F8 grants.

## 6. Commands (after a real schema exists)

```
python D:\DMS\scripts\ontology.py          # verify / compile CLI
python D:\DMS\scripts\ontology_bench.py   # AW family only; does not prove the customer
python D:\DMS\scripts\industry_ontology_bench.py  # warehouse/banking/insurance/storage fixtures
python D:\DMS\scripts\score_answers.py --docs D:\DMS\tests\fixtures\hostile_score --oracle-only
python D:\DMS\scripts\score_curated.py --self-check
```

Ask-path order in `live_ask`: Space VQ -> metric tools -> bronze-sheet -> CCA (flag 0) -> Cortex.
Do not recycle `:8090` / `:8010` unless the founder says so.

## Explicit non-goals

- No Constructor merge into `ontology.py`.
- No Foundry CLI / Marketplace clone.
- No inventing constructor.netie.ai or claiming app.netie.ai/cortex while 404.
- No generative Cursor dashboards until EPIC-018 GitHub closed and EPIC-017 completeness is true.
- No `packs/insurance/` mill, no Palantir retrieve, no second orchestrator.
