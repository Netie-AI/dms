# FreeRoute bake-off

**When:** 2026-07-30T15:58:19Z
**OpenVault:** `http://127.0.0.1:5000`

## Notes

- Secrets stay in OpenVault (`keys.db`). DMS never stores API keys.
- FreeRoute `POST /v1/chat/completions` uses role-ordered fallback (primary→backup→cheap→free).
- Anthropic may be skipped by the `/v1` proxy today — record honestly.
- Parallel OmniRoute Electron bake-off is **not** shipped; use precheck latency + one FreeRoute call.
- Add keys via UI `http://127.0.0.1:3010/vault` or `POST /api/vault/ingest-env`.

## Precheck-all

- ok: `True`
- vault key records listed: **6**
- precheck result rows: **6**

| provider / label | status | latency_ms | error |
|---|---|---|---|
| ? | error |  | All connection attempts failed |
| ? | error |  | All connection attempts failed |
| ? | error |  | All connection attempts failed |
| ? | ok | 293.4481999982381 |  |
| ? | ok | 403.4073999937391 |  |
| ? | auth_fail | 256.4325999992434 | HTTP 401 |

## FreeRoute chat probe

- prompt: `In one sentence: what is 2+2? Reply with only the number.`
- status: `400`
- latency_ms: `6273.3`
- preview: ``
- error: `{"error":{"message":"request rejected by upstream (non-retryable)","type":"openvault_non_retryable","reason":"non_retryable","details":["Netie Cortex (seeded): All connection attempts failed","LiteLLM Proxy (seeded): All connection attempts failed","OPENROUTER_API_KEY: HTTP 402 (quota_exhausted)","G`
- note: Sequential FreeRoute fallback — not a parallel OmniRoute Electron bake-off.

## Escalation

If free tiers suck, add Claude / OpenAI / DeepSeek into OpenVault (not into DMS `.env`),
then re-run `python scripts/bakeoff_freeroute.py`. Warehouse answers still route via Cortex.
