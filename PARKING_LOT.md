# PARKING_LOT.md — DMS

**Deferred until condition is met.**
**Ledger updated:** 2026-07-31 — C7-min + C10 harness + P-DMS-24/25 closed; launcher ASCII fix.

---

## Done this pass (not parked)

| Item | Evidence |
|------|----------|
| Launcher ASCII fix | `Start-DMSStack.ps1` / `Start-DMS.bat` — no em-dash/ellipsis |
| C7-min EXPLAIN gate | `CortexOS/dms/sql_validate_gate.py` + submit wire + `test_sql_validate_gate.py` |
| C10 adversarial harness | `bench/adversarial.py` + `dms_adversarial_v1.yaml` (11 cats) + CI tests |
| P-DMS-24 Power BI double-count | `docs/POWERBI_DUCKLAKE.md` + `test_powerbi_ducklake_export.py` |
| P-DMS-25 contract wheel | `Build-CortexContractWheel.ps1` + release.yml + DMS `>=1.2,<2` + CI wheel install |
| Live ask smoke / desktop / Library | prior |

## Strike when claimed

~~P-DMS-10 — Live Cortex accuracy (JWKS)~~ — cleared 2026-07-30.  
~~P-DMS-24 — Power BI DuckLake double-count~~ — cleared 2026-07-31 (orphan snapshots; use export/catalog).  
~~P-DMS-25 — cortex-contract wheel~~ — cleared 2026-07-31 locally (publish on next Cortex `v*` tag).

---

## Why warehouses need a DMS (product thesis — not a feature ticket)

Generic warehouses (Snowflake / Databricks / Oracle) give **storage + SQL**. They do **not** give SME operators:

1. **Provenance** — every number clickable back to Excel/PDF cell (`_src[]`)
2. **Dual-confirm amend** — human says yes twice before truth changes
3. **0 confidently wrong** — abstain over invent; signed session manifests + F5 gate
4. **Office ACL** — Spaces / departments without hiring a platform team
5. **Ingest honesty** — triage receipts, quarantine reasons, never silent partial Excel success

DMS sits **on top of** the warehouse (today DuckDB+Parquet; later Iceberg/MinIO at scale), not as a second warehouse engine.

### Sources ≠ “4 PDFs”

| “4 sources” means | Examples |
|-------------------|----------|
| 4 **registered** inputs in a Space | Excel workbook, CSV folder, Postgres connector, SharePoint library |
| One source can be **many files** | One “KL sales” source → 40 monthly xlsx |
| PDFs | Blob tier + doc index (quote); **sums only after extract → silver** |

**~100 GB PDFs:** yes as **blobs** (content-addressed, not loaded into RAM). Do **not** put 100 GB of PDF bytes into DuckDB. Extract facts → silver rows; RAG quotes chunks via Cortex `[rag]` later. Appliance target disk is NVMe TBs; working set for answers stays warm Parquet facts (see architecture §3).

---

## P-DMS-21 — C7-full generation (schema retrieval + model)

**C7-min done** (EXPLAIN + retry structure + L2 abstain stub). Remaining: `schema_retrieval.py` + FreeRoute `sql_generator.is_configured()→True` + plausibility (needs C8).  
**Condition:** working FreeRoute keys + C8 `query_run`.

## P-DMS-22 — C10 corpus growth to 150–300

Harness + 11 categories + value_normalization golden shipped. Still grow paraphrases and raise `robustness_floor` only when earned.  
**Condition:** overnight paraphrase runs green; ratchet floor upward.

## P-DMS-23 — Push / commit hygiene (third scare)

Cortex + OpenVault + DMS still have large uncommitted trees. Push before any drive risk.  
**Condition:** you push (this session — agent does not push).

## P-DMS-1 — Full D1 remaining extras

T14 signed receipts, U5–U6, Runs durability, C10.  
**Condition:** personal stress-test of T3–T8 + T12–T13 install + **G1–G13 rehearsal**.

