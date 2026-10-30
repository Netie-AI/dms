---
keywords: [oss-landscape, DB-GPT, Databricks, Unity-Catalog, dlt, sqlmesh, dagster, dbt, mlflow, evidently, superset, rill, vega-lite, OpenLineage, great-expectations, distill, W-0006, F87, F88, F108, VQ, bronze, promote, chart_spec, forecast, retrain]
main_idea: Research input for prd-agent, not a PRD decision. 70 public repos read via gh api and docs, none cloned. Clone-and-rebrand is F87/F88 REFUSE and 8 of 70 are license-banned. What the survey found is six verified gaps in our own tree, none needing a competitor's code. Training or retraining a planner is a founder amendment.
---

# Research -- OSS landscape, what is worth extracting (2026-10-02)

PREFLIGHT: kb.py search no direct hit. Followed W-0006 / S-0006 (distill from docs, no clone). Reused F87 (DB-GPT REFUSE), F88 (Genie / Databricks product REFUSE), F108 (learn loop, parked), P-DMS-29 (writeups are research), `vendor/ANALYSIS.md` (EXTRACT patterns, never absorb).

Laptop-ASCII only. Status of this file: **input for prd-agent intake**. Not a ticket. Not implemented. Nothing in the tree was changed except this file and its INDEX row.

## What was asked, what was done

Founder paste 2026-10-02: clone every similar repo (DB-GPT, Databricks alternatives, ...), change the logo, make them the DMS ecosystem, across ingest -> mining -> training -> retraining -> answering -> insights -> visualisation.

| Part of the ask | Done | Why |
|---|---|---|
| Research the landscape | YES | 6 read-only lanes, ~70 repos, `gh api` + public docs |
| Clone them | NO | A bulk clone into `vendor/` was blocked by the session's auto-mode classifier ("untrusted code integration"). Not retried or routed around. W-0006 says no clone anyway |
| Change logo / absorb as ours | NO | F87 and F88 are already REFUSE. Dify and Chat2DB licenses forbid stripping branding. AGPL / ELv2 banned |
| Training / retraining pipeline | NO (mapped, see below) | NETIE.md: "Intelligence is bought." Training a planner is a constitution change |

## License gate

| Posture | Repos |
|---|---|
| Permissive, read for patterns | DB-GPT, DB-GPT-Hub, vanna, dataherald, M-Schema, sql-eval, metricflow, malloy, boring-semantic-layer, rill, unitycatalog, polaris, lakekeeper, ducklake, delta-rs, iceberg, nessie, dremio-oss, dlt, meltano, mage, dagster, prefect, kestra, dbt-core, sqlmesh, docling, markitdown, unstructured, great_expectations, OpenLineage, datahub, ragflow, LightRAG, graphrag, kotaemon, anything-llm, langflow, mlflow, evidently, feast, zenml, metaflow, autogluon, FLAML, statsforecast, prophet, darts, superset, redash, evidence, vega-lite, perspective, mosaic, plot, marimo, lida, data-formulator, vizro, echarts |
| MIT/Apache except an `ee/` dir (never use `ee/`) | pandas-ai, lightdash, onyx, cube (Apache + some MIT) |
| BANNED, concept from public docs only | WrenAI (AGPL, multi-licensed), Metabase (AGPL), Grafana (AGPL), Airbyte (ELv2), Soda (ELv2) |
| Not usable | PyCaret (FSL, competing use), Dify (modified Apache: no logo removal, no multi-tenant), Chat2DB (extra conditions), Flowise (archived) |
| Out of scope by prior decision | Spark, Trino, Hudi, Ray, OpenMetadata (F88 / constitution) |

## What I checked in this tree (the findings that matter)

Each was first read in the working tree of `feat/sqlsrc-08-ontology-on-the-route`, which is **86 commits behind and 18 ahead of `origin/main`** (local `main` is stale too, so `git diff main` is meaningless here). I then re-checked each against `origin/main` (f0e6c61): G1, G2, G3 and G4 still stand there; G6 is partly closed; G5 is unchanged in the working tree and was not re-read upstream. Several working-tree files also carry uncommitted edits. "Not verified" says what I did not run.

