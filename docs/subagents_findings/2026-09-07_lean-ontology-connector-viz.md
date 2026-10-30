---
keywords: [goal, postgres, object-views, metric-tools, pie, studio-sql, DR-0005, palantir-lean]
main_idea: First goal slice landed extract-only Postgres plus Studio SQL form, DuckDB objects.* VIEWs over bronze, allowlisted metric tools, and pie charts. Not DB-GPT/Harness/Semantica clones. Streaming and vault #116 remain.
---

# Lean ontology slice (2026-09-07)

PREFLIGHT: HIT F87. DISTILL connector+viz into DMS. COPY-none of DB-GPT/Harness/Semantica.

## What landed

- Postgres is a third extract-only kind beside mysql/sqlserver. Studio can POST it. No DuckDB ATTACH.
- `objects` lake schema. `publish_object_views` creates human-named VIEWs over bronze/silver/gold only.
- `metric_tools.call_metric_tool` runs authored SELECT by name. LLM sees name+description only.
- SimpleChart pie. Cortex `chart_spec.type=pie` maps on the envelope.

## Tests

74 passed this slice (`test_db_connector`, `test_sql_source_route`, `test_object_views`, `test_metric_tools`, `test_ask_mapping`, `test_library_promote_nodes`, extract-only, lake-schema concurrent).

## Not done (goal stays open)

- Live #116 (vault unseal)
- CDC/streaming of every engine
- DB-GPT UI/AWEL, DeepSeek Harness orchestrator, Semantica RDF
- Metric tools are now on live_ask (see `2026-09-07_dbgpt-distill-metric-ask.md`)