"""SCALE-FREE-AI-01 — FreeRoute providers via OpenVault API only.

Prove/ask generate still uses ``model_preference=free+normal`` (Cortex/OV hop).
DMS resolves which free providers that preference would attempt vs skip from
OpenVault HTTP catalog endpoints -- not from chat completions, not from
``D:\\OpenVault`` files, not from a second vault.

LIVE_KEY_ID stays Platform SoT (freeze). This ticket does not mint or rotate keys.

## Resolve

GET only, using ``OPENVAULT_URL`` (Platform BASE):

- ``/api/freeroute/status`` (hops + spendable)
- ``/api/freeroute/onboard`` (Groq-first checklist)
- ``/api/tool/register`` (catalog, no secrets)
- ``/api/keys`` (vault metadata; no ``X-OpenVault-Reveal``)

Never ``POST /v1/chat/completions`` for discovery. Never Path-scrape a vault root.

## Attempt vs skip (free+normal)

- Attempt unique labels, Groq first.
- Skip duplicate labels (``dup_label``) so a second key with the same name is
  not burned as a second provider.
- Skip paid primary hops (``paid_not_free_normal``).
- Skip retired GitHub Models (``retired``).
- Skip non-spendable / non-OpenAI-compat rows (``not_spendable``).

WRONG=0 is unchanged: DMS does not add extra generate retries or paid keys to
raise coverage. Abstain stays the honest miss.

## Surfaces

- Plan: ``packages/core/dms_core/freeroute.py``
- HTTP: ``apps/api/dms_api/freeroute_client.py``
- GET ``/v1/freeroute/providers`` (labels/reasons only)
- Harness: ``python scripts/bakeoff_freeroute.py --self-check``
- Live leftover Platform: ``python scripts/bakeoff_freeroute.py`` with host
  ``OPENVAULT_URL`` already on the unit. Not CI. Not #178 COMPLETE.

Regression: ``tests/test_scale_free_ai.py``.
