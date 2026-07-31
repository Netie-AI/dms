# Versioning & release flow

Three **independent** version lines. Do not renumber Cortex engine back to 1.x.

| Line | Scheme | Moves when |
|------|--------|------------|
| **cortex-contract** | 1.2.0 | Wire format — pin via pip + vendored OpenAPI |
| **cortex-engine** | 2.5.0 → 3.0.0 | Per engine gate; pin via compose/GHCR tag |
| **dms** | 0.1.0 → 1.0.0 | First paying install |

## One trunk

Trunk-based development. `feat/*` from `main`, under a week, squash-merge.
Never maintain two product trunks (that forces double backports forever).
Cut `release/X.Y` only when a customer is pinned and needs a fix — lazily, with an EOL date.

## Optional engine profiles (Cortex side)

Do not delete agentic capability — make optional:

- `cortex-engine` — answer, execution, ledger, F5, semantic layer
- `cortex-engine[agentic]` — planner, durable runs, seeker, OSR
- `cortex-engine[rag]` — embeddings, doc index
- `cortex-engine[full]` — everything

Images from one Dockerfile: `cortex:X.Y.Z-core` (SME appliance) and `cortex:X.Y.Z-full`.

## HTTP boundary

DMS upgrades Cortex by swapping a container image tag. No Python version coupling.
`packages/cortex_client` is generated from `contract/openapi-1.0.0.json`.
In-process import of CortexOS is forbidden in this repo.

## Public front door

| Repo | Visibility |
|------|------------|
| `netie/cortex` | private — source, CI, issues |
| `netie/dms` | private — source, CI, issues |
| `netie/netie-platform` | public — docs, changelog, customer issues, Releases |

Flow: green CI on main → tag → CI builds artifacts → GHCR (private) + signed installer on public Release.
Never put binaries in git (not LFS). Manifest lives in `releases/*.json` with sha256; Drive and GitHub are mirrors.

## Process rules

1. Trunk-based; conventional commits (`feat:`, `fix:`, `refactor:`, `chore:`, `feat!:`).
2. One issue per task; branch name + commit trailer reference it.
3. CI gates merge: lint, mypy, tests, import-linter, invariants.
4. Tag = release. No manual builds.
5. Feature flags for incomplete work on trunk — never long-lived branches.
6. Protected paths: `tests/invariants/**`, `.importlinter` require `INVARIANT-CHANGE: <reason>`.