| # | Finding | Evidence | Not verified |
|---|---|---|---|
| G1 | A verified query is not pinned to a data version. `_verified_queries` has no schema/hash/ingest column, and `maybe_verified_ask` re-runs the SQL against whatever is now in the lake and stamps `L0_CERTIFIED`. A zero-row result is already withheld (`envelope.py:1105`), so the residual is a re-upload that changes what the SQL returns without emptying it (recoded filter value, repurposed column). Hard rule 12 adjacency | `verified_queries.py:66-74`, `:213-280`; `bronze.py:188-210` (`_record_ingest` is DELETE then INSERT, no history) | No repro run. Whether CCA encodings and ontology links have the same gap |
| G2 | SQL-extract bronze write is not atomic. `write_bronze_rows` drops the table, then streams batches with no staging and no transaction. A failure mid-stream leaves a partial table; `record_source_pull` only runs after success, so the registry row is stale or absent. The file-upload path already does stage-then-swap in a transaction, so the two paths disagree | `bronze.py:804` vs `:460-502`; `db_connector.py:614-639` | How the ask path treats a partial table with a stale or missing registry row |
| G3 | Expectations are advisory. A failed `*_not_null_rate` writes `expectation_fail:<key>=1` into `counts_by_reason`, does not touch `reconciled`, and the target is still written | `promote.py:532-553` | Whether any UI reads it as a gate (the quality lane says `PromoteReceiptPanel` lists it like a quarantine reason) |
| G4 | A chart is not checked against the rows it claims to draw. Cortex `chart_spec` x/y labels are forwarded without checking they exist in the rows; the fallback infers types from `rows[0]` only; `SimpleChart` floors bar scale at 1 and bar height at 2px, so values under 1 lose rank. Violates the spirit of E9 (a chart must not be a second source of truth) | `dms_executor/__init__.py:593-634`; `SimpleChart.tsx:47,58,134` | Whether a real answer has hit this. `SimpleChart.tsx` is mid-edit in the working tree |
| G5 | `Measure.additive` is descriptive only. The code says so honestly (nothing rolls a grouped result into a total), so this is NOT a wrong answer today. It becomes one the day a roll-up feature lands | `ontology.py:141-147` | -- |
| G6 | Bench runs leave no history. **Partly closed upstream:** on `origin/main` `score_curated.py` records a run id, `git rev-parse HEAD` and a per-case `score_cases_<run_id>_<sha>.jsonl`. `ontology_bench.py`, `cca_eval.py` and `score_answers.py` still do not (grep); `ontology_bench.py` writes one caller-named JSON path | `origin/main:scripts/score_curated.py:2749-2905`; `scripts/ontology_bench.py:89` | Whether output is overwritten or appended in practice; not run |

Note: two lane claims were weaker than reported once I read the code. The lakehouse lane's "live wrong-answer risk" for G1 is narrower (see G1). The NL2SQL lane's "silent wrong answer today" for G5 is not true (see G5).

## Pipeline map: stage -> what to extract -> from where -> where it lands