## P-DMS-4 — F5 gate catalog tasks

Soft-allow on `gate_task_unknown` until Cortex catalogs DMS mutation tasks.  
**Condition:** Cortex packs task definitions.

## P-DMS-5 — Sellable hardening

C10, backup/restore, usage caps. **Condition:** first paying conversation.

## P-DMS-7 — Do not grow demo_ask

No new demo intents.

## P-DMS-8 — C6 scope-tagged memory

Unblocked for Cortex kickoff (live smoke green).

## P-DMS-9 — Next lane (plan only)

T14, T13b, T15, T9–T11 (external tables / multi-pool / MinIO).

## P-DMS-11 — ChatGPT-class streaming + history

SSE + durable threads. Partial: queue + pause pills.

## P-DMS-12 — Interactive warehouse UX (next product lane)

Studio classify / infer / promote / Library browse exist. Still need: steward overrides, Runs timeline, allowlisted SQL runner, mock-data generator UI, ETL action confirm.  
**Condition:** after desktop demo used once by steward.

## P-DMS-13 — Caddy appliance hardening

TLS, rate limit, internal Cortex/OV, worker.

## P-DMS-14 — Ingest steward customization

Editable classify, join keys, connectors, folder watches.

## P-DMS-15 — FreeRoute provider quality

Add working Claude/OpenAI/DeepSeek/Groq in OpenVault; re-run bake-off.

## P-DMS-16 — Snowflake / Databricks exceed (north star)

Governed SQL + provenance + abstain + amend — not Instant RAG clone.  
**Condition:** paying install + C10.

## P-DMS-17 — Iceberg / Spark lakehouse

**Do not wire now.** Product path = DuckDB + Parquet medallion. Iceberg/Spark only when concurrent writers / multi-engine readers demand it (near T11).  
**Condition:** measured writer contention or customer multi-engine mandate.

## P-DMS-18 — Grafana live insights

**Do not embed Grafana as product chrome.** Product “insights” = Chat answers + Library + Audit.  
**Condition:** appliance ops monitoring demand post-D1.

## P-DMS-19 — CRAG / open_ragbench / “100% RAG accuracy”

**DMS D1 accuracy path is not CRAG.** Warehouse → SQL + manifest + L0/L1 + abstain.  
**Condition:** thin doc-index + Cortex rag route green; then CRAG-like scoring for **document** questions only.

## P-DMS-20 — Scale stress (mock warehouse, not 4 TB in git)

Generate configurable mock Parquet/DuckDB; measure ask latency. Do **not** commit multi-TB binaries.  
**Condition:** after interactive Studio lane (P-DMS-12); script `scripts/gen_mock_warehouse.py` TBD.

## P-DMS-26 - an uploaded table is grantable from any Space

Ingest records no `space_id`, so a bronze table created by an upload is
grantable from every Space in the demo tenant. Grounding still narrows a
question to exactly what was ticked (dms#5), so the manifest never widens - but
the upload itself is not Space-scoped the way the six seeded tables are.
**Condition:** ingest carries a `space_id` end to end (Studio upload -> route ->
`ingest_batch` -> registry), at which point `DemoSessionStore` grants it to the
owning Space only.

## P-DMS-27 - mypy and import-linter debt that CI never surfaced

CI-02 kept Ruff, Mypy, import-linter and the test suite from ever running. With
the checkout step reached, mypy reports 19 errors (mostly `fetchone()` returning
`tuple | None` and being indexed) and import-linter reports one broken contract:
`dms_api.routes.chat -> dms_executor.envelope` and `dms_api.wiring ->
dms_executor.{library_tree,warehouse_browse}`. Both pre-date this pass and are
unrelated to the demo path.
**Condition:** CI-02 credential resolved, so the gates run and stay green.

## Move out of parking lot

Claim in STATUS, then strike here.
