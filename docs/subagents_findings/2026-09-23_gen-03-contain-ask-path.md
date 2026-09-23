# GEN-03 contain ask_path (dms#194)

Date: 2026-09-23
Keywords: GEN-03, ask_path, DMS_HARNESS_ASK_PATHS, bind_plan, /dms/query, ask_path_not_allowed, DR-0004, dms-194
Main idea: "POST /v1/chat/ask refuses ask_path exact|generative unless DMS_HARNESS_ASK_PATHS. live_ask never bind_plan or POST /dms/query. D03 probes not confident. Product 26/26 verdicts identical, compute 14->0. Not COMPLETE."

## What shipped

PR: https://github.com/Netie-AI/dms/pull/246
Branch: `cursor/gen-03-contain-ask-path-b6e2` off `dff2a6ea`. Did not reuse `claude/gen-03-contain-ask-path` / PR #195.

## Ceiling

Isolated gen on a customer origin 400s. Upgrade: measurement origin with `DMS_HARNESS_ASK_PATHS=1` after Cortex emits typed query_plan (not this ticket). GEN-07 still owns moving pre-gates.
