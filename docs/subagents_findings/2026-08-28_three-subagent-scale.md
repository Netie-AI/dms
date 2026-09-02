# three-subagent-scale

Keywords: accuracy, surface, delivery, genie, constructor, excel-mcp, modes, ontology
Main idea: Scale three Cursor project subagents until DMS hits Genie/Cortex-Analyst precision, CEO-usable dual UI modes, and a gated ontology-to-Excel/PowerBI last mile. Do not invent a fourth product.

## Files

- `E:\DMS\.cursor\agents\dms-accuracy.md`
- `E:\DMS\.cursor\agents\dms-surface.md`
- `E:\DMS\.cursor\agents\dms-delivery.md`

## Map to existing SoT (not new epics until PRD says so)

| Lane | Agent | Existing |
|------|-------|----------|
| Accuracy | dms-accuracy | EPIC-017 #33, EPIC-018 #35, EPIC-019 #38, envelope E9/E12, ontology.py grain |
| Surface | dms-surface | cream/graphite in TopBar+index.css, EPIC-023 gated, UI plan rejects Superset chrome |
| Delivery | dms-delivery | EPIC-016 Excel Copilot, POWERBI_DUCKLAKE export, EPIC-022 precision-gated, Constructor separate repo |

## Measured this session (live :8090, Cortex :8012)

- Hostile pack: precision-on-answered 100.00 pct (0/0), coverage 0/11, PASS 0 WRONG.
- Named-xlsx + demo `FROM transactions` -> ABSTAIN (xlsx demote). Stale uvicorn
  without `--reload` was the 0/3 WRONG; source already had the guard.
- E12: `total ... by country/category` was demoted as a one-number ask. Fixed.
  Finance spend answers; Warehouse Ops refuses `suppliers`.
- CEO Ask mode: Library **Ask about this table** grounds Chat (verified in
  browser on bronze.encoding_value_norm_Sales). Cream does not link to Studio.
  Suggested asks include live-curated spend-by-country plus the categoty trap.
- Constructor `--ingest`: live catalog -> `bronze.constructor_objects` (6
  objects, ingested=1). Serving sync locked (Cortex PID holds DuckDB). Not
  merged into ontology.py. Foundry still refused.
- Curated CEO pack (`scripts/score_curated.py --live`): 10/14 L0 (Ops
  shipment cost included), 4 expected abstains, 0 WRONG.
- Constructor `--ask` Space routing: 5/5 grantable L0; shipments -> Ops;
  alerts ungranted. Not merged into ontology.py.
- Hostile live after bronze serving sync: 6/11 correct L0, coverage 54.55 pct,
  0 WRONG. Later same day: two waves 71.43 pct then 64.29 pct (serial confirm
  71.43 pct), precision 100.00 pct, 0 WRONG. Exact SKU-BETA / Kuala Lumpur
  certify; BETA / KL / F32 / RAG abstain. Do not start EPIC-022 while 017 open.
- Cream Chat: suggested asks are exact certified wording + categoty trap.
  `ceoSafeHref` keeps Ask mode off Studio/Ontology/Audit.

## Explicit refuses (encoded in agents)

- No CortexOS import
- No Constructor merge into `scripts/ontology.py` (F71)
- No Foundry CLI/Marketplace clone (H6 / P-DMS-31)
- No MCP-into-customer-MSSQL (F27)
- No regex intent cascade as the accuracy path (F28)
- Excel source-only in Python; MCP/COM last-mile is visualization, not warehouse write
