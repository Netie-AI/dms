# OV-MINT-BEARER-01

Keywords: OV-MINT-BEARER-01, ManifestMinter, fetch_intermediate, POST /keys/intermediate, POST /keys/services, Bearer, reveal-only, DMS_OV_SERVICE_TOKEN, DMS_OV_SERVICE_TOKEN_FILE, DMS_OV_INTERMEDIATE_TTL_S, ov_service_token_missing, ov_service_token_unauthorized, ov_mint_failed, cortex-contract 1.4.0, model routing inventory, dms-362, dms-359, OV#128, OV#130

## Main idea

`ManifestMinter.fetch_intermediate` re-registered `dms` (`POST /keys/services`, reveal-only) on every fetch. OV `b4d68021` (OV#130) 401s that re-register by design, so DMS never got its key. The pack lane then said `Cortex SQL fail`. Now the normal path is one `POST /keys/intermediate` with `Authorization: Bearer <dms token>`. Register only on a first mint with no token. Any OV token failure is one named ABSTAIN. Unit tests mock OV. Not live-measured. Nothing PASS.

## OV contract read (b4d68021, read-only)

- `OpenMW/openmw/openvault/routers/keys.py` `issue_intermediate` (`POST /keys/intermediate`). Loopback only. Bearer required: no Bearer is 401. Wrong or unknown token is 403 `service is not authorised to sign`. Sealed vault is 403 `vault is sealed`.
- `register_service` (`POST /keys/services`). Peer plus `X-OpenVault-Reveal: intentional`. If the service is already registered it needs the current Bearer or `X-OpenVault-Admin`, else 401.
- `vault/http_guard.py`: the three mint routes skip the admin header. A non-`ov_` Bearer from a non-loopback peer on `/keys/intermediate` is 403 `openvault_forbidden` at the guard.

## DMS design

- service_id: OV compares it case-sensitively (`dms` != `DMS`). The only source is `OV_SERVICE_ID = "dms"` in `manifest.py`. The constructor arg and the env path are removed. A register echo that is not exactly `dms` is `ov_mint_failed` and is not persisted.
- Token: env `DMS_OV_SERVICE_TOKEN` wins. Else file `DMS_OV_SERVICE_TOKEN_FILE`. Read once in `ManifestMinter.__init__` and kept in memory. Never logged, echoed, or stamped.
- First mint runs only when env and file are both empty and a file path is set. The file is opened 0600 before the register call, so a path DMS cannot write never costs the only copy of the token. OV 401 on register means already registered: `ov_service_token_missing`, and the file stays empty.
- 401 / 403 (not sealed) on intermediate: re-read env / file once, then one more Bearer fetch. Still refused: `ov_service_token_unauthorized`. No register, no rotate.
- Key cache: until `not_after - KEY_EXPIRY_SKEW` (30s). If OV omits `not_after`, lifetime is `DMS_OV_INTERMEDIATE_TTL_S` (default 900). That value is also the `ttl_s` DMS requests.
- Client: one `httpx.Client` per minter. `Timeout(10.0, connect=3.0)`, `Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=60.0)`.
- Ask path: the pack lane (`Cortex SQL fail`) and the generative lane (`submit_failed`) re-raise `OpenVaultTokenError`. `Executor.live_ask` returns ABSTAIN `ans_ov_mint` with assumptions `[<code>, "no generative fallback", "no demo fallback"]`. That is HTTP 200, so `DMS_DEMO_FALLBACK=1` cannot replace it.
- DMS source has no `OPENVAULT_ADMIN` / `X-OpenVault-Admin`. A test scans for it.

## cortex-contract (measured, no bump here)

Six contract operations DMS calls: ask, submit, ledger append, ledger verify, tools, drillthrough. Their request/response fields are identical between DMS vendored `openapi-1.2.0.json` and Cortex 1.2.0 at `279cbd85`. At Cortex main `c7469da4` (contract 1.4.0) the only change is on `POST /v1/contract/ask`, and it is additive. Response gains `served_provider|model|local|reason` (1.3) and `memory_ids_read`, `memory_reads[]`, `reused` (1.4). Request gains optional `scored_pack_id` (1.4). `execution.py` and `testvectors` are unchanged from pin to main, so `canonical_manifest_bytes` is unchanged. DMS parses Answer into `additional_properties` and `AskResponse(extra="ignore")`, so 1.4 parses. Byte drift: DMS `openapi-1.2.0.json` sha `08efc36d` != Cortex `75ad80e6`. Cortex's file has extra unreferenced components (`AssembleIn`, `InsightsAskIn`, ...), none on the six paths. CI builds the wheel from Cortex default branch with no ref, so CI already runs 1.4.0.

## Gate

Draft PR. #362 / #359 stay OPEN. DevOps admin rotate into the secret file and the DMS pin move are after Lead Formal. Prove pin `cbf7de87` untouched. Not live. Nothing PASS.
