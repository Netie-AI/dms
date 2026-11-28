---
keywords: [DEMO-HOST-01, EPIC-008, IAP, Cloudflare, studio.netie.ai, loopback, dms-163]
main_idea: "Prove walk origin is https://studio.netie.ai. GET / /studio /api/health 200 postgres live. :8090 stays loopback. Temp trycloudflare is not the walk. Studio lede names PostgreSQL. Not COMPLETE."
models: [grok-4.7]
workflow: ticket-runner
reuse: golden_rule
status: raw
cite: agent: this-goal
repo: DMS
date: 2026-10-05
---

# DEMO-HOST-01 Studio origin

PREFLIGHT: HIT (`2026-09-13_demo-host-01-iap-runbook.md`). That note still names the temp trycloudflare host. Epic #8 SoT is `studio.netie.ai`.

## Main idea

`docs/DEMO_RUNBOOK.md` section 2.1 walk URL is `https://studio.netie.ai/studio`. DMS API on prove stays `127.0.0.1:8090`. No public `:8090`. No `0.0.0.0/0`. DR-0004 Option A stands. Platform owns the tunnel.

## Probe (GET only, 2026-10-05)

`GET /` 200 HTML `netie DMS`. `GET /studio` 200. `GET /api/health` 200: postgres, persistent, `ask_mode=live`, `demo_fallback=false`. Cortex and OpenVault ok on the host. No upload. No ask. Not DEMO-HOST-02 PASS.

## UI

`apps/ui/src/lib/api.ts` fetches same-origin `/api`. `StudioPage` / `SqlSourcePanel` do not claim laptop-only. Studio lede now names PostgreSQL, matching the panel. No bind change.

## Does not prove

A steward point-or-upload plus ask envelope. EPIC-008 COMPLETE. #163 stays OPEN. Merge only after #337.
