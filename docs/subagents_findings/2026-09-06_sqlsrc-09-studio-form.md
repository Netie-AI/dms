---
keywords: [SQLSRC-09, Studio, sql source, dms-158, empty 500, password]
main_idea: "Studio form POSTs /v1/studio/sources/sql. Empty proxy 500 hid the error until describeApiError always returned a sentence. Password is request-only and redacted."
models: [grok-4.6]
workflow: ticket-runner
reuse: golden_rule
status: raw
cite: agent: this-goal
repo: DMS
date: 2026-09-06
---

# SQLSRC-09 Studio point-UI

PREFLIGHT: PARTIAL (epic020-f0019-worktree; sqlsrc-08-bridge-landed)

## Main idea

`POST /v1/studio/sources/sql` was live (#112). Studio had no form. SQLSRC-09 is UI+receipt: `SqlSourcePanel` calls `postSqlSource` -> `/api/v1/studio/sources/sql`. Receipt copy in `sqlSourceReceipt.ts` renders #156/#157 `links` / `violations`. Password is an uncontrolled field, cleared after the request, stripped from error copy.

Merged as #160. Browser verify on a down API: Vite proxy empty 500 -> `describeApiError("")` -> `{err &&` hid the error. Fix in #161.

## Verify

```
cd apps/ui && npm test
```

94 passed. Does not prove a live SQL Server pull (#116). Does not prove Chat consults the ontology.

## Promote?

Yes: empty-body fail-closed is a customer-visible hole on the merged form.