| Stage | Extract (pattern, not code) | Source | Lands in |
|---|---|---|---|
| Ingest | Stage-then-swap + column/type hash with additive-vs-destructive diff, freeze on destructive | dlt schema contracts, sqlmesh `SchemaDiff`, Iceberg field ids | `bronze.py`, `db_connector._pull_one` |
| Ingest | Freshness policy (`max_staleness` flags or abstains) | dagster `FreshnessPolicy`, dbt source freshness | `envelope.py`, pipeline YAML |
| Catalog / lineage | Version stamp (`input_versions`, `data_version`) on receipts and envelope sources | DuckLake `commit_extra_info`, Nessie `CommitMeta` | `dms_core/pipelines.py`, `promote.py`, `envelope.py` |
| Catalog / lineage | Emit-only OpenLineage-shaped receipt export | OpenLineage facets | `to_openlineage()` on `PromoteReceipt` |
| Transform / promote | Typed blocking checks with `error`/`warn` severity | dagster `@asset_check(blocking)`, dbt tests, sqlmesh audits, GE result shape | `promote.py`, `pipeline_loader.py` |
| Transform / promote | Plan-then-apply with a diff preview and plan hash | sqlmesh plan/apply, Feast `registry_diff` | `promote.py`, `routes/pipelines.py`, `StudioPage.tsx` |
| Semantic | Non-additive and ratio-of-sums measures; drill-across for two-measure asks | MetricFlow, Cube multiplied measures | `ontology.py` (EPIC-021 gated) |
| Verify | Pin VQ / binder / link to a dependency fingerprint, abstain until re-verified | DuckLake snapshots, evidently structural checks | `verified_queries.py`, `cca/binder.py` |
| Verify / eval | General result-set grader (order-aware, multi accepted answers, exact only); bench run registry with `last_green` | sql-eval `normalize_table`, DB-GPT comparator, mlflow run tags | `score_answers.py`, `main._bench_runs` |
| Answer (docs) | Span-exact citations: quote verified as a verbatim substring; page-cited parse; no OCR | kotaemon (but exact, not fuzzy), docling-core provenance | `document_chunks.py`, `SourcePanel` (EPIC-015 L3 only) |
| Insight | Deterministic insight cards computed from executed rows, each with a row index | Metabase x-ray idea (concept only) | `metric_tools.py`, `splitInsights.ts` |
| Visualisation | Chart-vs-rows validator; result-shape chart rules; small declarative spec with no aggregate/transform | Superset validation, Rill `create_chart`, Plot `autoSpec`, Vega-Lite subset | `__init__.py:593-634`, `types.ts`, `SimpleChart.tsx` |
| ML / "retraining" | Drift as exact structure and value-set checks that trigger re-verification, not model retraining | evidently structural metrics, mlflow digest | same as Verify row |
| ML / forecast | Bounded Predictive tool: backtest, abstain gates (short history, gaps, no beat on seasonal-naive) | statsforecast `cross_validation`, autogluon splitter | beside `metric_tools.py`, behind the `_UNOBSERVED_ASK` branch |

## Suggested routing (prd-agent decides; this is a proposal)

Ranked by value-for-DMS after de-duplicating across lanes. Lane = Tool division in CLAUDE.md.

| Rank | Idea | Closes | Effort | Lane | Adjacent open work |
|---|---|---|---|---|---|
| 1 | Dependency fingerprint on VQ / binder / link; mismatch falls through or abstains with a named reason | G1 | M | Claude Code (schema, tests) | #38 EPIC-019 remainder, #132 EPIC-CCA |
| 2 | Atomic SQL-extract bronze + schema-drift classification on re-pull | G2 | M | Claude Code | #108 / #116 EPIC-020 family |
| 3 | Typed expectations with severity as real promote gates; receipt carries `expectations[]`, not a counts hack | G3 | M | Claude Code | EPIC-024 lineage (closed) |
| 4 | Chart-vs-rows validator, drop the chart and show the table on any mismatch; result-shape rules | G4 | S-M | Cursor (UI) + Claude Code (validator, tests) | `viz_bakeoff.py` |
| 5 | Bench run registry (`git_sha`, pack digest, warehouse digest, `last_green`) | G6 | S | Claude Code | #299 grid runner, #292 row-compare judge |
| 6 | Version stamp on receipts and envelope sources | G1 aid | S | Claude Code | -- |
| 7 | General result-set grader, exact only | eval | S | Claude Code | #292 |
| 8 | Plan-then-apply promote with diff preview | G3 aid | M | Claude Code + Cursor | -- |
| 9 | Span-exact L3 citations; page-cited PDF parse (no OCR); invariant that chunk tables are unreachable from SQL / ontology / VQ | L3 | M | Claude Code | EPIC-015 |
| 10 | Freshness policy | -- | S | Claude Code | -- |
| 11 | Deterministic insight cards | insight | S-M | Claude Code | -- |
| 12 | Non-additive / ratio measures, drill-across | G5 | M | Claude Code | EPIC-021 gated until #116 |
| later | OpenLineage export, drill-through on cited rows, report-as-code, column tags driving grants | -- | S-L | -- | build only on a buyer ask |

