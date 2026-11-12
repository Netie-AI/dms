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
# API (repo root)
cd D:\DMS
python -m uvicorn dms_api.app:app --app-dir apps/api --reload --port 8090

# UI
cd D:\DMS\apps\ui
npm install
npm run dev
```

Open http://127.0.0.1:3000 — Chat is the landing surface. With API up, Spaces load from
`GET /v1/spaces` and Ask hits `POST /v1/chat/ask` (`DMS_ASK_MODE=demo` by default).
Demo answers use **L2 “generated — check sources”** — never certified green.

`npm run test:e2e` needs the local stack with Cortex up (`Start-DMSStack.ps1 -StartSiblings -StartUi`). Cortex must open its own duckdb, not `DMS_WAREHOUSE_DB` — a shared file makes Studio ingest return ingested=0. The Library receipt spec leaves `e2e_`-prefixed tables in the lake.

## iPhone (IPHONE-PWA-01)

The same SPA installs to the iPhone home screen. There is no native binary and
no App Store build.

1. Serve the appliance over HTTPS (`deploy/compose`, Caddy). The phone must reach
   that host and trust its certificate. `DMS_TLS_MODE=internal` needs Caddy's
   root installed on the phone, so use `files` with a real certificate.
2. Open the site in Safari, tap Share, then Add to Home Screen.
3. Launch "DMS" from the home screen. It runs full screen.

What the phone gets: `public/manifest.json`, a 180px touch icon, 44px top-bar
controls, 16px text fields (no focus zoom), a stacked Library, and Sources in the
chat composer row. `public/sw.js` caches the app shell and hashed build assets
only. It never caches `/api`, `/v1` or `/health`, so an offline phone opens the
app and shows the "DMS API unreachable" banner. It never shows a stored figure.
The service worker registers in production builds only.

One-handed use (IPHONE-PWA-02): a bottom tab bar replaces the hamburger as the
main navigation, an answer shows its figure before its tools, Sources is a bottom
sheet, every control is a 44px target, and the Ontology map is a tap-through list.
Spaces shows the read-only derived ontology of the active Space.

`e2e/iphone-pwa.spec.ts`, `e2e/iphone-onehand.spec.ts` and
`e2e/iphone-ontology.spec.ts` assert all of this at 390x844 against the local stack.

## U0 / demo-core acceptance (this slice)

- Empty Chat shows **six** suggested questions aligned to the demo warehouse.
- Asking hits DMS API; **Divide the revenue by 5** returns computed total÷5 + bar chart (L2).
- Nonsense questions abstain with suggestions (never invent).
- Clicking a number opens the docked Sources panel.
- `DMS_ASK_MODE=live` uses mint→session_bind→ask when Cortex+OpenVault are up.
- Track unfinished work in repo-root `STATUS.md` / `PARKING_LOT.md`.

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
