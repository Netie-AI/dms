# 2026-09-07 live objects + metric chart

Keywords: 8090, PYTHONPATH, E:\DMS, objects, digit-prefix, stock_on_hand, clean_sale, sealed, metric_tool

Main idea: Recycled :8090 with pytest PYTHONPATH (editable finders still point at missing E:\DMS). Metric tools answer while the vault stays sealed. Object publish now accepts bronze tables that start with a digit.

## Verify
- GET /openapi.json has /v1/studio/objects
- POST /v1/chat/ask question=stock_on_hand -> L0_CERTIFIED route=metric_tool chart.kind=bignum (no /keys/intermediate)
- POST /v1/studio/objects relation=bronze.01_clean_sales -> objects.clean_sale + silver.clean_sale; invent-join grounded on objects.* ABSTAINs
- Chat UI localhost:3000 stock_on_hand shows certified + Insights + 7,890 card
- pytest tests/test_object_views.py tests/test_extract_object_ask.py tests/test_db_connector.py::test_mysql_ingest_lands_in_bronze tests/test_sql_source_route.py::test_mysql_kind_is_accepted tests/test_metric_tools.py::test_grouped_tool_ask_is_pie_with_insights
- npm test -- src/components/SimpleChart.test.ts

Streaming extract (2026-09-07 later): SQL pulls fetchmany in 10k chunks into bronze. pytest 49 passed including test_streamed_pull_writes_chunks and test_mysql_extract_grouped_tool_is_pie. Studio tool SQL is steward-authored (GROUP BY -> pie). Gold stays signed promote.

Does not prove: live MySQL/SQL Server credentials (#116 verify-agent), Cortex LLM asks (vault sealed).

cite: distill none