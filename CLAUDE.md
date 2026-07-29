# CLAUDE.md — DMS agent contract

**Repo:** `netie/dms` (private). Consumer app. Not the Cortex engine.

Read this before any edit. Violations fail CI via `.importlinter` and `tests/invariants/`.

## Who is who

| System | Role |
|--------|------|
| **DMS** (this repo) | Consumer app: Spaces, ingest UX, chat, Studio, audit UI, deploy |
| **Cortex** (`netie/cortex`, private) | Engine: answer engine, F5 gate, F1 ledger, semantic layer, execution |
| **OpenVault** | Keys, leave-machine gate, FreeRoute |
| **netie-platform** (public) | Docs, changelog, customer issues, Releases — support surface |

DMS treats Cortex as an **HTTP service with typed payloads**, never a Python import.

## Hard rules

1. **DMS never imports CortexOS.** HTTP client only (`packages/cortex_client`), pinned to **cortex-contract major 1**.
2. **One ledger** — DMS appends through Cortex (`/v1/ledger/append`). Never maintain a local hash chain.
3. **One Postgres, two schemas** (`cortex`, `dms`). No cross-schema foreign keys.
4. **Excel is source-only.** Outbound is generated export. No `to_excel` / openpyxl save / xlsxwriter.
5. **Exactly five ports** are abstracted (see `packages/core/dms_core/ports.py`): catalog, object store, model provider, serving engine, secrets. No new dependency, abstraction, or config key without a stated swap scenario.
6. **duckdb.execute** only inside `packages/executor`.
7. Every FastAPI **mutation** route must call `compliance_gate` before side effects.
8. `api` may not import `executor` directly — only via `core`. `core` may not import `api`. Nothing may import `CortexOS`.

## Version lines (independent — do not renumber backward)

| Line | Scheme | Moves |
|------|--------|-------|
| cortex-contract | 1.0.0 | rarely — wire format |
| cortex-engine | 2.5.0 → 3.0.0 | per gate; pin via compose image tag |
| dms | 0.1.0 → 1.0.0 | at first paying install |

Engine upgrade = swap a container image tag. No Python version coupling.
Compose is the integration point — one line to pin, one line to upgrade.

Optional Cortex install profiles (Cortex side): `cortex-engine`, `[agentic]`, `[rag]`, `[full]`.
Images: `cortex:X.Y.Z-core` (SME appliance) and `cortex:X.Y.Z-full`. Do not delete agentic — make optional.

## Release ideology

- Private source (`netie/dms`, `netie/cortex`); public front door (`netie/netie-platform`).
- Never put binaries in git (not LFS either). GitHub Releases for installers, GHCR for images.
- Manifest in git (`releases/*.json`) with sha256; Drive/GH are mirrors of bytes.
- Tag = release. Conventional commits. Trunk-based. Feature flags for incomplete work.

## Protected paths

`tests/invariants/**` and `.importlinter` are protected. Any PR touching them fails CI unless the
commit body contains:

```
INVARIANT-CHANGE: <reason>
```

## Tool division

| Claude Code | Cursor |
|-------------|--------|
| Migrations, RLS, schema | UI components, Studio ingest UX |
| Cross-file refactors | Chat surface, diff/confirm screens |
| CI/CD, release pipelines | Prompt / vocabulary tuning |
| Test suites (hostile SQL, invariants) | Exploratory spikes you watch |
| Terminal + git heavy work | Single-file focused edits |

If acceptance is verifiable from a terminal → Claude Code. If you need to look at it → Cursor.

## T0 scope

Skeleton + agent contract + boundary invariants. **No business logic** until a later slice.
