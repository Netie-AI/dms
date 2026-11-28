---
status: proposed
date: 2026-11-28
decision-makers: founder (2026-11-28 18:08 SGT), recorded with the DMS-SIGN draft
outcome: Signing is a default-off feature. The evidence hash chain lives on sign_audit_events. The product ledger stays the Cortex append API.
---

# DR-0011 - Document signing stays off until DMS_SIGN is 1

## Context and Problem Statement

The founder asked for signature capture, a cryptographic PDF signature, an
audit certificate, safe disclosure, database storage, a verify endpoint, and
a REST/SDK hook that Netie Space can call later. Space itself is not changed.
The model may draft a document. It must not sign.

Hard rule 3 says there is one product ledger, through Cortex, and `ledger_ref`
stores pointers only. The founder also asked for a tamper-evident hash chain
on the signing audit table. Those two requirements meet if the chain is the
e-sign evidence certificate and is not a second product ledger.

## Decision

`DMS_SIGN` mounts `/v1/sign` only when the environment value is the digit 1.
Any other value, including unset, leaves the router unmounted and the audit
page without a sign panel.

Signing uses pyHanko PAdES (`/ETSI.CAdES.detached`). The per-tenant private
key and certificate are read from OpenVault refs `signkey/{tenant}` and
`signcert/{tenant}`. They are not written to the repo, to prompts, or to
logs. Tests inject `ephemeral_dev_material()`, a self-signed RSA-2048
certificate. That certificate is not a production trust anchor.

`DMS_SIGN_TSA_URL`, when set, adds an RFC 3161 timestamp through pyHanko's
HTTP timestamper. Unset means no timestamp. `DMS_SIGN_LEDGER` must be the
digit 1 before an audit event is appended to Cortex. The payload is document
id, event type, the two hashes, identity, and event hash. A ledger failure
stores an empty `cortex_entry_id` and does not undo the signature.

The hash chain is columns `prev_hash` and `event_hash` on
`dms.sign_audit_events` only. `dms.ledger_ref` is unchanged and has no
`prev_hash`. `cortex_entry_id` is the pointer when the ledger callback
succeeds.

Space calls `dms_core.signing.sdk.SigningClient`: `create_envelope`,
`add_signer`, `sign`, `verify`, `disclose`. `draft` writes an unsigned PDF.
A capture marked as coming from a model, or an actor named model, assistant,
or ai, is refused with `model_cannot_sign`.

## Consequences

- New dependencies, each with a swap: pyHanko (another PAdES library),
  reportlab (another PDF writer), pypdf (another PDF merger), pillow
  (another image decoder). pypdf stays below 7 because version 7 removed
  merge on a page that is not yet in a writer.
- The blob defaults to the database with its sha256. An object store, when
  one is attached, uses the existing `ObjectStorePort`. That is not a sixth
  port.
- Postgres appends take `pg_advisory_xact_lock` and recheck the chain tip.
  One process signs one document at a time (`_PROCESS_LOCK`). Upgrade path:
  a per-document lock if sign volume outgrows one process.
- This record is proposed. It is the founder build, not a PRD-agent slice.
