# PARKING_LOT.md — DMS

**Deferred until condition is met.**
**Ledger updated:** 2026-08-05 afternoon — F29 NEEDS-YOU closed **A** (thin EPIC-023 deferred SURFACE; B/H6 rejected; P-DMS-30 stays parked). Accuracy RUN NOW = residual EPIC-017/018 completeness (#33/#35), then EPIC-019. EPIC-023 not ticketed.

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

## P-DMS-28 - Cassandra/Scylla or MCP-into-customer-DB as serving plane (declined)

Founder/architecture ask (PRD F27): replace DuckDB with Cassandra/Scylla, or
MCP-write into customer DynamoDB/MSSQL for ontology/inference/outputs.
**Declined as product path.** DMS sits on top of a lake warehouse (DuckDB+Parquet
today; Iceberg/MinIO later via P-DMS-17), not as a second OLTP/wide-column engine.
Postgres stays control plane only. Customer DBs enter via extract-to-bronze
(EPIC-020), never live federation. Ontology/inference stay in Cortex over HTTP.
Lawful future warehouse swap = `serving_engine` port only, after measured need.
**F27 NEEDS-YOU closed 2026-08-05:** founder YES on both declines; EPIC-020
extract-only confirmed.
**Condition:** measured DuckDB writer contention or multi-engine reader mandate
(same unlock as P-DMS-17) - and still not Cassandra; pick an analytics lake
engine. MCP-into-customer-DB write path stays forbidden unless a PRD amendment
explicitly reopens live federation + Cortex ownership of ontology.

## P-DMS-29 - Accuracy / orchestration research paper backlog (F28)

Research-ingest: DeepMind-class agent papers, Genie/AI-BI writeups, MNC DB
systems notes, and Bigtable 20-year paper (DOI 10.1145/3788853.3803095).
**Not a build queue.** Bigtable = storage ops only (does not lift precision).
Product levers already mapped to Wave 7 (EPIC-017/018/019/020 + EPIC-015 L3)
and Cortex orchestrator depth; no parallel epic.
**Condition:** EPIC-018 instrument green OR founder names specific papers to
promote into epic acceptance / Cortex golden rules. Until then: read only;
do not spawn tickets from paper titles.

## P-DMS-30 - Build-with-AIP gallery / Workshop-class product clones (F29)

Founder ask: copy Palantir Build-with-AIP examples catalog (Agent Studio,
Workshop, Contour, Quiver, Pipeline Builder, OSDK, Cipher, writeback, media)
as perfect UI + wired backends; also ontology docs "best practices" structural
guidance as product inspiration.
**Parked under H6 / PRD section 3.** Not a ticket queue. Thin adjacent work
that *is* in product: deferred **EPIC-023** (What's New + guided tour over
existing DMS surfaces only — Spaces/Chat/Studio/Library/Ontology + E9 honesty;
UI-only, no fake backends). Generative apps remain **EPIC-022**
(precision-gated). Ontology stays Cortex-authored; DMS renders.
**Condition:** founder amends H6 / section 3 to unlock either (a) a docs-only
Build-examples gallery with no backend clones, or (b) named product surfaces
as new PRD epics with stated swap scenarios. Until then: inspiration reading
only; do not file Workshop/Contour/Quiver/Cipher/OSDK/Agent Studio tickets.

## P-DMS-31 - Palantir SuperRepo / Foundry CLI / Marketplace clone (F30)

Founder paste (2026-08-05): Palantir SuperRepo beta (2026-08-04) — pro-code
monorepo of Ontology + functions + React as one versioned artifact; Foundry CLI
local preview (TS Ontology-as-code, embedded Ontology, auto-gen OSDK, seed);
Marketplace signed-bundle deploy (`env.yml`); Ontology Manager type import.
**Not a DMS tour.** Maps internally to **EPIC-022** (precision-gated) + Cortex
**P14** dual-brain / ENGINE_SDK + **P17** packaging + packs YAML ontology + O4
Agent SDK. External SuperRepo/Foundry-CLI/Marketplace clone stays **H6**.
**Do not clone this sprint.** Do not file SuperRepo / Foundry CLI / Marketplace
tickets. Netie already has: `packs/dms/ontology/*.yaml`, O4 `agent_sdk`, O7
`new_pack.py`, DMS as separate React consumer, compose + cortex-contract pins.
Missing: monorepo single artifact, CLI preview+TS OSDK codegen, Marketplace
signed bundle.
**Condition:** EPIC-018 precision gate green **and** founder amends H6 if any
external SuperRepo-parity claim is wanted; else internal dual-brain work stays
on Cortex P14/P17 under EPIC-022 unlock only. F29 closed **A** (independent).

## P-DMS-32 - Architecture selector on ask path (F31)

Founder ask: meta-chooser so the orchestrator itself picks Gen / C / JEPA / FSM /
DAG vs linear FreeRoute verify loop ("AI smart" architecture-level thinking).
**Not a DMS epic. Not a sixth port.** Lives in Cortex (gen-cFSM G1.0/G1.1 shipped;
enterprise loop = Cortex **P21** / `ENTERPRISE_GEN_CFSM_LOOP_PLAN.md`). DMS ask
path stays linear verify until unlock. Honesty: never claim trained JEPA shipped.
Chooser must not bypass envelope E1–E9, abstain-over-invent, or five ports.
**Condition:** EPIC-018 instrument green **and** measured linear-refine bottleneck
**OR** founder names an ask-path policy switch. Until then: research/continue
Cortex P21 only; do not file gen-cFSM / JEPA / DAG-chooser tickets in DMS.
Hostile/adversarial use cases meant to fail green land under EPIC-018 eval pack
(not a parallel epic).

## P-DMS-33 - L4/L5 answer badges (founder playground ask)

Founder wants "L3 to L5 automation like best" (Genie-class). Product ladder today
stops at **L3** (doc-RAG prose only). Playground labels L4/L5 as **aspirations**
only (`playground/questions.yaml`), not badges.
**L4 candidate meaning:** multi-step / synonym / encoding automation that stays
precise (EPIC-019 trusted assets + value-norm + linear verify).
**L5 candidate meaning:** steward registers trusted metrics; system answers from
them (EPIC-019 VQ-02 + EPIC-021 semantic).
**Condition:** founder confirms L4/L5 definitions (or declines new badges). Until
then: no L4/L5 badge in envelope; build via 019/021; playground is the probe.

## Move out of parking lot

Claim in STATUS, then strike here.
