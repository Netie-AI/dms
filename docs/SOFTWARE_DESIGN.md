# SOFTWARE_DESIGN — modular & AI-editable

Binding for humans and coding agents.

## Principles

1. **Modular monolith** before microservices.  
2. **Vertical slices** — one feature folder; AI changes one slice per PR.  
3. **Ports & adapters** — domain does not import FastAPI/DuckDB/HTTP clients directly.  
4. **Presets as YAML** — never fork the codebase per deploy mode.  
5. **Contracts first** — OpenAPI / JSON Schema in `packages/contracts`.  
6. **Small files** — target ≤300 LOC per module; split before growing gods.  
7. **Feature flags** — new behavior behind env/flag + test.  
8. **Golden tests per slice** — refuse silent behavior drift.

## Slice template

```
services/api/dms/<feature>/
  __init__.py
  models.py      # pydantic / dataclasses
  service.py     # pure-ish business logic
  router.py      # HTTP only
  tests/test_*.py
```

## Ports (`services/api/ports/`)

- `control_store.py` — users, orgs, ledger, spaces  
- `lake_store.py` — bronze/silver/gold  
- `engine_client.py` — Cortex  
- `vault_client.py` — OpenVault  

Adapters live under `services/api/adapters/`.

## Do not

- Bidirectional Excel write-back  
- Authorize only in the UI  
- Copy entire CortexOS into this repo in one PR  
- Require K8s for SME installs  
- Let agents invent numbers (0 confidently wrong)

## Research anchors (patterns we chose)

| Pattern | Why |
|---------|-----|
| Hexagonal / ports-adapters | Swap Postgres/DuckLake/HTTP without rewriting product logic |
| Vertical slice architecture | AI + humans edit small surfaces; fewer merge conflicts |
| 12-factor config | Forward deploy: local PC vs company server same code |
| Idempotent confirm tokens | Safe amend under concurrency |
| Credential-style path manifests | Local analogue of Unity Catalog vending |

## Autonomy

Agents may **propose** only. Apply requires steward confirm + ledger. See [AUTONOMY_ROADMAP.md](AUTONOMY_ROADMAP.md).
