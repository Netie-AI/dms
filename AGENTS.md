# AGENTS.md — Cursor & Claude Code

**Repo:** `D:\DMS` — product app (not the Cortex engine).
**Before coding:** read `CLAUDE.md`, `.cursorrules`, `docs/PRODUCT_ROLES.md`.

## Who owns what

| System | Path | You may change for… |
|--------|------|---------------------|
| **DMS** | this repo | UX, Spaces, ingest, serving, amend, auth/ACL, deploy |
| **Cortex** | `D:\Cortex` | Engine only — HTTP service; never import into DMS |
| **OpenVault** | `D:\OpenVault` | Keys / LLM proxy / leave-machine gate |
| **Pointer** | `D:\Netie Clicks` | Screen Act — out of DMS demo scope |

## Integration

```
DMS UI → DMS API → HTTP → Cortex (cortex-contract 1.x)
                      → HTTP → OpenVault
```

Compose pins `cortex_engine` image tag. Contract pins wire format.
In-process Cortex import is forbidden in this repo (dev speed lives in Cortex itself).

## Hard rules

- **0 confidently wrong** — abstain over invent.
- **Excel = source only** — generated export outbound.
- **Never import CortexOS** — `packages/cortex_client` only.
- **One ledger** — append through Cortex; no local hash chain.
- **One Postgres, two schemas** (`cortex`, `dms`) — no cross-schema FKs.
- **Five ports only** — catalog, object store, model provider, serving engine, secrets.
- Secrets via OpenVault / env — never commit keys.
- Protected: `tests/invariants/**`, `.importlinter` — need `INVARIANT-CHANGE:` trailer.

## Tool division

Terminal-verifiable acceptance → Claude Code. Visual acceptance → Cursor.

## Distill

Product lock originated in Cortex:
`distill: skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md` (Cortex tree).
