---
keywords: [lineage-05, playwright, company-default, space-switcher, fetchSpaces]
main_idea: "selectOption('') is a no-op; fetchSpaces treats null company-default as unset and snaps back to Finance, hiding silver/gold nodes."
models: [grok-4.6]
workflow: worktree-continue-then-pr
reuse: lineage-03-library-node, library-tree-duckdb-config
status: raw
cite: agent: this-goal
repo: DMS
date: 2026-09-03
---

# LINEAGE-05 company-default Space race

PREFLIGHT: PARTIAL
reuse: lineage-03-library-node, library-tree-duckdb-config
spawn: skip

## Golden rule

> Company (default ACL) is `activeSpaceId === null`. `fetchSpaces` must not treat null as uninitialized. Playwright must select by label after `/v1/spaces` returns.

## Symptom

Promote wrote `silver.e2e_sales` (receipt 100/90/10). Library stayed on Finance. Silver/Gold folders are company-default only (LINEAGE-03), so `promote-node-silver.e2e_sales` never appeared.

## Fix

- Spec: `selectOption({ label: "Company (default ACL)" })` after `/v1/spaces`.
- Product: `AppContext` keeps `prev === null` on the spaces fetch.

## Verify (ticket ports UI :3000, API :8090, Cortex :8010)

```
npx playwright test --reporter=list
python scripts/verify_demo_live.py
```

12/12 and 31/31 on 2026-09-03. CSV bronze, not SQL (#116). Not in CI.

Does not prove: dual-brain PaaS, Power BI, random online DB, matrix/kv-cache governed answers.
