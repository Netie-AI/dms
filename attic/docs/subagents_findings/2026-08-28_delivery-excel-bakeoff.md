# delivery Excel Copilot bakeoff + Constructor source

Keywords: excel-mcp, copilot-prompt, bakeoff, constructor, ontology-scale, epic-022, powerbi, superset
Main idea: Governed Excel Copilot system prompt + MCP playbook shipped; bakeoff bar chart matches envelope SUM and axis-0; Constructor bronze exists but chat cannot see it until serving sync; EPIC-022 still blocked on coverage 0/11.

## Pipeline step moved

Step 4 (viz bakeoff evidence) + step 2 (Constructor as SOURCE verify) + step 1 seam doc (next-company DESCRIBE). Step 3 generative apps still gated.

## Prompt

Path: `docs/EXCEL_COPILOT_GOVERNED_PROMPT.md`

## Bakeoff table (this laptop)

| Prompt / channel | Magnitude match | Visual rank | Ran? |
|------------------|-----------------|-------------|------|
| Excel MCP BarClustered + governed playbook | PASS (SUM=642969499.25, axis min=0, 10 CSV categories) | 1 (CEO can read ranks) | YES -- `E:\DMS\.tmp\insights_stock_bakeoff.xlsx`, shot `E:\DMS\.tmp\excel_bakeoff_bar.png` + MCP capture A1:L20 |
| Prior MCP chart (no system prompt) | PASS (same mechanics) | 1 (tied on magnitude) | Prior `insights_stock_chart.xlsx` -- prompt is the new delta |
| In-app SimpleChart | harness PASS | 1 for Chat default | `scripts/viz_bakeoff.py --self-check` |
| Power BI MCP | n/a | -- | NO MCP tool on this laptop. Recipe only: `docs/POWERBI_DUCKLAKE.md` single-file export |
| Apache Superset | refuse | -- | Not DMS chrome; harness refuses |
| Pointer / UACC / Playwright | control only | -- | UACC used for window list + full-screen save; not a product orchestrator |

**Winner (CEO reason):** Excel MCP clustered bar from the certified CSV -- numbers reconcile to the envelope total, axis starts at zero (no truncated-scale lie), categories are exactly the CSV set, and it needs no new chart library. Copilot/ChatGPT-in-Excel stays secondary: paste the system prompt from the same doc; do not trust it without the SUM gate.

## Constructor as SOURCE

- `bronze.constructor_objects` exists, COUNT=6 (inventory, suppliers, locations, shipments, transactions, alerts).
- Only in bronze -- no serving copy. Chat cannot query it as a governed metric until serving sync.
- DuckDB RW lock held (`Can't open ... different configuration`) -- matches STATUS "Chat sync locked when Cortex holds DuckDB".
- `scripts/constructor_source.py --self-check` PASS; Foundry dump refused. No merge into `scripts/ontology.py` (F71).

## Next-company scale

`docs/NEXT_COMPANY_ONTOLOGY_SCALE.md` -- copy grain compiler + DESCRIBE-backed relations, not `demo_ontology` product names.

## EPIC-022 blocker

Hostile live coverage still 0/11 (precision-on-answered vacuous). Generative Cursor apps over ontology stay refused.

## Files changed

- `docs/EXCEL_COPILOT_GOVERNED_PROMPT.md` (new)
- `docs/NEXT_COMPANY_ONTOLOGY_SCALE.md` (new)
- `docs/subagents_findings/INDEX.md` (this entry)
- Evidence (untracked .tmp): `insights_stock_bakeoff.xlsx`, `excel_bakeoff_bar.png`
