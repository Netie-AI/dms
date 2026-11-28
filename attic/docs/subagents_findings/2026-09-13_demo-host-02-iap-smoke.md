---
keywords: [DEMO-HOST-02, EPIC-008, IAP, Cloudflare, host-online, smoke, dms-164, rotate]
main_idea: "Host-online smoke fails closed without STUDIO_ORIGIN / DMS_API_BASE / LOCAL_TUNNEL_PORT. Temp trycloudflare ROTATE RISK. Ask BLOCKED live_ask_failed (DuckDB lock) from cloud seat. Not COMPLETE."
models: [grok-4.6]
workflow: ticket-runner
reuse: golden_rule
status: raw
cite: agent: this-goal
repo: DMS
date: 2026-09-13
---

# DEMO-HOST-02 measured host-online smoke

PREFLIGHT: HIT (demo-host-01-iap-runbook)

## Main idea

`verify_demo_live.py` remains loopback 31/31. This ticket adds `scripts/smoke_studio_host_online.py` against the Platform origin. Unset env is not PASS. Loopback is VPC/prove (`127.0.0.1:LOCAL_TUNNEL_PORT/health`). No public `:8090`. No durable host until `TUNNEL_TOKEN`.

## Re-derive

- DEMO_RUNBOOK 2.1: temp CF `https://occurred-guest-guaranteed-practitioners.trycloudflare.com` (rotate).
- SPA `/health` is HTML; API is `{origin}/api/health`.
- 2026-09-13 cloud walk: `/` 200, `/studio` 200, `/api/health` 200 postgres live. Ask 503 `live_ask_failed` DuckDB lock on `/var/cortex/data/dms_demo.duckdb` PID 176807. Owner=Platform/tunnel.

## Does not prove

EPIC-008 COMPLETE. Durable hostname. Loopback from a cloud seat. Envelope PASS.
