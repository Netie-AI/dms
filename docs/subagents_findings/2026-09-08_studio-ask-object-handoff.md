---
keywords: [studio, ask-this-object, askTool, objectAskHandoff, sales_sku_metric, bignum]
main_idea: Ask this object now hands Chat the authored tool name and auto-asks it. Live Finance sales_sku -> sales_sku_metric L0 bignum 3 without typing. Library ask-about-these stays un-tooled. Goal open until #116 real engine. Vault is sealed again.
---

# Studio Ask-this-object handoff (2026-09-08)

PREFLIGHT: HIT bronze-preview-publish.

## What landed

- `setGrounded` writes `groundedTablesRef` in the same tick so `ask()` does not send the previous scope.
- Studio `Ask this object` passes `askTool` + `askNonce`. Chat consumes once (`takeAskTool`) and auto-asks.
- Library "Ask about this table" still has no `askTool` (bronze can still hit Cortex SQL).
- Metric envelopes suggest the tool name. Chat hides generic BI chips while grounded on `objects.*`.

## Verify

- `npm test -- src/lib/objectAskHandoff.test.ts` (4 passed)
- `pytest tests/test_metric_tools.py::test_maybe_metric_ask_hits_name_and_alias` (1 passed)
- Browser Studio Finance: bronze.sales_sku -> Publish -> Ask this object -> Chat `sales_sku_metric` certified, row_count=3, bignum

## Not done

#116 live MySQL/SQL Server extract. No host :3306/:1433. Vault sealed. Goal OPEN.