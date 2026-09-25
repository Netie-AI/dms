# BIRD Mini-Dev local run (500 PostgreSQL questions)

Keywords: A1-02, bird_minidev, score_bird --minidev --live, insights_refused, InsightsAskIn, packs/dms ranking, default_readable, source_count, dms-264, dms-179, dms-279
Main idea: Full local stack (OpenVault + Cortex + DMS, loopback), 75/75 BIRD tables in bronze, 4 provider keys pooled. The repo harness scored 500/500 ABSTAIN, WRONG=0, EX on all = 0.00 pct. No LLM was called on any question: the shipped ask path has no route from a SQL-source Space to SQL generation.

Measure only. No product code changed. Not PASS. Not COMPLETE. This is not a model-capability number.

## Harness summary (verbatim, `scripts/score_bird.py --minidev ... --live`, no `--limit`)

```
n=500 answered=0 RIGHT=0 ABSTAIN=500 WRONG=0 GOLD_ERROR=0 (excluded from n)
EX on answered=n/a abstain rate=100.00 pct
  answered=0 bound n/a (nothing answered)
per db_id:
  california_schools n=30 RIGHT=0 ABSTAIN=30 WRONG=0 GOLD_ERROR=0
  card_games n=52 RIGHT=0 ABSTAIN=52 WRONG=0 GOLD_ERROR=0
  codebase_community n=49 RIGHT=0 ABSTAIN=49 WRONG=0 GOLD_ERROR=0
  debit_card_specializing n=30 RIGHT=0 ABSTAIN=30 WRONG=0 GOLD_ERROR=0
  european_football_2 n=51 RIGHT=0 ABSTAIN=51 WRONG=0 GOLD_ERROR=0
  financial n=32 RIGHT=0 ABSTAIN=32 WRONG=0 GOLD_ERROR=0
  formula_1 n=66 RIGHT=0 ABSTAIN=66 WRONG=0 GOLD_ERROR=0
  student_club n=48 RIGHT=0 ABSTAIN=48 WRONG=0 GOLD_ERROR=0
  superhero n=52 RIGHT=0 ABSTAIN=52 WRONG=0 GOLD_ERROR=0
  thrombosis_prediction n=50 RIGHT=0 ABSTAIN=50 WRONG=0 GOLD_ERROR=0
  toxicology n=40 RIGHT=0 ABSTAIN=40 WRONG=0 GOLD_ERROR=0
per difficulty:
  challenging n=102 RIGHT=0 ABSTAIN=102 WRONG=0 GOLD_ERROR=0
  moderate n=250 RIGHT=0 ABSTAIN=250 WRONG=0 GOLD_ERROR=0
  simple n=148 RIGHT=0 ABSTAIN=148 WRONG=0 GOLD_ERROR=0
served provider/model/local counts:
  unknown/unknown/local=false 456
  unknown/unknown/local=unknown 44
served_local true=0 false=456 unknown=44
plan_origin generate_sql=0 ontology_ranking=0 unknown=500
sha=0577275507b95b8116430f6a47b1d6e30ae06939 data_bytes=283525
setup_fingerprint=babe43a13f236a6d20a8bf3afe5a1f9bdbe17d576f8e2d1bb39714d7e1091520
MEASURED: WRONG=0. Not PASS. Not COMPLETE. Not a quoted Mini-Dev score.
```

EX on all (abstain counted as a miss) = 0/500 = 0.00 pct. Provider failures: 0 (0 retries, 0 after retry). Provider mix: none; every `served_provider` is unknown or null. FreeRoute freeze held: learn=0, store fresh, row_count 0 before and 0 after, same hash.

Official mini_dev README EX (SQLite / PostgreSQL): gpt-4 47.8 / 35.8, gpt-4-turbo 45.8 / 36.0, llama3-70b 40.8 / 29.4, TA+gpt-4o 63.0 SQLite. DMS as shipped: 0.00 PostgreSQL. The gap is plumbing, not model quality: no model saw a question.

## Expected vs actual

- Expected: BIRD questions on a Space holding the 75 BIRD tables reach Cortex generation (`plan_origin=generate_sql`), FreeRoute serves a pooled provider, and the harness grades the returned rows.
- Actual: 500/500 ABSTAIN in about 1 s each, `plan_origin` unknown, no provider served. Abstain reasons from the envelope text:

| Count | Gap | Where |
|------:|-----|-------|
| 249 | `insights_refused`: Cortex answers "no ontology path or metric for intent" | Cortex ranking |
| 110 | "cannot certify an ontology-grounded query" with no named gap | ask path |
| 52 | `unhonored_qualifier` (for example `time_filter=year=2013`) | ranked against demo-pack metrics |
| 45 | `unknown_measure` (for example `expired_last_month`) | ranked against demo-pack metrics |
| 44 | "nothing grants Space ... session grant names no sources" | Space grant |

The 97 `unhonored_qualifier` / `unknown_measure` cases are BIRD questions ranked against the supply-chain demo ontology. The qualifier and measure guards caught them. Without those guards they are the rule-12 shape: a plausible number and a green badge.

## Root-cause class (largest group, 249 `insights_refused`)

**Consumer-supplied context silently dropped at the engine wire. The engine then ranks against a fixed built-in pack.**

