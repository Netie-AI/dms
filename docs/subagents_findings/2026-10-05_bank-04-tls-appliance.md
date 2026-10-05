# BANK-04

Keywords: BANK-04, TLS, Caddy, internal CA, DMS_TLS_MODE, DMS_TLS_DIR, DMS_DB_PASSWORD, compose required variable, try_files, wheelhouse, cortex-contract, dms-271
Main idea: The appliance port is one https:// site. Certificate from DMS_TLS_DIR (files) or Caddy's internal CA (sandbox default). Plain HTTP gets a 400, no data, no :80 listener. The Postgres password is `${DMS_DB_PASSWORD:?}`, never committed, and compose refuses to start without it. Not COMPLETE; no clean-Linux prove.

Found on the way: (1) `try_files` at site level ran before every `handle`, so `/health`, `/v1/*` and `/api/*` returned `/index.html` through Caddy, on the old plain `:8080` site too (run against 7a8d6c1: 200 and the SPA for all four paths). A "plain HTTP does not serve /v1/*" check that only looks for absence passes on that bug, so the smoke first asserts HTTPS `/v1/spaces` returns the API's data. (2) `apps/api/Dockerfile` installs `cortex-contract`, which is private and on no index, so the image does not build on a clean machine; it now installs from `deploy/wheelhouse/`. Publishing the wheel is the real fix and is not this ticket.

Gate: `tests/test_bank04_tls_appliance.py` (Docker-free, 8 tests, 8 red on 7a8d6c1) guards the config. `scripts/compose_tls_smoke.sh` (CI job `compose-tls-smoke`) asserts the behaviour on live containers.
