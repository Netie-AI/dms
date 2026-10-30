---
keywords: [objectDraft, GROUP BY, pie, hbar, inferGroupedToolSql, publish-and-ask]
main_idea: Preview rows draft GROUP BY tool SQL. Publish and ask on blank_rows_hanging_Sales_Clean yielded L0 chart + insights (6 sku groups). COUNT(*) stays when there is no measure. Goal open until #116.
---

# Grouped tool draft from preview (2026-09-08)

PREFLIGHT: HIT sqlserver-publish-and-ask.

## What landed

- `inferGroupedToolSql` turns a category + numeric preview into authored GROUP BY SUM.
- Studio drafts from bronze preview rows. Extract fetches preview after land.
- Live: bronze.blank_rows_hanging_Sales_Clean -> Publish and ask -> certified, 6 sku groups, insights, chart.

## Verify

- `npm test -- src/lib/objectDraft.test.ts src/components/SimpleChart.test.ts` (9 passed)
- Browser Finance Studio Publish and ask on blank_rows_hanging_Sales_Clean

## Not done

#116 live SQL host. Vault sealed. Goal OPEN.