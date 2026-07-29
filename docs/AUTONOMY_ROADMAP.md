# AUTONOMY_ROADMAP — governed AI

## Goal

Warehouse AI agents that process data like ChatGPT for Excel/DBs — **never silent writes**.

## Loop

```
detect → draft proposal → AI check → second ask (confirm)
  → apply (txn) → verify re-read → ledger receipt
```

On revise: **new proposal version**; old confirm token dies.

## Levels

| Level | Behavior | Gate |
|-------|----------|------|
| L0 | Answer only (Q&A) | Abstain-safe retrieval |
| L1 | Propose amend / ingest plan | Steward sees diff |
| L2 | Auto-draft on schedule (watcher) | Confirm before apply |
| L3 | Limited auto-apply | Dual-control + policy allowlist — **later** |

## Cortex role

Heavy planning / OSR / DAG may call Cortex. Product confirm + ledger stay in DMS.

## OpenVault role

Any LLM wording for diffs/explanations goes through OpenVault. No raw keys in DMS.

## Prerequisites before L2+

1. Postgres ledger + RLS  
2. Versioned Proposal + idempotency token  
3. Space-scoped ACL in data plane  

Do not ship autonomous apply before those exist.
