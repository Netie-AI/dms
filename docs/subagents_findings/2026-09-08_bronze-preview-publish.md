---
keywords: [studio, bronze-preview, objectDraft, sales_sku, digit-stem, c_01]
main_idea: Manager publishes objects.* from an already-landed bronze leaf. Digit bronze stems become c_01_ not objects.object. Live sales_sku_metric L0 bignum 3. Goal open until #116 real engine.
---

# Bronze preview publish (2026-09-08)

PREFLIGHT: HIT studio-link-verify.

## What landed

- Object form used to require a fresh SQL extract receipt. Clicking Bronze now drafts `objects.<name>` + tool SQL from the preview.
- Digit-leading stems (`bronze.01_clean_sales`) name `c_01_clean_sales`, not `object`.
- Studio live: bronze.sales_sku -> objects.sales_sku + silver.sales_sku. Chat `sales_sku_metric` L0 bignum 3.

## Verify

- `npm test -- src/lib/objectDraft.test.ts` (4 passed)
- Studio click bronze.sales_sku -> Publish -> Objects (2)
- POST /v1/chat/ask sales_sku_metric -> L0_CERTIFIED bignum 3

## Not done

#116 live MySQL/SQL Server extract. No host :3306/:1433. Goal OPEN.