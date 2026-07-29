# DEPLOY

## Day-1: local appliance (Compose)

```powershell
cd D:\DMS
.\scripts\bootstrap.ps1
# API :8090  Web :3000  (SQLite control plane if Docker/Postgres unavailable)
.\scripts\verify.ps1
```

Does **not** require Kubernetes or Slurm. Port **8090** is used for the API on Windows (8080 is often reserved).


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

- Compose: **Caddy** on `:80` → web `:3000`, api `:8080`  
- K8s: Ingress controller (+ cloud LB / MetalLB)

## Slurm

Only with `hpc-slurm` preset. Worker submits ingest/pipeline batch jobs. Web/API stay on K8s or Compose — Slurm is **not** the HTTP control plane.

## External deps

Set `CORTEX_URL` and `OPENVAULT_URL` to reachable siblings. Charts do not embed Cortex/OpenVault images by default.
