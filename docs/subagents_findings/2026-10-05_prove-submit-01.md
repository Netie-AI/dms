# PROVE-SUBMIT-01

Keywords: PROVE-SUBMIT-01, Cortex SQL fail, cq_sku_count, maybe_pack_ask, contract submit, 279cbd85, ans_curated_step, dms-359

## Main idea

Live Finance pack asks stamp `Cortex SQL fail` because `maybe_pack_ask` drops the exception from `_submit_verified_sql`. That call is bind plus `POST /v1/contract/submit` on Cortex pin `279cbd85`. The engine status and body are not in the envelope. Owner of the failed step is that Cortex pin. No DMS patch. Nothing PASS.

## Live envelope (prove IAP, curl)

`POST https://studio.netie.ai/api/v1/chat/ask`

Space `cccccccc-cccc-cccc-cccc-cccccccccccc`. Question `How many SKUs do we have in inventory?` (`cq_sku_count`).

- DMS HTTP 200. Cloudflare `cf-ray: a45e89510ea57c81-EWR`. No Cortex `run_id` on the response.
- `assumptions`: `["exact match ok", "Cortex SQL fail", "no generative fallback"]`
- `answer_id` / `audit_id`: `ans_curated_step`
- `badge` ABSTAIN, `route` abstain, `sql_used` null, `rows` empty
- Wall time about 1.3s. Not a timeout. Ledger step not reached.

Same stamp, same shape, under 1.5s:

- `What is total stock value by category?` (inventory)
- `Show warehouse capacity utilisation` (locations)

`Top 5 selling SKUs by revenue` is `exact-match miss` / `pack-metric miss` (that id is not on the allowlist). A non-pack ask returned `GEN-01: validate:explain:BinderException`, which is local EXPLAIN on the thin DMS file, not this curated submit.

Health on the same origin: `ask_mode=live`, `demo_fallback=false`, Cortex dependency `ok` with `url=http://127.0.0.1:8010/health` and `status_code=200`. Python urllib POST got Cloudflare 1010 (`cf-ray: a45e892a7ffb2672`). curl reached the DMS API. Cortex loopback is not reachable from this VM, so the inner submit status was not read off the wire.

## Call-site that writes the assumption

`packages/executor/dms_executor/demo_pack.py` `maybe_pack_ask`.

Live `_live_ask` always passes `submit` and `ledger_append`, so the `submit is None` branch is not this prove path.

The live branch is the bare `except` around `submit(hit.sql)` (the following `ok is False or output is None` check is the other writer of the same string). `submit` is `Executor._submit_verified_sql`: `bind_session` when the session is new, then `submit_sql` -> `CortexClient.submit` -> `POST /v1/contract/submit`.

`_submit_verified_sql` raises. `maybe_pack_ask` discards the exception. The envelope therefore cannot contain HTTP status, `detail.code`, `detail.message`, or `run_id`.

SQL for `cq_sku_count` (certified text, not invented here):

`SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory`

## What pin 279cbd85 does with a failed submit

`CortexOS/api/contract_routes.py` `contract_submit`:

- `submit_request` returns `QueryResult(ok=False, status=<code>, error=...)` for `ManifestError` and `PoolSaturated`.
- The route does not return that body. It raises `HTTPException` with `detail` `{code, message, run_id}` and a status from `_http_for_submit_status` (400, 403, 409, or 429).
- `SqlGateAbstain` (EXPLAIN reject inside `execute_sql`) is not a `ManifestError`. `submit_request` does not catch it. `create_app` has no exception handler, so that path is an unhandled HTTP 500. The DuckDB text stays in the engine log, not in the default 500 body.

DMS `CortexClient` uses `raise_on_unexpected_status=True`, so a non-200/422 becomes `UnexpectedStatus(status_code, content)`. `submit_sql` / `bind_session` classify that and re-raise. The curated except then drops it.

A successful pin response is HTTP 200 with `ok=true` and `output` set (`bound` includes `session_id`; sql includes `columns` and `rows`). The live abstain means that 200 did not come back.

## Owner

Cortex pin `279cbd85` submit/bind. DMS is the mapper. The URL on the health probe is the loopback contract port. The payload is the contract submit (pool, plan.kind `session_bind` then `sql`, body.sql, signed manifest). This PR does not change that call.

Not a DMS rewrite of the certified SQL. Not a lake reseed. Not a tip deploy.
