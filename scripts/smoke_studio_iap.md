# DEMO-HOST-02 -- measured Studio host-online smoke

**Ticket:** #164 under EPIC-008 (#8). **Does not close** #164 or #8.
**Depends on:** Platform IAP/CF tunnel UP + DEMO-HOST-01 runbook (section 2.1).
**Does not replace:** `scripts/verify_demo_live.py` (prove/laptop loopback certify, already 31/31). Do not weaken it. Do not invent a re-run green from this path.

## Hostname ROTATE RISK

Platform CONFIRM 2026-09-13: Studio is a **temp Cloudflare quick tunnel**. Durable hostname waits on founder GO `TUNNEL_TOKEN`.

If `GET /` 404s or TLS fails, the hostname rotated. Ask Platform for the current origin. Do not open `:8090`. Do not invent COMPLETE for EPIC-008. Do not invent a durable host.

Temp origin used for the walk below (may already be stale):

`https://occurred-guest-guaranteed-practitioners.trycloudflare.com`

## Who certifies what

| Seat | What it may measure | What it must not claim |
|------|---------------------|------------------------|
| Founder / Platform on prove | `LOCAL_TUNNEL_PORT` -> `http://127.0.0.1:{port}/health` (API bind stays loopback) | public `:8090` |
| Any seat that can reach the CF origin | `STUDIO_ORIGIN` `/` + `/studio`; `DMS_API_BASE` `/health` + one ask | VPC-only loopback PASS |
| Cursor cloud | origin walk only if the temp CF URL is still live | sole certifying seat for VPC loopback; EPIC-008 COMPLETE |

Owner for tunnel / host / lock / rotate: **Platform/tunnel**.

## Env (fail closed -- no laptop default)

```
STUDIO_ORIGIN      required. Browser origin (https://<host>). No default.
DMS_API_BASE       required. Same-origin API prefix, usually {STUDIO_ORIGIN}/api
                   (alias STUDIO_API_BASE). SPA `/health` is HTML -- use /api/health.
LOCAL_TUNNEL_PORT  required for loopback PASS. Integer. Client always hits
                   http://127.0.0.1:{port}/health -- never 0.0.0.0.
```

Unset `STUDIO_ORIGIN` or `DMS_API_BASE` -> exit 2 CONFIG.
Unset `LOCAL_TUNNEL_PORT` -> loopback check BLOCKED `error.type=env.unset` (not PASS).

Do not set `LOCAL_TUNNEL_PORT=8090` on a cloud VM. That would probe **this** machine, not prove.

## Run

```powershell
# Fail-closed self-check (no network, CI-safe):
python scripts/smoke_studio_host_online.py --self-check

# Origin + ask (cloud or laptop that can see the CF URL). Loopback BLOCKED if port unset:
$env:STUDIO_ORIGIN = "https://occurred-guest-guaranteed-practitioners.trycloudflare.com"
$env:DMS_API_BASE  = "$env:STUDIO_ORIGIN/api"
python scripts/smoke_studio_host_online.py

# Full host-online (Platform on prove, after TUNNEL_TOKEN or current quick tunnel):
$env:LOCAL_TUNNEL_PORT = "8090"
python scripts/smoke_studio_host_online.py
```

This is **not** a GitHub CI job. GCP/IAP is not assumed. Record walked outputs here when the live path moves.

Must not: bind `0.0.0.0`, open public `:8090`, touch lake / `LIVE_KEY_ID`, upload (lake freeze -- SQL point or upload is a founder/Platform walk).

## Recorded walk -- 2026-09-13 cloud seat

Seat cannot see prove `127.0.0.1:8090`. `LOCAL_TUNNEL_PORT` left unset. Upload skipped (lake freeze).

```
GET https://occurred-guest-guaranteed-practitioners.trycloudflare.com/
  status=200 ctype=text/html  title=netie DMS

GET https://occurred-guest-guaranteed-practitioners.trycloudflare.com/studio
  status=200 ctype=text/html  title=netie DMS  (SPA)

GET https://occurred-guest-guaranteed-practitioners.trycloudflare.com/health
  status=200 ctype=text/html  (SPA -- not the API. Use /api/health.)

GET https://occurred-guest-guaranteed-practitioners.trycloudflare.com/api/health
  status=200 ctype=application/json
  keys: status, product, version, contract, ask_mode, demo_fallback,
        backend, database_configured, database, dependencies
  status=ok product=dms ask_mode=live demo_fallback=false
  database.backend=postgres persistent=true
  dependencies.cortex.ok=true  (url=http://127.0.0.1:8010/health on host)
  dependencies.openvault.ok=true

GET https://occurred-guest-guaranteed-practitioners.trycloudflare.com/api/v1/spaces
  status=200  Finance + Warehouse Ops  persisted=true storage.backend=postgres

POST /api/v1/chat/ask  {"question":"Top 5 selling SKUs by revenue", "space_id": Finance}
  status=503
  error.type=live_ask_failed
  message: IO Error: Could not set lock on file "/var/cortex/data/dms_demo.duckdb":
           Conflicting lock is held in /usr/bin/python3.11 (PID 176807)
  owner=Platform/tunnel  (host DuckDB writer -- do not kill from dms; do not open :8090)

GET 127.0.0.1:LOCAL_TUNNEL_PORT /health
  BLOCKED error.type=env.unset owner=Platform/tunnel
```

**VERDICT from this seat: BLOCKED** (loopback unset + ask `live_ask_failed`). Origin `/` + `/studio` + `/api/health` keys measured. Not PASS. Not EPIC-008 COMPLETE.

R-0003: a different run on prove with `LOCAL_TUNNEL_PORT` set, after the lake lock clears, walks the envelope. Platform owns that.

## Exit codes

| Code | Verdict | Meaning |
|------|---------|---------|
| 0 | PASS | origin + health keys + loopback health + ask envelope |
| 1 | FAIL | reached and contradicted (HTML health, memory backend, green-on-abstain) |
| 2 | CONFIG | required origin/API env missing or `0.0.0.0` |
| 3 | BLOCKED | cannot finish; print `error.type` and owner=Platform/tunnel |
