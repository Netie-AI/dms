# PROVE-SUBMIT-01

Keywords: PROVE-SUBMIT-01, vault, OpenVault mint, openvault_unauthenticated, 401, Cortex SQL fail, cq_sku_count, dms-359, dms-360, Cortex#301, OV#126

## Main idea

Lead Formal owner is vault / OpenVault mint, not Cortex pin SQL or submit. Live prove (Cortex Build) never called `POST /v1/contract/submit`. OV mint `POST :18080/keys/services` returned HTTP 401 `openvault_unauthenticated`. Owner stamp is vault (Cortex #301 HttpGuard admin clash / OV #126). The earlier DMS inference that pin `279cbd85` submit/bind failed is incorrect: the mapper dropped a non-submit path. Do not paper over with DMS SQL. #359 stays OPEN. Live still BLOCKED. Nothing PASS. No tip deploy. No lake reseed. No product code.

## Live envelope (prove IAP, curl)

`POST https://studio.netie.ai/api/v1/chat/ask`

Space `cccccccc-cccc-cccc-cccc-cccccccccccc`. Question `How many SKUs do we have in inventory?` (`cq_sku_count`).

- DMS HTTP 200. Cloudflare `cf-ray: a45e89510ea57c81-EWR`. No Cortex `run_id` on the response.
- `assumptions`: `["exact match ok", "Cortex SQL fail", "no generative fallback"]`
- The string `Cortex SQL fail` is the observed label only. It is misleading. `POST /v1/contract/submit` was never called.
- `answer_id` / `audit_id`: `ans_curated_step`
- `badge` ABSTAIN, `route` abstain, `sql_used` null, `rows` empty
- Wall time about 1.3s. Not a timeout. Ledger step not reached.

Same observed label, same shape, under 1.5s:

- `What is total stock value by category?` (inventory)
- `Show warehouse capacity utilisation` (locations)

`Top 5 selling SKUs by revenue` is `exact-match miss` / `pack-metric miss` (that id is not on the allowlist). A non-pack ask returned `GEN-01: validate:explain:BinderException`, which is local EXPLAIN on the thin DMS file, not this path.

Health on the same origin: `ask_mode=live`, `demo_fallback=false`, Cortex dependency `ok` with `url=http://127.0.0.1:8010/health` and `status_code=200`. Python urllib POST got Cloudflare 1010 (`cf-ray: a45e892a7ffb2672`). curl reached the DMS API.

## Owner (Lead Formal)

Vault / OpenVault mint.

- Live prove on Cortex Build: `POST /v1/contract/submit` was never called.
- OV mint `POST :18080/keys/services` returned HTTP 401 `openvault_unauthenticated`.
- Owner stamp = vault. Cortex #301 HttpGuard admin clash. OV #126.
- After OV auth lands, re-capture on Cortex #301. Keep abstain honesty.

## Prior claim (incorrect)

Merged #360 recorded the owner as Cortex pin `279cbd85` submit/bind. That reading inferred `maybe_pack_ask` dropped an exception from bind plus `POST /v1/contract/submit`. The inference is incorrect. The mapper dropped a non-submit path. Do not paper over with DMS SQL. This amend does not change product code.

## Gate

#359 stays OPEN. Live still BLOCKED. Nothing PASS. No tip-deploy claim. No lake reseed. No product code. This note records the Lead Formal. It does not issue a Formal stamp.
