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

DMS treats Cortex as an **HTTP service with typed payloads**, never a Python import of the engine.

## Hard rules

1. **DMS never imports CortexOS** (the engine). HTTP via `packages/cortex_client` only.
2. **DMS pins `cortex-contract>=1.2.0,<2`** (install a real wheel — never editable into the Cortex tree) and imports `canonical_manifest_bytes()` from it. Reimplementing canonicalisation is forbidden — a one-byte drift breaks every signature. `cortex-contract` has zero CortexOS imports by its own lint rule.
3. **One ledger** — DMS appends through Cortex (`/v1/contract/ledger/append`). Never maintain a local hash chain. `ledger_ref` stores pointers only.
4. **One Postgres, two schemas** (`cortex`, `dms`). No cross-schema foreign keys.
5. **Excel is source-only.** Outbound is generated export. No `to_excel` / openpyxl save / xlsxwriter.
6. **Exactly five ports** are abstracted (see `packages/core/dms_core/ports.py`): catalog, object store, model provider, serving engine, secrets. No new dependency, abstraction, or config key without a stated swap scenario.
7. **duckdb.execute** only inside `packages/executor`.
8. Every FastAPI **mutation** route must call `compliance_gate` before side effects (call-through to Cortex F5 — no local policy).
9. `api` may not import `executor` directly — only via `core`. `core` may not import `api`. Nothing may import `CortexOS`.
10. **Assert the user-visible output.** Every test for answer-path behaviour must assert on the **rendered answer text** and the **returned rows**, not only on generated SQL. SQL assertions are permitted only *in addition*. A gate that asserts an intermediate artifact will certify a broken feature as working.
10a. **Customer envelope (Phase 0).** Every answer-path property — `badge`, `abstained`, `values`, `sources`, `drillthrough_token`, `audit_id` — must be asserted on the DMS envelope from `POST /v1/chat/ask` via `assert_envelope_valid` (E1–E8). Cortex-side checks are necessary and insufficient. A green badge on abstention prose is a P0.
11. **`DMS_DEMO_FALLBACK=1` is a lying affordance** unless the UI shows a permanent, unmissable banner. Prefer `DMS_DEMO_FALLBACK=0` for any customer-facing or demo-ready run. Silent fallback that still returns 200 with demo numbers is forbidden for ship gates.
12. **Value normalization** — filter values must match the column's actual encoding (`BETA` vs `SKU-BETA`, `KL` vs `Kuala Lumpur`, case/whitespace). A filter that parses, validates, executes, and matches nothing is the most dangerous single failure: plausible number + green badge.

## Version lines (independent — do not renumber backward)

| Line | Scheme | Moves |
|------|--------|-------|
| cortex-contract | 1.2.0 | wire format — pin via pip + vendored OpenAPI |
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
