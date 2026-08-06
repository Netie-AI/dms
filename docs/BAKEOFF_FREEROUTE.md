# FreeRoute bake-off

**When:** 2026-08-02T18:21:29Z
**OpenVault:** `http://127.0.0.1:5000`

## Notes

- Secrets stay in OpenVault (`keys.db`). DMS never stores API keys.
- FreeRoute `POST /v1/chat/completions` uses role-ordered fallback (primary→backup→cheap→free).
- Anthropic may be skipped by the `/v1` proxy today — record honestly.
- Parallel OmniRoute Electron bake-off is **not** shipped; use precheck latency + one FreeRoute call.
- Add keys via UI `http://127.0.0.1:3010/vault` or `POST /api/vault/ingest-env`.

## Precheck-all

- ok: `True`
- vault key records listed: **13**
- precheck result rows: **13**

| provider / label | status | latency_ms | error |
|---|---|---|---|
| ? | ok | 13.071000023046508 |  |
| ? | ok | 363.45380000420846 |  |
| ? | ok | 407.2895999997854 |  |
| ? | ok | 305.34299998544157 |  |
| ? | ok | 334.2065000033472 |  |
| ? | ok | 397.8400999912992 |  |
| ? | ok | 801.1857999954373 |  |
| ? | ok | 430.45250000432134 |  |
| ? | ok | 436.3906000216957 |  |
| ? | ok | 388.8611000147648 |  |
| ? | ok | 581.8428000202402 |  |
| ? | error |  | All connection attempts failed |
| ? | error |  | All connection attempts failed |

## FreeRoute chat probe

- prompt: `In one sentence: what is 2+2? Reply with only the number.`
- status: `200`
- latency_ms: `823.5`
- preview: `4`
- error: `None`
- note: Sequential FreeRoute fallback — not a parallel OmniRoute Electron bake-off.

## Escalation

If free tiers suck, add Claude / OpenAI / DeepSeek into OpenVault (not into DMS `.env`),
then re-run `python scripts/bakeoff_freeroute.py`. Warehouse answers still route via Cortex.
