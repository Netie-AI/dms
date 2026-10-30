---
keywords: [openvault, 10048, sealed, passphrase, auto-unseal, utf-16, 8090, serve-main]
main_idea: Leave serve-main :8090. Second OpenVault on :5000 exits 3 because the first still owns the port. Vault is sealed, bak retired, no passkey. Night watch restarts API only, never unseals. Dirty D:\DMS py nuls are gone; do not start uvicorn from that tree.
---

# OpenVault second bind + sealed vault (2026-09-07)

PREFLIGHT: HIT verify-gates-sealed-vault + full-suite-green. Do not kill :8090/:8010/:5000.

## What is up

- DMS :8090 pid 32688. Leave it (D:\DMS-wt\serve-main / origin/main).
- Cortex :8010 pack=dms.
- OpenVault :5000 healthz 200. GET /api/vault/status: sealed=true, passphrase-scrypt, plaintext_backup_present=false, webauthn_registered=false.
- Electron npm dev then tried to bind :5000 again -> WinError 10048, api exit 3. Expected.

## Do not

- Do not start uvicorn from D:\DMS (feat/sqlsrc-08). metric_tools.py is UTF-8 now (ast.parse ok) but that tree is still dirty vs the live API.
- Do not auto-unseal. Product forbids it.
- Do not wipe OPENVAULT_HOME unless the founder confirms they accept losing every key.

## Founder unseal

http://127.0.0.1:3010 or :5000 vault. Wrong passphrase = 401, keys stay. No passkey on this box. Reset while sealed is a new empty vault, not a password change.
