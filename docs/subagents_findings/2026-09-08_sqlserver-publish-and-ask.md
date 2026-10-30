---
keywords: [sqlserver, extract-object, publish-and-ask, silver, bignum, sales_sku]
main_idea: SQL Server fake extract lands bronze then objects.* + silver + certified chart (gold unsigned). Studio Publish and ask is one click to Chat. Multi-table extract picker. Live sales_sku_metric L0 bignum 3. Goal open until #116 real engine.
---

# SQL Server extract and Publish-and-ask (2026-09-08)

PREFLIGHT: HIT studio-ask-object-handoff.

## What landed

- `test_sqlserver_extract_object_tool_ask_is_certified_chart`: extract-only -> human-named object -> silver TABLE -> L0 bignum. Gold stays unsigned.
- Studio `Publish and ask` publishes then hands Chat the tool. Multi-table extract picks which bronze table to publish.
- Extract drafts via `objectDraftFromBronze` (digit stems stay legal).

## Verify

- `pytest tests/test_extract_object_ask.py` (3 passed)
- `npm test -- src/lib/objectAskHandoff.test.ts` (5 passed)
- Browser Finance: bronze.sales_sku -> Publish and ask -> Chat certified, row_count=3, bignum

## Not done

#116 live MySQL/SQL Server host. Vault sealed. Goal OPEN.