None of these needs a third-party dependency. Where a swap scenario was named by a lane (statsforecast for Predictive, docling or pdfminer for the L3 parser), it is a candidate swap behind an existing port, not an adoption.

## Needs a founder amendment (do not build)

| Decision | The call | Risk |
|---|---|---|
| Distill or fine-tune a planner on checker verdicts | Let Netie train and host weights. Contradicts NETIE.md lines 17-18 and plane 1; F87 skipped DB-GPT-Hub | Turns Netie into a serving company. Training on the bench contaminates the bench. SME volume will not beat a frontier prompt |
| Cross-install learning | Installs report schema shapes to HQ | Breaks F99 and PRD-001 s.3 single-tenant promise and PDPA posture. Pack frames shipped one way are already recommended instead |
| Local pretrained time-series models | Chronos / Toto via autogluon, darts torch | Serving a model; weights licensing; unverifiable output |
| Bounded Predictive path | (A) closed-form per-question estimators with no stored weights are tools, not "building models"; (B) a non-fact PROJECTION badge sits outside the wrong=0 fact law | A user reads a projection as a promise. Keep the F-0021 guard on until both are confirmed |
| Thumbs / learn loop / failure ledger | F108 parked | -- |

## Rejected (with the rule)

- Absorb or rebrand any competitor product: F87, F88, `vendor/ANALYSIS.md` policy.
- Orchestrators as a dependency (dagster, prefect, kestra, mage, zenml, metaflow): five ports, no new dependency without a swap scenario.
- Catalog servers and table formats (Unity Catalog, Polaris, Lakekeeper, Nessie, Iceberg, Delta): F88, P-DMS-17. No measured writer contention.
- Second Text2SQL / compound-AI path, AWEL, Langflow, Dify builders: F87.
- LLM-judged correctness, auto-repair or auto-memory of verified queries: F95, F108, DR-0003.
- pandas-ai style code execution: bypasses the grain guard.
- Vector / graph index as the number path, RAGFlow cosine citations, kotaemon fuzzy span match: E9, wrong=0.
- LLM-generated charts (LIDA, Data Formulator, Vizro-AI): the chart becomes a second source of truth.
- Browser-side query engines (Mosaic with DuckDB-WASM, Perspective): bypass the governed executor, ACL and audit.
- Statistical-drift gating: low power under ~1000 rows, false alarms, blind to a meaning flip (1=active becomes 1=churned). Use exact structure and value-set checks; keep tests advisory.
- Live CDC, ATTACH, federation: DR-0005 extract-only.
- Charting libraries (echarts 368KB gz, Plot 128KB gz, Vega-Lite 87KB gz plus runtime): no swap scenario. Revisit only if buyers need more than ~6 chart types.

## Caveats

- Lane reports are model output read from READMEs and file trees. I re-read the code behind G1-G6 myself (G6 by grep only); the per-repo license reads are the lanes' own, except the NOASSERTION repos whose LICENSE heads I read directly.
- Not read: Polaris policy contents, Dremio reflections, dbt v1 internals, Kestra and Mage beyond structure, Docling model-weight licenses.
- `vendor/grafana` holds a stray `.git` (AGPL repo). It is gitignored. Delete it if you want nobody reading it by accident; I did not.
- Unverified: whether Cortex (engine side) already holds a version stamp or format version on the ledger; two ideas (audit-ledger format version, M-Schema-style prompt schema) are Cortex tickets, not DMS.
