# justfile — DMS developer tasks
# Requires: https://github.com/casey/just

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

cortex_root := env_var_or_default("CORTEX_ROOT", "D:\\Cortex")

# Vendor the current OpenAPI pin (scripts/sync_contract.py --version) + testvectors from Cortex;
# regenerate cortex_client.generated
sync-contract:
    python scripts/sync_contract.py --cortex-root "{{cortex_root}}"

# Run unit + invariant tests (Postgres optional for control_plane)
test:
    python -m pytest tests/ -q --tb=short

# UI
ui-dev:
    cd apps/ui; npm run dev
