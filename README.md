# DMS — Data Management Service

Forward-deployable **ChatGPT for Excel & databases**.
Sibling of Cortex (engine) and OpenVault (keys/LLM). DMS is the **consumer**; Cortex is HTTP-only.

## Version lines

| Line | Now | Notes |
|------|-----|-------|
| cortex-contract | 1.0.0 | Wire format — rarely moves |
| cortex-engine | 2.5.0 (pin via compose) | Floats under contract |
| dms | 0.1.0 | → 1.0.0 at first paying install |

## Quick start (T0 skeleton)

```powershell
cd D:\DMS
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ./packages/core -e ./packages/cortex_client -e ./packages/executor -e ./packages/ledger
pip install -e ".[dev]"
$env:PYTHONPATH = "apps/api;packages/core;packages/cortex_client;packages/executor;packages/ledger"
uvicorn dms_api.app:app --reload --port 8090
```

- API: http://127.0.0.1:8090/health
- Compose: `deploy/compose/docker-compose.yml` (pins Cortex image tag)

## Layout

```
apps/api                 FastAPI
apps/ui                  product chrome (skeleton)
packages/core            ports + compliance_gate
packages/cortex_client   HTTP client from contract/openapi-1.0.0.json
packages/executor        DuckDB / serving only
packages/ledger          append via Cortex HTTP
contract/                cortex-contract OpenAPI
deploy/compose           integration pin
tests/invariants         protected boundary AST checks
releases/                signed manifests (bytes live on GH Releases / Drive)
```

## Agent contract

Read `CLAUDE.md` and `.cursorrules` before editing.

## Docs

| Doc | Purpose |
|-----|---------|
| [docs/PRODUCT_ROLES.md](docs/PRODUCT_ROLES.md) | DMS vs Cortex vs OpenVault |
| [docs/VERSIONING.md](docs/VERSIONING.md) | Three version lines + release flow |
| [AGENTS.md](AGENTS.md) | Cursor / Claude Code instructions |
