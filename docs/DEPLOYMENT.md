# Deploying DMS, Cortex and OpenVault

Audience: a customer operator or a Netie forward-deployed engineer (FDE) standing up the three services on a customer's infrastructure.
Based on: `Netie-AI/dms` main `f43a86d5`, `Netie-AI/Cortex` main `7d6945b4`, `Netie-AI/OpenVault` main `ceab7c77` (read-only clones), and the working single-VM stack Netie runs today (`bootstrap.sh`, `keepalive.sh`).
Written 2026-11-29.

## Fill these in from your private ops notes

These placeholders are not values. Copy them from your private ops notes. Do not commit a filled-in copy.

| Placeholder | Fill with |
|-------------|-----------|
| `<STUDIO_HOST>` | DNS name clients use for Studio |
| `<DEPLOY_ROOT>` | Deployment tree on the host (gateway site and launchers) |
| `<OPS_ROOT>` | Private ops scripts, not in the three git repos |
| `<STUDIO_WEB_ROOT>` | Directory the web server serves as the Studio static files |
| `<HTPASSWD_PATH>` | Basic-auth password file, mode 600 |
| `<LOOPBACK>` | This host's loopback address only |

## 0. How to read the verification marks

| Mark | Meaning |
|------|---------|
| **[V-live]** | The exact commands run today on Netie's own Linux stack, so they have been executed (Postgres 17, nginx, three Python services, loopback binds). |
| **[V-repo]** | Taken from files in the repos (compose file, Dockerfile, CI job, docs) and cross-read against the code. Not executed by the author of this guide. |
| **[NOT VERIFIED]** | Plausible and consistent with the repos, but nobody has run it. Treat as a starting point and test on a scratch host first. |

A list of everything unverified is in section 14. Operator scripts (`bootstrap.sh`, `keepalive.sh`, `offbox-backup.sh`, `ovctl.py`, `RESTORE.md`) live under `<OPS_ROOT>` on the operations host. The gateway and its launchers live under `<DEPLOY_ROOT>`. Neither tree is in the three repos.

## 1. What you are deploying

Three services, one rule: **model routing and provider keys live only in OpenVault.** DMS and Cortex never hold a provider key and never name a model.

| Service | Role | Default port | Health | Start command (Linux) |
|---------|------|--------------|--------|----------------------|
| **DMS** (`dms`) | Studio SPA plus the API: connect, ontology, ask, exports, Create. Owns the Postgres control plane and a DuckDB copy of each customer's data. | API `8090` (box) or `8080` (compose, behind Caddy `8080` TLS) | `GET /health` | `python -m uvicorn dms_api.app:app --host <LOOPBACK> --port 8090` |
| **Cortex** | The answer engine. DMS calls it over the pinned `cortex-contract` (1.2.x). | `8010` | `GET /health` | `python -m uvicorn CortexOS.api.main:app --host <LOOPBACK> --port 8010` |
| **OpenVault** | Key custody, FreeRoute (role routing, hard spend caps), service tokens. | API `5000`, console `3010` | `GET /api/healthz` | `openmw console --host <LOOPBACK> --port 5000 --no-open-browser` (from `OpenMW/`) |
| **Postgres** | DMS control plane (`dms` schema, row level security forced). | `5432` | `pg_isready` | system service |

Request flow: browser -> TLS reverse proxy (nginx or Caddy) -> DMS `/api/*` and `/v1/*` -> Cortex `:8010` -> OpenVault `:5000` -> model provider. DMS also talks to OpenVault directly (service token) for ontology and Create calls.

Non-negotiables for every topology:

- **Never expose OpenVault `:5000` or its console `:3010` to a network.** Custody routes trust loopback (plus an explicit `OPENVAULT_SERVICES_ALLOW` list for service minting). Bind them to `<LOOPBACK>` only. **[V-repo]** `OPENVAULT_ALLOW_PUBLIC_BIND` exists as an override; do not set it.
- **DMS has no per-user login unless you turn on SSO** (`DMS_SSO=1`). The deployment is the actor (DR-0004, `docs/decisions/0004-the-authentication-trust-boundary.md`). So the reverse proxy must authenticate people (basic auth, SSO proxy, VPN or IAP) before anything reaches DMS. **[V-repo]**
- DMS is single-tenant per install for a first sale. One tenant per company, one Space per company (`docs/fde/RUNBOOK.md`).
- Extract-only: DMS reads `SELECT * FROM <granted table>` from the customer's database into DuckDB; model-written SQL never runs on the source (`docs/fde/RUNBOOK.md`, Security notes).
- No secret goes in a repo, a ticket, a log, a chat, a shell history or a compose file in git.

## 2. Prerequisites (all topologies)

