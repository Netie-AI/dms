# Next-company ontology scale (one page)

What a second company copies. Not a PaaS. Not Constructor-as-engine.

## Copy these, not the demo tables

| Piece | Path | What to do |
|-------|------|------------|
| Grain compiler | `scripts/ontology.py` | Declare object types + links + measures; run `verify()` then `compile()`. Fan-out and unverified links refuse. |
| DESCRIBE, not hard-coded demo | `Ontology._object_columns` uses `DESCRIBE SELECT * FROM {rel}` | Point object `relation=` at the customer's bronze/gold tables after ingest. Do not require `demo_ontology()` product / txn_of_product names. |
| Ask-path insights | `scripts/ontology.py` + `demo_ask` / envelope | Prefer grain compile; live insights ride certified envelope. Note: `space_insights.py` is not on disk in this tree -- do not invent it. |
| Constructor as SOURCE | `scripts/constructor_source.py` | Fetch catalog HTTP -> CSV -> bronze, and `--ask` routes each object to a Space that grants it (`GET /v1/library/warehouse/tables`). Do not import Constructor into `ontology.py` (F71). |
| Envelope gate | `packages/executor` envelope + E1-E12 | Apps and charts consume certified envelopes only. |

## Second-company checklist

1. Ingest their schema (Excel/CSV today; extract-to-bronze for MSSQL/MySQL if F36 path a). No live ATTACH without founder.
2. Author ontology YAML/Python object+link+measure declarations whose `relation` names match DESCRIBE output.
3. `verify(con)` must pass on their keys (uniqueness, nulls, link cardinality). Demo AW lakes cannot prove this.
4. Ask path serves only verified measures. Hostile + curated live packs: 100% precision-on-answered, 0 WRONG, coverage 71.43% (10/14) each. Do not start EPIC-022 apps while EPIC-017 (Cortex#11) is still open.
5. Last-mile: export certified CSV -> Excel MCP playbook (`docs/EXCEL_COPILOT_GOVERNED_PROMPT.md`) or in-app SimpleChart. Power BI = single-file export only (`docs/POWERBI_DUCKLAKE.md`).

## Explicit non-goals

- No Constructor merge into DMS ontology compiler.
- No Foundry CLI / Marketplace clone.
- No inventing constructor.netie.ai or claiming app.netie.ai/cortex while 404.
- No generative Cursor dashboards until EPIC-018 is closed on GitHub and EPIC-017 completeness is true. Numeric hostile gate (100% precision, two waves, coverage >= 60%) is now met locally; do not ship apps on that number alone.
