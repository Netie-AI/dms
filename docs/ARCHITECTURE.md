# ARCHITECTURE

## Topology

```
Browser
  → Caddy / Ingress (LB)
      → apps/web (Next.js)
      → services/api (FastAPI modular monolith)
            → Postgres (users, orgs, ledger, spaces) + RLS
            → DuckLake + Parquet (bronze/silver/gold)
            → Cortex HTTP (engine)
            → OpenVault HTTP (LLM/keys)
```

## Modes

| Preset | Who runs where |
|--------|----------------|
| `local-appliance` | All on one PC/Compose; Cortex/OpenVault may be local URLs |
| `company-server` | Shared Postgres + replicas; users login to org URL |
| `airgap` | No cloud LLM; OpenVault local or disabled features |
| `hpc-slurm` | Same APIs; batch ingest/jobs submitted to Slurm worker |

## Process model

- **Modular monolith** — one API process day 1.  
- **Writer vs readers** — when scaling API replicas, warehouse writes go to a dedicated writer or job; readers use read-only connections.  
- **Slurm is not** the HTTP scheduler — only batch jobs behind the same bronze writer contract.

## Key env

| Var | Default | Meaning |
|-----|---------|---------|
| `DMS_API_URL` | `http://127.0.0.1:8090` | Browser → DMS API |
| `CORTEX_URL` | `http://127.0.0.1:8010` | Engine |
| `OPENVAULT_URL` | `http://127.0.0.1:5000` | Vault / LLM |
| `DATABASE_URL` | `postgresql://dms:dms@127.0.0.1:5432/dms` | Control plane |
| `DMS_JWT_SECRET` | (dev only) | Session signing |
| `CORTEX_PROXY` | `1` | Proxy `/dms/*` to Cortex until extract complete |

## Slice map (`services/api/dms/`)

`auth` · `spaces` · `library` · `ingest` · `query` · `amend` · `audit` · `proxy`