| Item | Version / note |
|------|----------------|
| OS | Linux x86_64. Ubuntu 22.04/24.04 is what Netie runs. **[V-live]** |
| Python | 3.11 or newer (`requires-python >=3.11`). `uv` is used for venvs. **[V-live]** |
| Postgres | 17 on the VM path **[V-live]**; the compose file pins `postgres:16-alpine` **[V-repo]**. |
| Node | Needed to build Studio (`apps/ui`, Vite, React 19) and the OpenVault console (`apps/web`, Next). Version: see each `package.json`; Node 20+ is a safe assumption **[NOT VERIFIED]**. |
| Docker + Compose v2 | Only for the compose path. |
| `cortex-contract` wheel | **Private, on no index.** DMS pins `cortex-contract>=1.2.0,<2`; the compose Dockerfile wants exactly one `cortex_contract-1.2.x` wheel (the live Cortex on Netie's stack reports contract `1.9.0` on DMS `/health`, so confirm the version pair before a customer install). On a VM it is installed from the Cortex checkout (`cortex/packages/cortex_contract`). In compose it must sit in `deploy/wheelhouse/` (exactly one). CI builds it with `python -m build --wheel --outdir deploy/wheelhouse ./_cortex/packages/cortex_contract`. **[V-repo]** |
| DNS name + certificate | For TLS. A private CA certificate is fine. |
| Outbound HTTPS | From OpenVault only, to the model providers you choose. DMS and Cortex need no direct internet. |
| A model provider key | Registered into OpenVault (section 7). Never into DMS or Cortex config. |

Repos, read-only for the installer:

```bash
git clone https://github.com/Netie-AI/dms.git
git clone https://github.com/Netie-AI/Cortex.git
git clone https://github.com/Netie-AI/OpenVault.git
```

DMS' editable install expects Cortex as a **sibling directory named `cortex`** (the bootstrap script uses `../cortex/packages/cortex_contract`). Lay the checkouts out as `<root>/dms`, `<root>/cortex`, `<root>/openvault`.

## 3. Topology 1: single Linux VM (systemd, nginx, TLS, basic auth or SSO)

This is the topology Netie runs today (minus systemd, see the note). **[V-live]** unless marked.

### 3.1 Layout and users

```text
/opt/netie/dms          DMS checkout (+ .venv)
/opt/netie/cortex       Cortex checkout (+ .venv)
/opt/netie/openvault    OpenVault checkout (OpenMW/.venv, apps/web/.next)
/var/lib/netie/openvault-home   OPENVAULT_HOME (vault db, master key wrap, admin token)  mode 700
/etc/netie/             env files and secret files, mode 600/700, owner netie
/var/log/netie/         service logs
```

Create a service user: `sudo useradd --system --create-home --shell /usr/sbin/nologin netie`. Netie's own box runs everything as one user with a keepalive loop; a dedicated user is the recommended hardening. **[NOT VERIFIED]**

### 3.2 Packages and Postgres

```bash
sudo apt-get update
sudo apt-get install -y postgresql-17 postgresql-common nginx age sqlite3 curl git
# uv (Python env manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo pg_ctlcluster 17 main start      # if the cluster is not already running
```

(`postgresql-17` comes from the PGDG apt repo on Ubuntu releases that do not ship 17; `bootstrap.sh` simply installs the three package names above. **[V-live]**)

Create the roles and database. The password is generated, put in a 600 file, and never printed:

```bash
sudo install -d -m 700 -o netie /etc/netie
umask 077
printf 'DMS_DB_PASSWORD=%s\n' "$(openssl rand -hex 24)" | sudo tee /etc/netie/db.env >/dev/null
sudo chown netie /etc/netie/db.env
set -a; . /etc/netie/db.env; set +a
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dms') THEN CREATE ROLE dms LOGIN; END IF; END \$\$;
ALTER ROLE dms PASSWORD '${DMS_DB_PASSWORD}';
DO \$\$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dms_viewer')  THEN CREATE ROLE dms_viewer  NOLOGIN; END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dms_steward') THEN CREATE ROLE dms_steward NOLOGIN; END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dms_admin')   THEN CREATE ROLE dms_admin   NOLOGIN; END IF;
END \$\$;
GRANT dms_viewer, dms_steward, dms_admin TO dms;
CREATE DATABASE dms OWNER dms;
SQL
unset DMS_DB_PASSWORD
```

This is the SQL in `bootstrap.sh`. The first Alembic migration (`0001_dms_control_plane.py`) creates the `dms` schema, `pgcrypto`, and the three NOLOGIN roles if missing, with row level security forced on tenant tables. Pre-creating the roles as above avoids needing `CREATEROLE` for `dms`. **[V-live]**

### 3.3 Python environments

```bash
sudo -u netie bash -lc '
  export PATH=$HOME/.local/bin:$PATH
  git clone https://github.com/Netie-AI/Cortex.git /opt/netie/cortex
  git clone https://github.com/Netie-AI/dms.git    /opt/netie/dms
  git clone https://github.com/Netie-AI/OpenVault.git /opt/netie/openvault

  # Cortex
  cd /opt/netie/cortex && uv sync --frozen

  # DMS (editable packages + the private cortex-contract from the Cortex tree)
  cd /opt/netie/dms && uv venv .venv
  uv pip install --python .venv/bin/python ../cortex/packages/cortex_contract \
    -e packages/core -e packages/cortex_client -e packages/executor \
    -e packages/ledger -e packages/ao_vendor -e .

  # OpenVault custody API
  cd /opt/netie/openvault/OpenMW && uv sync --frozen
'
```

The DMS and Cortex lines are exactly what `bootstrap.sh` runs. **[V-live]** The OpenVault `uv sync` in `OpenMW/` matches how the box launches it (`uv run --no-sync openmw console ...`); `--frozen` is an assumption **[NOT VERIFIED]**. Optional DMS extras for source databases: `uv pip install --python .venv/bin/python -e '.[sources]'` (SQL Server, MySQL), `'.[oracle]'`, `'.[gauss]'` (section 8). **[V-repo]**

### 3.4 Build Studio and the OpenVault console

```bash
# Studio SPA, served as static files by nginx
cd /opt/netie/dms/apps/ui && npm ci && npm run build        # output: apps/ui/dist
sudo mkdir -p <STUDIO_WEB_ROOT> && sudo rsync -a --delete dist/ <STUDIO_WEB_ROOT>/

# OpenVault console (loopback only, optional but useful for key admin)
cd /opt/netie/openvault/apps/web && npm ci && npm run build
```

`npm run build` = `tsc --noEmit && vite build` for Studio. **[V-repo]** The operations host serves a pre-built bundle from `<STUDIO_WEB_ROOT>`; building from source on a customer host is **[NOT VERIFIED]** end to end.

### 3.5 Environment files

`/etc/netie/dms.env` (no secrets inline; secrets are files):

```bash
DATABASE_URL=postgresql://dms:<from /etc/netie/db.env>@<LOOPBACK>:5432/dms   # assemble in the launcher, see below
CORTEX_URL=http://<LOOPBACK>:8010
OPENVAULT_URL=http://<LOOPBACK>:5000
DMS_ASK_MODE=live
DMS_DEMO_FALLBACK=0
DMS_INSIGHTS_TIMEOUT_S=60
CORS_ORIGINS=https://<STUDIO_HOST>
DMS_TENANT_ID=<uuid of this customer's tenant row, section 8.4>
DMS_OV_SERVICE_TOKEN_FILE=/etc/netie/dms_ov_service_token
# CORTEX_API_KEY is exported by the launcher from /etc/netie/dms_cortex_api_key
```

The box launcher (`start-dms.sh`) builds `DATABASE_URL` from `db.env`, runs the migrations, then starts uvicorn. Use the same shape:

```bash
#!/bin/bash
# /opt/netie/bin/start-dms.sh
set -euo pipefail
cd /opt/netie/dms && . .venv/bin/activate
set -a; . /etc/netie/db.env; . /etc/netie/dms.env; set +a
export DATABASE_URL="postgresql://dms:${DMS_DB_PASSWORD}@<LOOPBACK>:5432/dms"
[ -s /etc/netie/dms_cortex_api_key ] && export CORTEX_API_KEY="$(cat /etc/netie/dms_cortex_api_key)"
export PYTHONPATH=$PWD/apps/api:$PWD/packages/core:$PWD/packages/cortex_client:$PWD/packages/executor:$PWD/packages/ledger
python -c "from dms_api.migrate import run_migrations; import os; run_migrations(os.environ['DATABASE_URL'])"
exec python -m uvicorn dms_api.app:app --host <LOOPBACK> --port 8090
```

**[V-live]** (derived from `<DEPLOY_ROOT>/start-dms.sh`). `run_migrations` is `alembic upgrade head` and is idempotent.

Cortex launcher (from `start-cortex.sh`) **[V-live]**:

```bash
#!/bin/bash
# /opt/netie/bin/start-cortex.sh
set -euo pipefail
cd /opt/netie/cortex && . .venv/bin/activate
export CORTEX_PROFILE=core PACK=dms DMS_REFUSE_DEMO_KEYS=1
export OPENVAULT_BASE_URL=http://<LOOPBACK>:5000
# the box also sets CORTEX_CALL_DEADLINE=1 CORTEX_FREEROUTE_ROLE=1 and points
# CORTEX_FREEROUTE_TOKEN_FILE / DMS_OV_SERVICE_TOKEN_FILE at 600 files
exec python -m uvicorn CortexOS.api.main:app --host <LOOPBACK> --port 8010
```

Never set `DMS_AUTH_DISABLED` or `CORTEX_DEV_MODE` on a real host. Cortex refuses `DMS_AUTH_DISABLED` unless `CORTEX_DEV_MODE` is also set (`AUTH_DISABLED_WITHOUT_DEV_MODE`), and the images deliberately set neither. **[V-repo]** `DMS_REFUSE_DEMO_KEYS=1` rejects the built-in demo keys.

OpenVault launcher (from `ov-api.sh`) **[V-live]**:

```bash
#!/bin/bash
# /opt/netie/bin/start-openvault.sh
set -euo pipefail
export OPENVAULT_HOME=/var/lib/netie/openvault-home
export OPENVAULT_ROUTE_POLICY=/etc/netie/route_policy.json     # copy of OpenVault/config/route_policy.claude.json, edited per customer
cd /opt/netie/openvault/OpenMW
exec uv run --no-sync openmw console --host <LOOPBACK> --port 5000 \
     --cortex-url http://<LOOPBACK>:8010 --no-open-browser
```

The console (`apps/web`, `PORT=3010 npm run start`) is optional and must stay on loopback.

### 3.6 systemd units **[NOT VERIFIED]**

Netie's own box has no systemd supervision: `keepalive.sh --daemon` restarts whatever is down every 30 seconds. On a normal VM use systemd. These units are written to match the launchers above and have not been run:

```ini
# /etc/systemd/system/netie-openvault.service
[Unit]
Description=Netie OpenVault custody API
After=network.target
[Service]
User=netie
ExecStart=/opt/netie/bin/start-openvault.sh
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/netie-cortex.service
[Unit]
Description=Netie Cortex engine
After=network.target netie-openvault.service
Wants=netie-openvault.service
[Service]
User=netie
ExecStart=/opt/netie/bin/start-cortex.sh
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/netie-dms.service
[Unit]
Description=Netie DMS API
After=network.target postgresql.service netie-cortex.service
Wants=postgresql.service netie-cortex.service
[Service]
User=netie
ExecStart=/opt/netie/bin/start-dms.sh
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now netie-openvault netie-cortex netie-dms
```

**Unseal step.** OpenVault starts sealed when a passphrase is configured (sections 1b of `docs/SECRETS_CUSTODY.md`). Unseal is a loopback `POST /api/vault/unseal`. Netie's `keepalive.sh` does this from a 600 passphrase file every time OpenVault restarts (`ovctl.py unseal`). On a systemd host add an `ExecStartPost` that calls your own unseal helper, or unseal by hand after a reboot. While sealed, reveal and mutate return 403 and routing fails closed. **[V-repo]** for the API; the helper is **[NOT VERIFIED]** outside Netie's box.

### 3.7 nginx with TLS and basic auth

A loopback gateway **[V-live]** (nginx site under `<DEPLOY_ROOT>`) listens only on `<LOOPBACK>`. TLS is terminated in front of it by a private front door. Record that front door in private ops notes, not in this repo:

```nginx
server {
    listen <LOOPBACK>:3080;
    server_name <STUDIO_HOST>;
    root <STUDIO_WEB_ROOT>;
    client_max_body_size 200m;
    auth_basic "Netie Studio";
    auth_basic_user_file <HTPASSWD_PATH>;
    location /api/ {
        proxy_pass http://<LOOPBACK>:8090/;      # trailing slash strips /api
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;                      # SSE streaming for ask progress
        proxy_read_timeout 600s;
    }
    location / { try_files $uri /index.html; }
}
```

For a customer VM with its own certificate **[NOT VERIFIED]** (same locations, TLS added):

```nginx
server {
    listen 443 ssl http2;
    server_name <STUDIO_HOST>;
    ssl_certificate     /etc/ssl/netie/tls.crt;     # leaf + chain, PEM
    ssl_certificate_key /etc/ssl/netie/tls.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    root <STUDIO_WEB_ROOT>;
    client_max_body_size 200m;

    auth_basic "Netie Studio";
    auth_basic_user_file <HTPASSWD_PATH>;

    location /api/ {
        proxy_pass http://<LOOPBACK>:8090/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
    location /health { proxy_pass http://<LOOPBACK>:8090/health; }
    location / { try_files $uri /index.html; }
}
server { listen 80; server_name <STUDIO_HOST>; return 301 https://$host$request_uri; }
```

Create the basic-auth password file at `<HTPASSWD_PATH>` without echoing a password (the web server password-file tool prompts; do not put the password on the command line), then `sudo nginx -t && sudo systemctl reload nginx`. Public CA certificates: `certbot --nginx -d <STUDIO_HOST>` **[NOT VERIFIED]**. Do **not** proxy anything to `:5000`, `:3010` or `:8010`.

### 3.8 Basic auth, or SSO

- **Basic auth at nginx** is what Netie runs today. It gives one shared login. Simple and adequate for a pilot behind a VPN.
- **Per-user SSO inside DMS**: set `DMS_SSO=1` (exact digit). Login, roles (`admin`, `steward`, `analyst`, `viewer`), and OIDC for Google, Microsoft Entra and a generic Okta issuer. Off, the API is unchanged. On, every GET except `/health` and the OIDC start/callback paths needs a session or the OpenVault service bearer. **[V-repo]** (`apps/api/dms_api/auth_sso.py`).
  - OIDC per provider: `DMS_OIDC_<GOOGLE|ENTRA|OKTA>_CLIENT_ID` and `DMS_OIDC_<...>_CRED_REF` (an OpenVault credential reference to the client secret, never the secret itself); Entra also `DMS_OIDC_ENTRA_TENANT`; Okta `DMS_OIDC_OKTA_ISSUER`; optional `DMS_OIDC_TENANT_ID`. `DMS_SSO_BOOTSTRAP_FILE` names a file for the first admin. **[V-repo]**
  - SSO login calls the compliance gate and **fails closed when Cortex is down**. **[V-repo]** (STATUS.md)
  - The redirect/callback URL you register at the identity provider and a live round trip are **[NOT VERIFIED]**.
  - Turning SSO on is an auth change. In Netie's own operation it goes through the founder 1-hour objection window.
- Do not combine nginx basic auth and DMS SSO on the same path without testing: the browser sends one `Authorization` header and DMS reads it as a bearer **[NOT VERIFIED]**. Use SSO behind a VPN or an SSO-aware proxy instead of basic auth.

### 3.9 First start and smoke

```bash
sudo systemctl start netie-openvault && sleep 10
# unseal (section 7.2), then:
sudo systemctl start netie-cortex netie-dms
curl -fsS http://<LOOPBACK>:5000/api/healthz
curl -fsS http://<LOOPBACK>:8010/health
curl -fsS http://<LOOPBACK>:8090/health      # expect "backend":"postgres", NOT "memory" (checked on Netie's stack)
curl -fsS -u '<user>' https://<STUDIO_HOST>/api/health
```

If `/health` shows `"backend":"memory"`, DMS could not use Postgres (wrong `DATABASE_URL` or password) and fell back to in-memory Spaces. Fix before going further. **[V-repo]** (`docs/DEPLOY.md`)

## 4. Topology 2: Docker Compose (the shipped appliance)

What exists on main **[V-repo]**: `deploy/compose/docker-compose.yml`, `docker-compose.hostdb.yml`, `Caddyfile`, `.env.example`, and the CI job **`compose-tls-smoke`** (`scripts/compose_tls_smoke.sh`). The compose file runs **three containers**: `postgres` (16-alpine), `api` (built from `apps/api/Dockerfile`), and `ui` (Caddy 2, TLS only). It does **not** include Cortex or OpenVault. Set `CORTEX_URL` and `OPENVAULT_URL` to reachable siblings (default `http://host.docker.internal:8010` and `:5000`). Cortex has its own Dockerfiles and compose; OpenVault has no production image today (its `Dockerfile` and `docker-compose.yml` are a test harness for the NVMe library, not the custody service).

### 4.1 Steps

```bash
git clone https://github.com/Netie-AI/dms.git && cd dms

# 1. cortex-contract wheel: exactly one cortex_contract-1.2.x*.whl in deploy/wheelhouse/
python -m pip install --upgrade pip wheel build
python -m build --wheel --outdir deploy/wheelhouse /path/to/Cortex/packages/cortex_contract

# 2. Studio bundle (Caddy serves apps/ui/dist)
(cd apps/ui && npm ci && npm run build)

# 3. configuration
cd deploy/compose
cp .env.example .env
#   set DMS_DB_PASSWORD  (openssl rand -hex 24; letters/digits/. _ ~ - only)
#   set DMS_TLS_MODE, DMS_TLS_DIR, DMS_TLS_HOST, DMS_BIND_ADDR as below
#   set CORTEX_URL / OPENVAULT_URL / CORTEX_API_KEY if not the defaults

# 4. up
docker compose up -d --build
```

### 4.2 Compose variables **[V-repo]** (`docs/DEPLOY.md` has the long form)

| Variable | Default | Meaning |
|----------|---------|---------|
| `DMS_DB_PASSWORD` | **none, required** | `docker compose up` refuses to start without it. Applied only when the `dms_pg` volume is first created; changing it later does not change the password inside an existing volume (`ALTER USER`, or recreate the volume). |
| `DMS_TLS_MODE` | `internal` | `internal` (Caddy's own CA, pilot only) or `files` (your CA certificate). Any other value stops the `ui` service before Caddy starts. |
| `DMS_TLS_DIR` | `./tls` | `files` mode: host directory holding `tls.crt` (leaf plus chain) and `tls.key`. Mounted read-only. Keep it outside git. |
| `DMS_TLS_HOST` | `localhost` | DNS name or IPv4 clients use; must be in the certificate SAN. |
| `DMS_BIND_ADDR` | all interfaces | Host address the one published port binds to. Set `<LOOPBACK>` behind a same-host load balancer. |
| `CORTEX_URL` | `http://host.docker.internal:8010` | Where the API reaches Cortex. |
| `OPENVAULT_URL` | `http://host.docker.internal:5000` | Where the API reaches OpenVault. |
| `CORTEX_API_KEY` | empty | DMS to Cortex key (an OpenVault-issued key). Empty means keyed Cortex calls refuse by name. |
| `DMS_INSIGHTS_TIMEOUT_S` | `60` | Insights timeout (code default 8, ceiling 60). |
| `DMS_ASK_MODE` | `live` | Compose default. |
| `DMS_DEMO_FALLBACK` | `0` | Must stay `0` on a real install. |

The only published port is Caddy's **`:8080` over TLS**. Plain HTTP to it is refused (400), not redirected. Postgres and the API are internal (`expose` only). HTTP/3 is off. Studio, `/health`, `/v1/*` and `/api/*` are all on that one `https://` site. **No HSTS, client certificates, rate limiting or per-user login in the appliance** (use `DMS_SSO=1` or a fronting proxy).

Corporate or bank CA: `DMS_TLS_MODE=files`, `DMS_TLS_DIR=/etc/netie/tls`, `DMS_TLS_HOST=<STUDIO_HOST>`. Rotation = replace the two files and `docker compose restart ui`. Pilot: `DMS_TLS_MODE=internal`, then trust Caddy's root: `docker compose cp ui:/data/caddy/pki/authorities/local/root.crt ./dms-root.crt` (the root lives in the `caddy_data` volume; `down -v` deletes it).

### 4.3 What CI proves (`compose-tls-smoke`)

`scripts/compose_tls_smoke.sh`, run on `ubuntu-latest` with a generated throwaway password and certificate. It checks: compose refuses to start without the password; HTTPS answers `/health`, Studio and `/v1/*`; plain HTTP serves none of them; exactly one published TCP port (`8080`) and no UDP; `files` mode serves the supplied certificate; an empty certificate directory, an unknown mode and the glob `*` all fail closed. It uses inert `CORTEX_URL`/`OPENVAULT_URL`, so **it does not prove a real ask works through the appliance**, and it needs the wheel in `deploy/wheelhouse/`. `apps/api/Dockerfile` is built only in CI; nobody at Netie has run the compose stack on a customer machine ("a live prove on a clean Linux install" is listed as not done in `docs/DEPLOY.md`). **[V-repo]**; the whole path is **[NOT VERIFIED]** by hand here (the authoring box has no Docker).

### 4.4 Adding Cortex and OpenVault beside it

- **Cortex**: `compose.constructor.yml` (Dockerfile.constructor, port `8010:8080`, `OPENVAULT_BASE_URL=http://host.docker.internal:5000`, `DMS_REFUSE_DEMO_KEYS=1`, auth stays on) or `Dockerfile.core` (profile `core`, port 8010, healthcheck on `/health`). `docker-compose.yml` in the Cortex repo is a Qdrant service only. **[V-repo]**, build not run here.
- **OpenVault**: run it on the host (section 3.5 launcher, systemd) and bind it to `<LOOPBACK>` only. Do not publish `:5000` or `:3010`. Do not bind OpenVault to a bridge address, and do not add any non-loopback CIDR to `OPENVAULT_SERVICES_ALLOW`. Containers on a bridge network cannot reach a loopback bind; prefer topology 1 so DMS and Cortex call OpenVault on `<LOOPBACK>`. **[NOT VERIFIED]**

## 5. Topology 3: the customer's cloud (AWS, Azure, GCP)

There is **no Helm chart, no Terraform and no cloud preset** in the repos (`docs/DEPLOY.md`: presets and Helm "Not shipped"). Everything here is guidance built on topologies 1 and 2. **[NOT VERIFIED]**: none of it has been run on a cloud.

General pattern: one VM in a private subnet running topology 1 (or compose plus host-run OpenVault), a managed or self-run Postgres, a TLS terminator in front, and egress only from the OpenVault process to the model provider.

| Concern | AWS | Azure | GCP |
|---------|-----|-------|-----|
| VM | EC2, Ubuntu 22.04/24.04, 4 vCPU / 16 GB to start, gp3 disk | VM, same image, Premium SSD | Compute Engine, same image, pd-ssd |
| Postgres | RDS PostgreSQL 16/17, or Postgres on the VM. RDS does not give you `SUPERUSER`; pre-create `dms_viewer`/`dms_steward`/`dms_admin` and grant them to `dms` as in 3.2. `pgcrypto` is allowed on RDS | Azure Database for PostgreSQL Flexible Server, allow the `pgcrypto` extension in server parameters | Cloud SQL for PostgreSQL, `pgcrypto` is supported |
| TLS and entry | ALB with ACM certificate to the VM on `:443`, or nginx with your cert; WAF optional | Application Gateway or Front Door with a Key Vault certificate | HTTPS load balancer with Google-managed or imported cert; IAP in front gives per-user login without `DMS_SSO` |
| Secrets for service tokens | Files on the VM root volume (encrypted EBS); Secrets Manager only for your own use | Same, with disk encryption | Same, with CMEK optional |
| Network rules | Security group: 443 from the corporate range only; no inbound 5000, 3010, 8010, 8090, 5432 | NSG, same | Firewall rules, same |
| Reaching the customer's source DB | VPC peering, Transit Gateway or Site-to-Site VPN to the DB's network; allow-list the VM's private IP | VNet peering or VPN | VPC peering or Cloud VPN / Interconnect |
| Egress to model provider | NAT gateway egress allow-list to the provider hostnames (OpenVault only) | NAT/Firewall with FQDN rules | Cloud NAT with egress firewall |
| Snapshots | EBS snapshots plus the logical backup in section 10 | Disk snapshots | Persistent disk snapshots |

Rules that matter more than the vendor:

1. Keep `:5000`/`:3010` unreachable from anything but the VM itself.
2. The DMS lake (DuckDB files) is on local disk. It is a copy of customer data; pick the region and disk encryption with the customer. (`docs/fde/RUNBOOK.md`: "Data residency")
3. A load balancer with `DMS_BIND_ADDR=<LOOPBACK>` is the compose pattern for IAP or a same-host balancer (`docs/DEMO_RUNBOOK.md`).
4. Kubernetes is not supported today. No ingress, chart or operator exists.

## 6. Topology 4: air-gapped and on-premises

`docs/ARCHITECTURE.md` names an `airgap` profile ("No cloud LLM; OpenVault local or disabled features") and `docs/DEMO_RUNBOOK.md` says the first install is single-tenant, "VPN-only or air-gapped". `deploy/presets/airgap.yaml` does **not** exist (`docs/DEPLOY.md`). So air-gapped is a procedure, not a product. **[NOT VERIFIED]** end to end.

### 6.1 Bring the software in

On a connected build host with the **same OS image and Python minor version** as the target, the least fragile route is to build the finished virtualenvs at the **same absolute paths** as on the target and ship them as tarballs (virtualenvs are not relocatable):

```bash
# on the build host, as the same path layout (section 3.1)
sudo mkdir -p /opt/netie && sudo chown "$USER" /opt/netie
for r in dms Cortex OpenVault; do git clone https://github.com/Netie-AI/$r.git /opt/netie/$(echo $r | tr A-Z a-z); done
# ... run section 3.3 and 3.4 here (uv sync / uv pip install / npm ci && npm run build) ...
tar czf netie-opt.tgz -C /opt netie                       # code + venvs + built front ends
# OS packages for the target: postgresql-17 nginx age sqlite3 (download the .debs or use an internal mirror)
sha256sum netie-opt.tgz > SHA256SUMS
```

On the target, unpack to `/opt` and skip sections 3.3 and 3.4. **[NOT VERIFIED]**

For Docker, `docker save` the images on the build host and `docker load` on the target; the Cortex Dockerfiles install from hash-pinned lock files (`requirements/image-core.lock.txt`), so a local wheel mirror is required for an offline image build. **[NOT VERIFIED]**

### 6.2 Models without the internet

OpenVault has a local hop: a loopback OpenAI-compatible server (llama.cpp at `http://<LOOPBACK>:8080/v1` or Ollama at `http://<LOOPBACK>:11434/v1`) configured by `OPENVAULT_LOCAL_BASE_URL` and `OPENVAULT_LOCAL_MODEL` (default `qwen2.5:0.5b`). It needs no cloud key, the host must be loopback, and OpenVault does not start or download the model. Requests can ask for local-only (`local_only`, 503 `openvault_local_only_unavailable` when unavailable). **[V-repo]** (`vault/local_hop.py`).

Honest limits: the default model is tiny and was built as a plumbing test. **Accuracy on a local model has not been measured**, and the accuracy bar (WRONG = 0 on a held-out pack) must be re-proven for whatever model the customer can host. An alternative is an in-network gateway to an approved model endpoint registered in OpenVault the same way as a cloud provider key. **[NOT VERIFIED]**

### 6.3 Keys, TLS, time, updates

- Generate service tokens on the target host only; never carry a token in the media.
- Use the customer's CA for TLS (`DMS_TLS_MODE=files` or the nginx block in 3.7).
- No NTP means token expiry and SSO sessions break: point the hosts at an internal time source.
- Upgrades arrive as a new signed bundle; follow section 11 using the bundle instead of `git pull`.
- The Create feature's Cursor generator (`DMS_CREATE_GENERATOR`) needs internet and must stay off. Leave `DMS_CREATE` and the other Create flags off unless an in-network generator exists.

## 7. Keys and OpenVault

### 7.1 Principles

- Provider keys exist **only** inside OpenVault (Fernet-sealed `keys.db` under `OPENVAULT_HOME`, master key wrapped by a passphrase). Every custody mutation is loopback-only and audited to `secret_audit.jsonl`.
- DMS and Cortex hold only **OpenVault-issued tokens**: a DMS service token (`DMS_OV_SERVICE_TOKEN_FILE`), a DMS to Cortex key (`CORTEX_API_KEY`), a Cortex FreeRoute spend token (`CORTEX_FREEROUTE_TOKEN_FILE`). They are files with mode 600, never printed, never in an env file in git.
- Models are routed by **role** (`sql`, `tool`, `reason`, `summarize`, `grain`, `ontology`) and bounded by **hard USD caps**, both in the route policy file (`OPENVAULT_ROUTE_POLICY`, loaded per request, no restart). A paid model with no price is `cost_unknown` and is refused; a paid hop with no cap is refused; a cap that would be exceeded refuses (HTTP 402-style `budget_exceeded`) instead of walking to another model. **[V-repo]** (`vault/route_policy.py`).
- Never put a model name in DMS config or code.

### 7.2 First-time vault set-up

Admin token and passphrase are generated into `OPENVAULT_HOME` (`admin_token`, `.passphrase`, both 600). Netie's helper does the following; the endpoints are the API underneath **[V-live]**:

```bash
# A header file keeps the admin token off the command line and out of `ps`.
# Create it once (mode 600): the single line  X-OpenVault-Admin: <contents of $OPENVAULT_HOME/admin_token>
H=/etc/netie/ov-admin.hdr
curl -s http://<LOOPBACK>:5000/api/vault/status -H @$H                       # sealed? passphrase configured?
# set the passphrase once; /etc/netie/passphrase.json is {"passphrase": "..."} with mode 600
curl -s -X POST http://<LOOPBACK>:5000/api/vault/passphrase -H @$H -H 'Content-Type: application/json' --data @/etc/netie/passphrase.json
# unseal after every restart
curl -s -X POST http://<LOOPBACK>:5000/api/vault/unseal     -H @$H -H 'Content-Type: application/json' --data @/etc/netie/passphrase.json
```

Prefer `ovctl.py init-passphrase | unseal | status` (on the operations host at `<OPS_ROOT>/bin/ovctl.py`; **not in a repo**, copy it into your ops tooling) because it reads both from 600 files and prints neither. (`curl -H @file` is the equivalent; `$(cat admin_token)` inside a command line would show the token in `ps`.) The `-H @file` form is **[NOT VERIFIED]** against this API.

### 7.3 Adding a provider key

Use the console on `<LOOPBACK>:3010` from this host only, or the prompt-based script, which uses `getpass` and talks to loopback only:

```bash
cd /opt/netie/openvault && uv run --project OpenMW python scripts/add_key.py --provider anthropic
# rotate:  python scripts/add_key.py --rotate <key-id>
```

Never pass a secret as a command-line argument or paste one into a ticket or chat. **[V-repo]** (`scripts/add_key.py`).

### 7.4 Minting the service tokens for DMS and Cortex

`POST /api/apikeys` with the admin header and `{"label": "<host>:dms-cortex", "tier": "free"}` returns a one-time `token` and a `key_id`. Write the token straight to a 600 file with `O_EXCL` and print only the `key_id`; Netie's `PREPARED-mint-cortex-key.sh` does exactly this. Then set a USD cap for the new caller in the route policy (`caps.callers.<name>.daily_usd`). **[V-live]** pattern, box-specific scripts.

DMS registers its own OpenVault service on first start (`DMS_OV_SERVICE_TOKEN_FILE`), through `POST /keys/services`, which is allowed from loopback plus `OPENVAULT_SERVICES_ALLOW` CIDRs. **[V-repo]**

**Netie internal rule:** auth, grant and key changes (new keys, token mints, SCIM, SSO, caps) go through the founder's 1-hour objection window, and the OpenVault Claude cap is changed only by the founder. A customer install owns its own policy.

## 8. Connecting each database type

Everything lands in DuckDB bronze and every answer runs there (extract-only). The model writes one dialect, DuckDB, whatever the source. The authoritative engine table is in `docs/fde/RUNBOOK.md` (generated by `scripts/fde/kits/matrix.py`). **[V-repo]**

| Engine | `kind` | Flag | Port | Driver / install | Notes |
|--------|--------|------|------|------------------|-------|
| PostgreSQL | `postgresql` | none | 5432 | `psycopg` (core dependency) | Read-only login: `GRANT USAGE ON SCHEMA` + `GRANT SELECT ON <tables>`. Keys are read from `pg_catalog` so a SELECT-only login still sees them. |
| MySQL / MariaDB | `mysql` (book kind `mariadb` uses it) | none | 3306 | `pip install -e '.[sources]'` (PyMySQL) | `GRANT SELECT ON db.*`. |
| SQL Server / Azure SQL | `sqlserver` | none | 1433 | `.[sources]` (pyodbc) **plus the system package `msodbcsql18`** | Encrypt defaults on; read-only connections; `db_datareader`. The legacy Windows "SQL Server" driver cannot reach Azure SQL (TLS 1.2). |
| Oracle | `oracle` | `DMS_CONNECT_ORACLE=1` | 1521 | `.[oracle]` (`oracledb` thin mode, no Oracle client) | `db` is the service name. Uses `ALL_TABLES`/`ALL_CONSTRAINTS`; read-only via allow-list plus `SET TRANSACTION READ ONLY`. |
| GaussDB / openGauss | `gauss` | `DMS_CONNECT_GAUSS=1` | 5432 (or 8000) | `.[gauss]` (psycopg) | openGauss defaults to SHA-256 auth, which stock libpq cannot speak (`auth_method_unsupported`); use the openGauss-built driver or an MD5 user. B-compat mode refused. |
| SQLite | `sqlite` | `DMS_SQLITE_SOURCE_ROOT=<dir>` | file | built in | The env names the directory holding the file. |
| CSV / Excel | not a SQL kind | none | n/a | built in | Studio upload: `POST /v1/studio/ingest`, `/ingest-batch`. |
| Snowflake, BigQuery, Databricks, Redshift | **no connector** | n/a | n/a | n/a | Do not promise these. Export to files or Postgres first. |

Only the digit `1` turns a connector flag on (`true`, `yes`, `on` leave it off). Off, the kind is rejected exactly like an unknown kind.

### 8.1 Credentials

Preferred: the customer's DBA stores the read-only password in the tenant's OpenVault; DMS receives an OpenVault credential reference (`cred_ref`) through `POST /v1/connect/sources` (needs `DMS_CONNECT_API`; the raw secret in that body is rejected). Fallback: `POST /v1/studio/sources/sql` with `{kind, host, port, database, user, password, tables?, max_rows?, space_id}`, where the password is used once and not stored. Type it through a secure prompt or `FDE_SOURCE_PASSWORD`, never in chat or shell history. **[V-repo]**

### 8.2 Network path

The customer's source DB must accept connections from the DMS host only: VPN, allow-listed IP or bastion. Agree it before day 1. No inbound path into DMS internals.

### 8.3 Pull and grant

Create the tenant Space, pull named tables, and read the receipt: `truncated: true` (lake is not the source; raise `max_rows` for that table), `untyped_columns` (numeric columns that stayed text are refused at ask time), `skipped` (a grant problem on their side). Grant only the tables the Space should answer from: `POST /v1/connect/sources/{source_id}/grants`. Every connect today is a **full pull**; there is no incremental refresh yet. The connect book is process memory unless `DMS_CONNECT_V2` is also on; re-connect after a restart otherwise. **[V-repo]** (`docs/fde/RUNBOOK.md` sections 1 to 2).

### 8.4 Tenant provisioning (before the first Space)

A process whose `DMS_TENANT_ID` is not a row in `dms.tenants` answers tenant-scoped writes with HTTP 409 `tenant_not_provisioned`. Provision as an operator command, once per company:

```bash
DMS_TENANT_PROVISION=1 DATABASE_URL=postgresql://... \
  python scripts/ops/provision_tenant.py --tenant-id <uuid> --name "<company>"
```

It inserts the tenant row and a default compute pool, creates no key, grant, user or credential, and a rerun is a no-op. Then set `DMS_TENANT_ID` to that uuid and restart DMS. **[V-repo]** The full onboarding checklist (steward review, verified examples, held-out run with WRONG = 0, handover pack) is `docs/fde/RUNBOOK.md`.

## 9. Feature flags

Rules **[V-repo]**: flags default **off**; the rule is "accepts only the digit `1`". Exceptions found in code are marked. Unset the variable to turn a flag off. A flag does nothing for accuracy until it has been measured on the customer's held-out pack; record the exact flag set per tenant.

`DMS_*` environment variables are read in many modules, so there is no single registry. This table lists the flags an operator is most likely to touch. The authoritative source for "what is on" is the code (`grep -rn DMS_ apps packages`) and `STATUS.md`. The defaults below are from `STATUS.md`, the module docstrings and spot checks of the reading code; **not every flag was individually exercised**.

| Flag | Default | What it does |
|------|---------|--------------|
| `DMS_SSO` | off | Login, roles and OIDC (section 3.8). |
| `DMS_HARNESS_SEC` (+ `DMS_DEMO_KEYS`, `DMS_HARNESS_PII`) | off | Harness security hardening (HARNESS-SEC-01, PR #663, **not on main yet**; auth/key class). |
| `DMS_TENANT_PROVISION` | off | Allows the tenant provision CLI to write. |
| `DMS_TENANT_ID` | a fixed default uuid | Not a flag: the tenant this process serves. Set it per company. |
| `DMS_CONNECT_API` | off | Connect API. Accepts `1/true/yes/on` (exception). |
| `DMS_CONNECT_V2` | off | Stores connections in Postgres instead of process memory. |
| `DMS_CONNECT_GAUSS`, `DMS_CONNECT_ORACLE` | off | Engines. |
| `DMS_SQLITE_SOURCE_ROOT` | unset | Enables the sqlite source in that directory. |
| `DMS_AI_FIRST` | off | AI-first text-to-SQL profile. |
| `DMS_AI_FLOOR` (`_DIRECT`) | off | One `run_sql` call against the executing lake. |
| `DMS_RULES_LEGACY` | **on** | Exception: stays on unless the value is the digit `0` (any other token, a typo included, keeps it on). `0` holds the L1 phrase pack, compile and question-word lanes. |
| `DMS_RULES_OFF`, `DMS_NO_WORD_RULES` | off | Hold rule lanes / rank ontology measures instead of word rules. |
| `DMS_GRAIN_AI`, `DMS_AO_COMPILE`, `DMS_AO_COMPILE_RESILIENT`, `DMS_AO_RESPONSE_FORMAT` | off | AI grain and ontology compile; need an OpenVault-issued key for DMS and the grain/ontology roles. |
| `DMS_ONTO_FROM_DATA`, `DMS_ONTO_PG`, `DMS_ONTO_SPACE_SCOPE`, `DMS_ONTO_GAP_PROPOSE` | off | Ontology from data, persisted in Postgres, scoped per Space, gap proposals. |
| `DMS_VQ_RAG`, `DMS_DEF_VALIDATE`, `DMS_GROUND_DEFS` | off | Verified-example retrieval, definition validation. |
| `DMS_ASK_LADDER`, `DMS_AI_CLARIFY`, `DMS_AI_ARBITER`, `DMS_RECONFIRM_RUN_HELD` | off | Escalation ladder, clarify cards, arbiter, held reconfirm runs. |
| `DMS_JOIN_DECLARED_FK`, `DMS_FK_ORPHAN_DISCLOSE` | off | Declared-FK joins on key-less children, orphan disclosure. |
| `DMS_DEADLINE_ADAPTIVE`, `DMS_TIE_PROJECTION`, `DMS_REPLY_HYGIENE`, `DMS_COMPILE_HONOUR_CHECK`, `DMS_SCALE_MISS`, `DMS_ASKBACK_SPECIFIC`, `DMS_MEASURE_FROM_SCHEMA`, `DMS_SPACE_CATALOG` | off | Ask-quality levers; each has a stub 52-pack number in `STATUS.md`. Not live-model PASS. |
| `DMS_CLOOP_B`, `DMS_LANE_ONTOLOGY_RANKED`, `DMS_LANE_BRONZE_SHEET` | off | Exception: these accept `1`, `true`, `yes`, `on`. |
| `DMS_ASK_STREAM` | off | Real ask steps over SSE. |
| `DMS_INSIGHT_PROOF`, `DMS_MCP`, `DMS_MCP_V2` | off | Proof surface, MCP server (`.[mcp]`). |
| `DMS_SIGN` | off | PAdES signing (tenant key from OpenVault). |
| `DMS_SEMANTIC_IMPORT`, `DMS_SPACE_BENCH`, `DMS_SPACE_KNOWLEDGE`, `DMS_STUDIO_ADJUST`, `DMS_ACTIONS` | off | Studio and action surfaces. |
| `DMS_CREATE`, `DMS_CREATE_DOCS`, `DMS_CREATE_GENERATOR`, `DMS_CREATE_OV_ROLE` | off | Create. `DMS_CREATE_GENERATOR` defaults to `claude_api`; the Cursor path needs `DMS_CREATE_CURSOR_REPO`/`_REF` and an internet-reachable Cursor API. |
| `DMS_FDE_KIT` | off | Enables the FDE field kits (`scan`, `upgrade`, `inspect`, `accuracy`). `list` works while off. |
| `DMS_RUNTIME_SELFCHECK` | off | Startup warnings about key wiring. |
| `DMS_ASK_MODE` | `live` in compose and on the box | `live` calls Cortex. |
| `DMS_DEMO_FALLBACK` | `0` | Demo answers when no model. Keep `0` in production. |
| `DMS_INSIGHTS_TIMEOUT_S` | code default 8; compose and box set 60 | Seconds. Ceiling 60. |

**The 52-pack numbers are stub-mode and are not a live accuracy claim.** Before telling a customer a flag helps, run their held-out pack (`scripts/verify/fde_scenarios.py --live`), keep the `run.json`, and require **WRONG = 0**.

## 10. Backups and restore

### 10.1 What to back up

| Item | How | Notes |
|------|-----|-------|
| Postgres control plane (`dms` db, plus globals) | `pg_dump -Fc` and `pg_dumpall --globals-only` | Tables use `FORCE ROW LEVEL SECURITY`, so dump as the postgres superuser. |
| OpenVault home (`OPENVAULT_HOME`) | `tar czf` of the whole directory while the vault is sealed or idle | Contains the wrapped master key, admin token, `keys.db`, audit. **Without it, every stored provider key is lost.** |
| Service token files (`/etc/netie/*`) | copy separately, encrypted | Not part of the DB dump. Re-minting is possible but is a key change. |
| DuckDB lakes | optional | They are a copy of customer data and can be re-pulled from the source. Back up if re-pull is slow or the source is gone. |

### 10.2 Netie's own scheme **[V-live]**

`offbox-backup.sh` runs daily after 03:30 SGT (driven by `keepalive.sh`): globals, a custom-format `pg_dump -Fc` of every non-template database, and a `tar.gz` of the OpenVault home, packed and **age-encrypted**, copied to a second server with a `.sha256` sidecar, 14 daily copies kept remotely and 3 locally. `--restore-check` restores into a scratch database and compares row counts (a first test matched all 51 tables). The age identity is kept on the box (mode 600) and, separately, on the destination server; **anyone who controls the destination server can open the backups**, so put the key somewhere the backup destination cannot reach when you copy this for a customer.

For a customer, the equivalent minimal job (run as root or via cron/systemd timer, to encrypted storage you control) **[NOT VERIFIED]**:

```bash
set -euo pipefail
D=$(date +%F-%H%M); W=$(mktemp -d); chmod 700 "$W"
sudo -u postgres pg_dumpall --globals-only > "$W/globals.sql"
sudo -u postgres pg_dump -Fc dms > "$W/dms.dump"
tar -C "$(dirname "$OPENVAULT_HOME")" -czf "$W/openvault-home.tar.gz" "$(basename "$OPENVAULT_HOME")"
tar -C "$W" -cf - . | age -r "$(cat /etc/netie/backup.pub)" > "/backups/netie-$D.tar.age"
sha256sum "/backups/netie-$D.tar.age" > "/backups/netie-$D.tar.age.sha256"
rm -rf "$W"
```

### 10.3 Restore (generic steps; adapted from Netie's `RESTORE.md`)

Never restore over a live cluster or a live OpenVault home unless you mean to replace them.

```bash
# 1. decrypt and unpack (identity file never printed)
age -d -i /secure/backup-age.key -o /tmp/restore/bundle.tar /backups/netie-<DATE>.tar.age
tar -C /tmp/restore -xf /tmp/restore/bundle.tar
sha256sum -c /backups/netie-<DATE>.tar.age.sha256

# 2. Postgres scratch check first (does not touch the live db)
sudo -u postgres psql -c "CREATE DATABASE dms_restore_check;"
sudo -u postgres pg_restore --no-owner --no-privileges -d dms_restore_check /tmp/restore/dms.dump
#    compare row counts with the live db, then DROP DATABASE dms_restore_check;

# 3. Real recovery onto an empty cluster
sudo -u postgres psql -v ON_ERROR_STOP=1 -f /tmp/restore/globals.sql     # roles (contains hashes; do not print)
sudo -u postgres psql -c "CREATE DATABASE dms OWNER dms;"
sudo -u postgres pg_restore --no-owner -d dms /tmp/restore/dms.dump
#    re-own the dms schema objects to role dms (bootstrap.sh does this) because --no-owner was used

# 4. OpenVault home (stop OpenVault first)
mkdir -p /var/lib/netie/openvault-home
tar -C /tmp/restore -xzf /tmp/restore/openvault-home.tar.gz
chmod 700 /var/lib/netie/openvault-home
chmod 600 /var/lib/netie/openvault-home/{master.key,.passphrase,admin_token}
# start OpenVault, then unseal (section 7.2)

# 5. clean up: the unpacked tree holds decrypted vault files
rm -rf /tmp/restore
```

On Netie's own stack, `bootstrap.sh` automates the "box reset" case: it re-installs packages, recreates the `dms` roles and database, restores the newest `dms-YYYY-MM-DD.sql.gz`, re-owns objects, rebuilds the venvs and reinstalls the nginx site. `bootstrap.sh --check` only lists what is missing. It does **not** restore the OpenVault home; do that from the encrypted bundle (`<OPS_ROOT>/RESTORE.md`). **[V-live]**

A restore you have not tested is not a backup. Run the scratch check after every change to the backup job.

## 11. Upgrades

DMS and Cortex are pinned to the **same cortex-contract major** (1.x). Upgrade order: back up, OpenVault, Cortex, DMS. **[V-repo]** for the checks; the commands are **[NOT VERIFIED]** on a customer host.

1. **Back up** (section 10) and confirm the restore check.
2. **Precheck** with the FDE upgrade kit (`docs/fde/UPGRADE.md`): shas resolve, pending Alembic revisions, each has a non-empty downgrade, a scratch-database upgrade and downgrade round trip, no revision-number clash, removed `DMS_*` env reads, backup freshness. It never deploys or migrates a customer database:
   ```bash
   DMS_FDE_KIT=1 python scripts/fde/kits/upgrade.py --engagement <dir>/ --phase precheck --target <sha>
   ```
   Exit 0 only when every check passes (FAIL 1, bad input 2, BLOCKED 3; a skipped required check is 1).
3. **Stop DMS**, `git fetch` and check out the target, re-run the `uv pip install` line in 3.3, rebuild Studio (`npm ci && npm run build`) and copy it to the web root, start DMS. Alembic runs on start (`run_migrations`). Compose: `git pull`, rebuild the wheel and `apps/ui/dist`, `docker compose up -d --build`.
4. **Verify**: `GET /health`, then `--phase verify` (re-runs inspect and held-out accuracy). Do not cut over while verify is FAIL, BLOCKED or SKIPPED. Held-out WRONG above the last accepted report is FAIL.
5. **Rollback**: previous checkout plus `alembic downgrade <rev>` (revisions with an empty downgrade block the precheck), or restore the database from step 1. Keep the previous checkout until verify passes.

Migration state on main at the time of writing: head is `0012_sign`. PRs #671 (`0013_gov_console`) and #672 (`0014_scim`, stacked) are not merged.

## 12. Health checks and monitoring

| Check | Command | Healthy |
|-------|---------|---------|
| Postgres | `pg_isready -h <LOOPBACK> -p 5432` | accepting connections |
| OpenVault | `curl -fsS http://<LOOPBACK>:5000/api/healthz` | 200, `{"status":"ok","service":"openvault",...}` |
| Vault sealed? | `GET /api/vault/status` | `"sealed": false` |
| Cortex | `curl -fsS http://<LOOPBACK>:8010/health` | 200 |
| DMS | `curl -fsS http://<LOOPBACK>:8090/health` | 200, `"backend":"postgres"`, `"database_configured":true` (not `"memory"`) |
| Gateway | `curl -fsS -o /dev/null -w '%{http_code}' http://<LOOPBACK>:3080/` | 200 or 401 (basic auth) |
| Studio through the proxy | `https://<STUDIO_HOST>/api/health` | 200 |

`/health` on DMS also reports Cortex and OpenVault reachability (`_probe` of their `/health`), the contract pin and the runtime self-check. **[V-repo]** Netie's `keepalive.sh` is a 30-second loop that restarts any of the above that returns the wrong code; on systemd use `Restart=always` plus an external probe. Watch: OpenVault spend per role against the cap (usage ledger), disk for the DuckDB lakes, and Postgres backups age.

## 13. Troubleshooting

| Symptom | Likely cause and fix |
|---------|----------------------|
| `docker compose up` exits at once with a message naming `DMS_DB_PASSWORD` | Password unset or empty. Set it in `deploy/compose/.env`. |
| Compose API cannot log in to Postgres after you changed the password | The password is applied only when the `dms_pg` volume is first created. `ALTER USER dms PASSWORD ...` inside the container, or recreate the volume (data loss). |
| `/health` shows `"backend":"memory"` | DMS fell back to in-memory Spaces: wrong `DATABASE_URL`, password, or Postgres down. Nothing persists until fixed. |
| Studio opens but every ask fails with `cortex_key_missing` or `insights_bearer_missing` | `CORTEX_API_KEY` empty with `DMS_ASK_MODE=live`. Mint an OpenVault key for DMS (section 7.4) and restart DMS. Startup logs a warning naming it. |
| Ask returns `openvault` 402 or `budget_exceeded` | A hard cap was hit (this is the spend stop, by design). Raise the cap in the route policy (a key/cap change, so follow your approval process). |
| OpenVault returns 403 `vault is sealed` | Unseal (7.2). Run the unseal on every restart. |
| `409 tenant_not_provisioned` on `POST /v1/spaces` | Tenant row missing for `DMS_TENANT_ID` (8.4). |
| `connect_api_disabled`, or a connector kind rejected as unknown | Flag not set to the allowed value (`DMS_CONNECT_API`, `DMS_CONNECT_ORACLE`, `DMS_CONNECT_GAUSS`). Digit `1` only for the engines. |
| Postgres connector returns zero joins/links on a read-only login | Key discovery should read `pg_catalog`; confirm you run a build that has FDE-PG-KEYS-01 (the INFORMATION_SCHEMA blind spot). |
| `auth_method_unsupported` for Gauss | openGauss SHA-256 auth, stock libpq. Use the openGauss driver or an MD5 user. |
| Browser shows a TLS warning (compose, `internal` mode) | Expected: trust Caddy's root, or use `files` mode with your CA. |
| `plain HTTP` to `:8080` returns 400 | Expected: TLS only, no redirect. |
| Caddy/`ui` exits at start: `DMS_TLS_HOST must be a DNS name...` or `DMS_TLS_MODE must be exactly internal or files` | The values are validated before Caddy runs (also rejects `*`). |
| API container build fails: `deploy/wheelhouse must hold exactly one cortex_contract wheel` | Put exactly one 1.2.x wheel there (section 4.1). |
| 502 from nginx on long asks | Streaming responses need `proxy_buffering off` and a long `proxy_read_timeout` (3.7). |
| Cortex refuses to start with `AUTH_DISABLED_WITHOUT_DEV_MODE` | `DMS_AUTH_DISABLED` is set without `CORTEX_DEV_MODE`. Remove it; never use it on a real host. |
| OpenVault console or API reachable from outside | Misconfiguration: rebind to `<LOOPBACK>`, firewall `5000/3010`, and treat keys as exposed (rotate). |
| After a reboot nothing answers a model call | Vault came up sealed. Unseal, then check `/api/vault/status`. |

## 14. What is not verified

- The compose appliance on a customer machine, and any real ask through it. CI proves the TLS and config contract only. The authoring host has no Docker.
- Every systemd unit, the certbot flow, the TLS nginx block, `--frozen` for the OpenVault sync, building OpenVault `apps/web` on a customer host, and a non-root `netie` user. Netie's own stack runs as one user under a keepalive loop.
- Cortex and OpenVault containers beside the compose file, and OpenVault reachability from a container.
- Everything in sections 5 (cloud) and 6 (air-gapped), including offline wheel bundling, local-model accuracy and the Create generator behaviour offline.
- DMS SSO on a live identity provider, redirect URIs, and basic-auth plus SSO together.
- Node version requirements for the two front ends.
- The complete flag table: defaults are from `STATUS.md`, docstrings and spot checks, not an exhaustive test of every flag.
- The customer backup script in 10.2. The Netie scheme in 10.2 and the restore check were run on Netie's own stack (2026-11-29).
- The upgrade commands outside the FDE kit's own checks.
- Whether HARNESS-SEC-01 (#663), governance console (#671) and SCIM (#672) are in the build you deploy: they were open draft PRs at the time of writing.

## 15. Related documents

- `docs/DEPLOY.md`: appliance (compose) TLS and install-time keys in full.
- `docs/fde/RUNBOOK.md`: landing DMS at a new company, day by day.
- `docs/fde/UPGRADE.md`: upgrade kit.
- `docs/DEMO_RUNBOOK.md`, `docs/ARCHITECTURE.md`, `docs/decisions/0004-the-authentication-trust-boundary.md`.
- Cortex: `docs/CORTEX_API.md`, `Dockerfile.core`, `compose.constructor.yml`.
- OpenVault: `docs/SECRETS_CUSTODY.md`, `OpenMW/openmw/openvault/vault/route_policy.py`.
