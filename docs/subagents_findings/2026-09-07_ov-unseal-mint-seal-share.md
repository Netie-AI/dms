---
keywords: [openvault, unseal, intermediate, TrustStore, Seal, jwks, live_ask_failed, 500, passphrase-scrypt]
main_idea: Unseal is not enough. /keys/intermediate used a fresh Seal() so mint 500ed while /api/vault/status said sealed=false. Wire TrustStore to the process Seal. Recycle :5000 then unseal once more. Do not start a second OV. Do not kill :8090/:8010.
---

# OpenVault unseal still could not mint (2026-09-07)

PREFLIGHT: HIT ov-second-bind-sealed + openvault-chat-500-decrypt.

## Live proof

- GET /api/vault/status -> sealed=false, passphrase-scrypt, no bak, no passkey.
- GET /keys/jwks -> keys=[].
- DMS POST /v1/chat/ask -> 503 live_ask_failed wrapping OpenVault 500 on POST /keys/intermediate.

## Cause

KeyVault / SecretStore share create_app's Seal. Signing routes did TrustStore() which does seal or Seal(). A new Seal on a passphrase-wrapped vault starts sealed, so _root_private() decrypts into an unhandled 500 while the console reports unsealed.

## Fix (OpenVault working tree; live :5000 now running it)

- app.state.seal = state_seal
- _store(request) -> TrustStore(seal=request.app.state.seal)
- VaultSealedError on issue -> 403, not 500
- Test test_unseal_lets_dms_mint_an_intermediate: 4 related tests passed
- Live while sealed: POST /keys/services 200, POST /keys/intermediate 403 "vault is sealed"

## Apply

Restart reseals. Founder unseals again at http://127.0.0.1:5000. Do not npm run dev while 5000 is up (WinError 10048). Do not wipe the home. Do not auto-unseal.

Live DMS :8090 OpenAPI has no /v1/studio/objects. Ontology objects stay on D:\DMS until that API is restarted on purpose.