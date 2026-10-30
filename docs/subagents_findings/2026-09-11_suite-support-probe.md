---
keywords: [suite-support, OpenVault, Cortex, DMS, health-probe, dms-116, SQLSRC-06, vault-unsealed, live-ask-skipped]
main_idea: Support probe 2026-09-11. OV :5000 healthz 200, vault unsealed. Cortex :8011 200. :8010 and :8090 connection refused. Live ask skipped; #116 stays open. OV blocker from 2026-09-10 sqlsrc06-ask-503-ov is cleared; DMS down is the remaining Chat blocker.
---

# Suite support probe -- 2026-09-11

PREFLIGHT: HIT (`2026-09-10_sqlsrc06-ask-503-ov`, `2026-09-10_jwks-health-timeout`).

Role: medium-thought support probe. No product edits. No Docker. No service restarts or kills.

## Results

| Port | Path | Status | One-line body |
|------|------|--------|---------------|
| 5000 | /api/healthz | 200 | `{"status":"ok","service":"openvault",...}` |
| 5000 | /api/vault/status | 200 | sealed **false** (passphrase configured) |
| 8011 | /health | 200 | `{"status":"ok","pack":"dms"}` |
| 8010 | /health | DOWN | Unable to connect to the remote server |
| 8090 | /health | DOWN | Unable to connect to the remote server |

## Ask

Skipped: DMS :8090 not listening. Live ask not proven this tick.

## Notes

- OV :5000 accepting again clears the 2026-09-10 `10061` OpenVault connection-refused blocker for the engine path.
- DMS :8090 down still blocks Chat POST regardless of OV/Cortex :8011 health.
- :8010 down is consistent with prior sessions; JWKS nested-timeout caveat applies only when DMS /health is up but `cortex.ok` is false.

## Verdict

**SUITE: support DOWN** -- close nothing; #116 stays open.