- Cortex `InsightsAskIn` (`CortexOS/insights/routes.py:30-37`) has only intent, question, ask, generate, session_id, space_id and consumer. DMS sends `mode=ontology_plan`, `ontology`, `model_preference` and `intent_slots` (`packages/cortex_client/cortex_client/compute.py:781-805`). Pydantic drops them without an error.
- `execute_insights` (`routes.py:157-163`) calls `run_insights` with no `pack_dir` and no `query_plan`. `_pack_dir` is hard-wired to `pack_dir_for("dms")` (`CortexOS/crew/insights.py:345-349`), so `PACK=` does not change it.
- An empty ranking plus `generate=True` refuses before `generative_ask` (`insights.py:1271-1279`). FreeRoute is never called.

Two DMS-side blocks sit behind it, so fixing Cortex alone does not make it answer:

- `default_readable = [t for t in granted if t in DEMO_TABLES]` (`packages/executor/dms_executor/__init__.py:572-573`). SQL-source bronze is never readable on the no-selection path. Any BIRD SQL would fail `validate_compiled_sql` as `ungranted:` (`generative_ask.py:554-568`).
- Selecting tables skips the generative lane (`generative_ask.py:956`), so `grounded_tables` is not a workaround.
- Nothing derives or stores an ontology for a SQL-source Space. The ONTO-STORE-01 `OntologyStore` has no non-test caller.

Four read-only tracer agents reached the same verdict, and an adversarial verifier confirmed it: no route, CLI, env flag or mode reaches SQL generation for this Space without a product code change. Per CLAUDE.md this goes to `prd-agent` as a defect against GEN-01 #179 / ONTO-STORE-01 #279. Not implemented here.

## Other defects seen (not fixed)

- `POST /v1/studio/sources/sql` landed 75 tables, but `GET /v1/spaces/{id}` still shows `source_count: 0` and `GET /v1/spaces/{id}/sources` returns 500. This is likely the 44 grant refusals.
- Row ceiling `DEFAULT_MAX_ROWS = 500_000` (`packages/executor/dms_executor/db_connector.py:39`). `trans` landed 500,000 of 1,056,320 rows (`truncated: True`). The other 74 tables match Postgres row counts exactly.
- The ingest of about 1 GB took about 2 h in one synchronous request (the client timed out at 50 min while the server kept going). The DMS access log was off, so progress was only visible in `pg_stat_activity`.
- The SQL-source path never syncs to Cortex serving: `scripts/sync_bronze_to_serving.py` refuses when the serving file does not exist. An empty DuckDB file had to be created first.
- The Mini-Dev loop has no try/except around ask (`scripts/bird_minidev.py:645`). One 429/5xx from a provider aborts all 500 with no artifact. This run used an out-of-repo wrapper: 1 s pacing, retries on 429/502/503/504, and a persistent failure returned as an abstained envelope (graded ABSTAIN, never RIGHT). It fired 0 times.
- `generate=true` with the default `dms-demo-viewer-key` abstains as `insights_bearer_missing` (BEARER-01, by design). A local run needs a matching steward key: Cortex `DMS_API_KEYS` and DMS `CORTEX_API_KEY`.

## Repro (Linux, loopback only)

1. Get `mini_dev_postgresql.json` and `MINIDEV_postgresql/BIRD_dev.sql` from the mini_dev Google Drive package (`minidev_0703.zip`, 800 MB). Hugging Face `birdsql/bird_mini_dev` has the JSON only, no dump.
2. Set up Postgres 16: `initdb` under /tmp, create role `xiaolongli` (the dump owner), `createdb bird_minidev`, `psql -f BIRD_dev.sql`. Result: 75 public tables, 0 errors. Add a read-only role for gold SQL and source extract.
3. Start OpenVault: `uv run openmw console --host 127.0.0.1 --port 5000 --no-open-browser`. Then `POST /api/vault/ingest-env`, dry run first. Pass `env_text` limited to the four provider names, because a bare scan also picks up `GH_TOKEN`/`GITHUB_TOKEN`. `/api/freeroute/status` should show `pooled_key_count 4` with all four prechecks ok.
4. Start Cortex on :8010: `PACK=dms OPENVAULT_BASE_URL=... DMS_L2_ENABLED=1 CORTEX_FREEROUTE_LEARN=0 CORTEX_FREEROUTE_SCOREBOARD=<fresh> DMS_API_KEYS=steward:<local>`. Then start DMS on :8090 with Postgres control plane, `DMS_DEMO_FALLBACK=0`, and `CORTEX_API_KEY=<local>`.
5. Run `POST /v1/spaces`, then `POST /v1/studio/sources/sql` (kind=postgresql). Stop Cortex, create the serving DuckDB file, run `sync_bronze_to_serving.py` (then `--check` returns 0), and restart Cortex.
6. Run `score_bird.py --self-check` (PASS). Then `--minidev <json> --live --limit 20`, then without `--limit`, with `BIRD_PG_DSN`, `DMS_API_BASE`, `BIRD_SPACE_ID`, `CORTEX_FREEROUTE_LEARN=0` and `CORTEX_ROUTE_STORE` set.

## Invariant

- **Silent fallback is a lie. Degradation has to show in the output.** Cortex drops `ontology`/`mode` with no error and substitutes the demo pack. The DMS envelope did show it (named ABSTAIN, WRONG=0), so the customer-visible contract held. The engine wire did not.
- **Assert the artifact the user actually receives.** A score read from Cortex or unit-level plumbing would not show that 0 of 500 questions reached a model. Only the envelope-level harness run shows it.

R4 held: WRONG=0 over n=500. Under the rule of three, WRONG=0 at n=500 bounds the true wrong rate below about 0.6 pct, but only for this abstain-everything configuration.
