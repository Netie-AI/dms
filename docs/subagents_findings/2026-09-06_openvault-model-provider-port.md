---
keywords: [ModelProviderPort, OpenVault, FreeRoute, sealed, CCA, dms-155, classify]
main_idea: First ModelProviderPort impl is OpenVault FreeRoute via httpx. Sealed vault raises VaultSealed and does not POST chat. CCA unwired. Flag stays 0. Do not dual-write seated dms#155 proposer files.
---

# OpenVault ModelProviderPort (2026-09-06)

PREFLIGHT: HIT
reuse: 2026-09-06_prd-ai-semantic-mds.md, 2026-09-06_openvault-nine-route-offline.md, 2026-09-05_openvault-chat-500-decrypt.md
spawn: skip

## What landed on disk

- `packages/executor/dms_executor/openvault_model.py` -- `OpenVaultModelProvider.complete`
- `tests/test_openvault_model.py` -- 8 passed
- `get_model_provider()` re-exported from `dms_executor`
- No CCA wire. `DMS_CCA_CASCADE` untouched.
- No Cortex classify route.

## Verify that ran

- `pytest tests/test_openvault_model.py -q` -> 8 passed
- Live `complete('2+2')` against `:5000` with vault `sealed=true` -> `VaultSealed openvault_vault_sealed` (no chat POST)
- Cortex ledger: `test_contract_ledger_seam.py` + `test_f1_ledger.py` -> 14 passed, 3 skipped
- `GET :8011/health` ok pack=dms. `GET :5000/api/healthz` 200. Status sealed.

## Do not

- Dual-write `cca/proposer.py` / `cascade.py` (seated dms PR #155 CONFLICTING)
- Flip the cascade flag
- Claim unlimited tokens (catalog is 18 providers, fallback is 4 roles, vault sealed, cloud hops DNS-fail)
- Unpark Palantir P1

## NEEDS-YOU

Unseal OpenVault (founder passphrase). Then FreeRoute can complete. Wiring this port into the #155 proposer is the seated writer's job after rebase, or a new unused branch after #155 merges.
