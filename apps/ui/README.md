# DMS UI — U0 product chrome

Vite + React + TypeScript + Tailwind SPA. Talks to **DMS API only** (proxied
`/api` → `:8090`). Never imports CortexOS; never binds Cortex `:8010`.

Pre-T0 Next demo patterns (Query rail, Spaces layout, RoleSwitcher, offline
banner) inform this rebuild; nav and stack follow
`DMS_TECHNICAL_ARCHITECTURE.md` §9–10.

## Boundary checklist (Claude Code / CI)

1. **Vite SPA**, not Next / SSR.
2. Left nav ⊆ Chat · Library · Studio · Amend · Audit · Runs · Admin (+ Space switcher in top bar). No Marketplace / ML / Warehouse / Spark clusters.
3. No new ports — HTTP to DMS API only.
4. Excel is source-only (preview / download / export later — no write-back).
5. Amend confirm will call DMS mutation → `compliance_gate` (Cortex F5); UI holds no policy.
6. Audit shows Cortex ledger pointers (`ledger_ref`), not a local hash chain.
7. Spark / Trino / Superset / Jupyter clones are **out of scope**.

## Run

```powershell
cd D:\DMS\apps\ui
npm install
npm run dev
```

Open http://127.0.0.1:3000 — Chat is the landing surface.

Optional: start DMS API on `:8090` so the top-bar health chip turns green.

## U0 acceptance

- Empty Chat shows **six** suggested questions (never blank).
- Scope chip above the input always visible.
- Asking a suggestion loads a **fixture answer envelope** (§4.7); the number is a button.
- Clicking the number opens / focuses the **docked** Sources panel — no modal over the answer.
- Top bar: hamburger, Space switcher, + New, search stub, role chip, user stub.
- Stub routes for Library / Studio / Amend / Audit / Runs / Admin (no 404s).

## Phase pointer

| Phase | Focus |
|-------|--------|
| **U0** (this) | Shell + fixtures |
| U1 | Preview grid / four-clicks-to-bedrock |
| U2 | Spaces + Library + Data Map |
| U3 | Studio + Runs |
| U4 | Amend + Audit |
| U5 | Admin pools |
| U6 | OIDC + SSE hardening |
