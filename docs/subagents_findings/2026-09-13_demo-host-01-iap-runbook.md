---
keywords: [DEMO-HOST-01, EPIC-008, IAP, Cloudflare, host-harden, runbook, SQLSRC-09, dms-163]
main_idea: "Prove host-online is Platform-owned (IAP to loopback :8090). dms docs the 2-min Studio walk; no public :8090; tunnel recipe is not in this repo."
models: [grok-4.6]
workflow: ticket-runner
reuse: golden_rule
status: raw
cite: agent: this-goal
repo: DMS
date: 2026-09-13
---

# DEMO-HOST-01 prove IAP runbook

PREFLIGHT: PARTIAL (sqlsrc-09-studio-form; epic-completeness-003-008-017-018)

## Main idea

host-harden + IAP/CF to `127.0.0.1:8090` is Platform/DevOps. This repo documents the steward walk in `docs/DEMO_RUNBOOK.md` section 2.1. SPA is same-origin `/api` (Vite proxy / Caddy). Studio/SqlSourcePanel do not claim laptop-only. No bind change. EPIC-008 is not COMPLETE.

## Re-derive

- Ports: laptop `:3000` / `:8090` vs prove Studio URL + loopback API.
- SQLSRC-09: `SqlSourcePanel` -> `POST /api/v1/studio/sources/sql`.
- Org listing 2026-09-13: no `Netie-AI/netie-platform` / `platform` / `infra` repo. Cite EPIC-008 #8 GO; do not invent tunnel commands.

## Does not prove

Walking `{STUDIO_URL}` (DEMO-HOST-02). Tunnel UP. EPIC-008 COMPLETE.
