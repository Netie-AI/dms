# DEPLOY

## Day-1: local appliance (Compose)

```powershell
cd D:\DMS
.\scripts\bootstrap.ps1
# API :8090  Web :3000  (SQLite control plane if Docker/Postgres unavailable)
.\scripts\verify.ps1
```

Does **not** require Kubernetes or Slurm. Port **8090** is used for the API on Windows (8080 is often reserved).

## Appliance (Compose): TLS only, install-time secrets

`deploy/compose/docker-compose.yml` publishes exactly one port, Caddy's **`:8080`**, and Caddy serves **TLS only** on it (BANK-04, [#271](https://github.com/Netie-AI/dms/issues/271)). Studio, `/health`, `/v1/*` and `/api/*` are all behind that one `https://` site. Postgres and the API are internal (`expose` only).

Plain HTTP to `:8080` is **refused**: the TLS listener answers `400 Client sent an HTTP request to an HTTPS server.` and serves nothing. It is not redirected, because a redirect needs a second published port. Port 80 is neither published nor opened inside the container.

Install-time configuration, in `deploy/compose/.env` (gitignored; template `deploy/compose/.env.example`) or the shell:

| Variable | Meaning |
|----------|---------|
| `DMS_DB_PASSWORD` | **Required, no default.** `docker compose up` exits before creating anything without it (unset or empty). Letters, digits and `. _ ~ -` only (it is spliced into `DATABASE_URL`); `openssl rand -hex 24` works. Compose's `:?` rejects unset and empty but **accepts a one-space value**, so do not use one: `Start-DMSStack.ps1` treats empty and whitespace-only as absent, and compose itself is unchanged. Applied when the `dms_pg` volume is first created: changing it later does **not** change the password inside an existing volume (`ALTER USER`, or recreate the volume). A wrong password does not stop the API: by `dms_api/app.py` it falls back to in-memory Spaces, visible as `"backend":"memory"` on `/health`. |
| `DMS_TLS_MODE` | Exactly `internal` (default) or `files`. The ui service's entry command refuses every other value, a glob such as `*` included, and exits before Caddy runs (the value is spliced into the Caddyfile's `import tls_<mode>`, and Caddy alone only warns about a glob that matches nothing). The Caddyfile also sets `local_certs`, so a site can never fall back to a public ACME CA. |
| `DMS_TLS_DIR` | `files` mode: host directory holding `tls.crt` (leaf plus chain, PEM) and `tls.key` (PEM). Mounted read-only at `/etc/caddy/tls`. A missing or unreadable pair makes Caddy refuse to load. Keep it outside the repository; `.gitignore` also ignores `.p12 .pfx .jks .cer .cert` under `deploy/`, and everything in `deploy/compose/tls/`. |
| `DMS_TLS_HOST` | The DNS name or IP clients use (default `localhost`). The certificate must carry it. |
| `DMS_BIND_ADDR` | Host address the one published port binds to. Default `0.0.0.0` (every interface, as before). Set `127.0.0.1` when a load balancer or IAP on the same host fronts the appliance, or one NIC's address to keep it off the others. |

Why each key exists (hard rule 6: a new key needs a stated swap scenario). None is one of the five ports.

| Key | What it swaps |
|-----|---------------|
| `DMS_DB_PASSWORD` | The database credential: one per install, chosen by the installer, replacing a value that was committed to the repository. |
| `DMS_TLS_MODE`, `DMS_TLS_DIR` | The certificate source: Caddy's internal CA for a sandbox pilot, a bank or internal CA certificate for a bank install. Same appliance, one install-time choice. |
| `DMS_TLS_HOST` | The name the certificate is issued for or must match: `localhost` in a sandbox, the bank's DNS name in production. |
| `DMS_BIND_ADDR` | The interface the appliance listens on: every interface by default, loopback behind a same-host load balancer or IAP (the prove-host pattern in `docs/DEMO_RUNBOOK.md`), or one NIC. The compose smoke uses it to stay on `127.0.0.1` while verifying the shipped file. |

The `api` image installs `cortex-contract`, which is private and on no package index, so **first** put exactly one 1.2.x wheel in `deploy/wheelhouse/` (CI builds it into place; a clean-server install has to be handed it until it is published). The image build installs that wheel first, by exact path with no index, and fails if the directory holds zero or more than one; the later install is then constrained to that version, so a `cortex-contract` published to a public index cannot replace it. Then:

```bash
cd deploy/compose
cp .env.example .env            # set DMS_DB_PASSWORD; for a bank certificate set DMS_TLS_MODE=files and DMS_TLS_DIR
(cd ../../apps/ui && npm ci && npm run build)    # Caddy serves apps/ui/dist
docker compose up -d --build
```

- **Bank CA or internal CA:** `DMS_TLS_MODE=files`, `DMS_TLS_DIR` pointing at the directory, `DMS_TLS_HOST` set to the name in the certificate's SAN. Rotation is replacing the two files and `docker compose restart ui`.
- **Sandbox pilot:** `DMS_TLS_MODE=internal` (the default). Caddy's own CA signs the certificate. Clients get a trust warning until they trust Caddy's root: `docker compose cp ui:/data/caddy/pki/authorities/local/root.crt ./dms-root.crt`. The root lives in the `caddy_data` volume and survives container re-creates; `down -v` deletes it. Not for a bank install.
- HTTP/3 is off (`servers { protocols h1 h2 }`): the appliance opens no UDP port and sends no `Alt-Svc`.
- Developer overlays: `docker-compose.hostdb.yml` (Postgres on `127.0.0.1:5432`) and the `dev` profile's `api_dev` (`127.0.0.1:8090`) are loopback-only and need `DMS_DB_PASSWORD` too. `Start-DMSStack.ps1` reads it the way compose reads `.env` (last duplicate wins, inline ` # comment` stripped, quotes honoured) from the shell or `deploy\compose\.env`, and never supplies one; `scripts/windows/Test-GetDmsDbPassword.ps1` pins that.

Checked by `scripts/compose_tls_smoke.sh` (CI job `compose-tls-smoke`; needs Docker): compose refuses to start without the password; HTTPS answers `/health`, Studio and `/v1/*`; plain HTTP serves none of them; the only published mapping is one TCP port, `8080`; the container has no `:80`, `:443` or UDP listener and sends no `Alt-Svc`; `files` mode serves the supplied certificate; an empty certificate directory, an unknown mode, and the glob `*` all fail closed. The smoke is hermetic: inert `CORTEX_URL`/`OPENVAULT_URL`, `-f docker-compose.yml` explicit, the port bound to `127.0.0.1`, and `down -v --rmi local` afterwards. `tests/test_bank04_tls_appliance.py` guards the same properties from the config files where Docker is unavailable. CI is the build proof for `apps/api/Dockerfile`.

Not done here: HSTS, client certificates, rate limiting, per-user authentication (DR-0004: the deployment is the actor), and a live prove on a clean Linux install (bank bar item 5).


## Presets

| File | Use |
|------|-----|
| `deploy/presets/local-appliance.yaml` | Single node / SME laptop |
| `deploy/presets/company-server.yaml` | Shared server, replicas |
| `deploy/presets/airgap.yaml` | No external LLM |
| `deploy/presets/hpc-slurm.yaml` | Batch jobs via Slurm worker |

Env companions: `*.env` next to presets where needed.

## Company server (Helm)

```bash
helm upgrade --install dms deploy/helm/dms \
  -f deploy/presets/company-server.yaml \
  -n dms --create-namespace
```

Ingress terminates TLS; API/web Deployments; Postgres (or external DSN); lake PVC or MinIO.

## Load balancer

- Compose: **Caddy** on `:8080`, TLS only → Studio from `apps/ui/dist`; `/health`, `/v1/*` and `/api/*` to api `:8080` (see "Appliance (Compose)" above)  
- K8s: Ingress controller (+ cloud LB / MetalLB)

## Slurm

Only with `hpc-slurm` preset. Worker submits ingest/pipeline batch jobs. Web/API stay on K8s or Compose — Slurm is **not** the HTTP control plane.

## External deps

Set `CORTEX_URL` and `OPENVAULT_URL` to reachable siblings. Charts do not embed Cortex/OpenVault images by default.
