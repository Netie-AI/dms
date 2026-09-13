# DMS + AirGPT demo runbook

**Audience:** founder / buyer laptop demo, and prove host-online via IAP/CF  
**Honesty:** DMS = governed SQL + Space ACL (Cortex HTTP). AirGPT = freeform hybrid RAG over real files (Explorer reveal). Do not collapse them into one product pitch.

**Last aligned:** 2026-09-13 (prove IAP: DEMO-HOST-01 #163; measured smoke: DEMO-HOST-02 #164; laptop flow still 2026-08-25)

---

## 0. The trust boundary - say this before anyone asks

**DR-0004 Option A, decided 2026-08-25.** There is no authentication in DMS. Identity is
resolved from server-side configuration, and a request that tries to name its own tenant,
actor or role is refused with a 400.

The sentence that must appear in the SOW, and must be said out loud in any demo where a
buyer's IT or compliance function is in the room:

> The API trusts its network. Anyone who can reach the host can act as the configured
> steward. Identity in the ledger is the identity of the deployment, not of a person.

So the install is single-tenant, on the customer's own network, VPN-only or air-gapped.
**Do not demo this on a shared or internet-open `:8090`**, and do not answer "yes" to
"is it access-controlled per user" - the honest answer is "not yet; that is Option B in
`docs/decisions/0004-the-authentication-trust-boundary.md`, and it is not built."
Prove host-online (section 2.1) is IAP/CF in front of loopback `:8090`, not a public bind.

Two consequences worth knowing before the room asks:

- **Object-level permissions are not real yet.** A row predicate keyed to an identity
  nobody verified enforces nothing. `acl_grants` has the columns and the predicate is
  carried on the signed manifest, but every producer emits `TRUE`.
- **A-0007 is CLOSED** (#72): "Company (default ACL)" is a real scope. Omitting
  `space_id` no longer skips the Space check on warehouse preview — missing and
  ungranted both answer 403. Network reach still means acting as the configured
  steward (Option A); that is separate from Space ACL.

---

## 1. Readiness (honest)

| Area | Color | Ticket / epic | What is true |
|------|-------|---------------|--------------|
| L0/L1/L2 ask + Spaces UI | Green | DEMO-PATH-01 #16 CLOSED; SPACE-UI #25 CLOSED (#90) | Core stranger path green; Runs/Amend pass `space_id` |
| Doc chunks schema + ingest | Green | RAG-01 #24 CLOSED; Cortex RAG-02 #33 / RAG-03 #32 CLOSED | Chunks indexed on Space upload; L0/L1/L2 then doc-RAG on abstain |
| RAG envelope + adversarial | Green | DMS RAG-04 #23 CLOSED; RAG-05 #22 CLOSED; Cortex EPIC-015 #34 PARTIAL | Envelope + adversarial ask green; Cortex epic still PARTIAL on older RAG-01..03 boxes |
| Postgres Spaces | Yellow by design | EPIC-003 #6 OPEN | Laptop: **in-memory**; never claim persisted. Prove host-harden: Spaces postgres is Platform's unit, not this epic CLOSED |
| Reorder / low-stock asks | Green on envelope | ENV-E4 #28 CLOSED (#91) | Listings abstain or cite; no customer 500. Live Cortex still un-run |
| Stack stability | Yellow | ops, not a ticket | Laptop: kill stale :8010/:8090 before show; Defender slows Python. Prove: do not kill systemd; Platform owns the host |
| Explorer reveal on citation | Green | REVEAL-01 dms#26 CLOSED | SourcePanel **Open original** → `POST /v1/library/reveal` (allowlisted roots); AirGPT still has `reveal-path` |
| Prove host-online | Yellow | DEMO-HOST-01 #163 (docs); DEMO-HOST-02 #164 (measure) | Platform owns the tunnel. Temp CF hostname below (ROTATE RISK until `TUNNEL_TOKEN`). Smoke: `scripts/smoke_studio_host_online.py`. `:8090` loopback-only. **Not** EPIC-008 COMPLETE |

---

## 2. Ports and open URLs

Laptop column is `Start-DMS.bat`. Prove column is host-harden + IAP/CF. **DMS API bind on prove stays `127.0.0.1:8090`.** The browser URL is the Platform Studio origin, never a public `:8090`.

| Product | Laptop start | Laptop URL | Prove (IAP/CF) |
|---------|--------------|------------|----------------|
| **DMS UI** | `D:\DMS\scripts\windows\Start-DMS.bat` | http://127.0.0.1:3000/ | Temp CF: https://occurred-guest-guaranteed-practitioners.trycloudflare.com (`/studio`). Same origin as `/api`. May rotate until durable `TUNNEL_TOKEN` |
| **DMS API** | same stack | http://127.0.0.1:8090/health | same loopback bind on prove; reach **only** via IAP/CF. Not `0.0.0.0/0` |
| Cortex | started by stack | -- | as deployed on prove (laptop `http://127.0.0.1:8010/health`). Host-side, not a public port |
| OpenVault | started by stack | UI :3010; `http://127.0.0.1:5000/api/healthz` | as deployed. Do not stand Next on the OV e2-micro |
| **AirGPT** | `cd D:\AirGPT; python clipdrop.py` | http://127.0.0.1:8765 | laptop dual-demo only; not the prove 2-min walk |

Full AirGPT path: `D:\AirGPT\tests\RAG\DEMO_RAG.md`

SPA calls are same-origin `/api/...` (`apps/ui/src/lib/api.ts`). Vite `VITE_API_TARGET` defaults to `http://127.0.0.1:8090` **on the process that runs Vite** (host loopback, rewrite strips `/api`). Compose analogue: `deploy/compose/Caddyfile` (Caddy is the only publicly bound appliance port; `/api` -> API). Studio/SqlSourcePanel copy does not claim the API is laptop-only.

### 2.1 Prove host-online (IAP/CF)

**Ticket:** DEMO-HOST-01 #163 under EPIC-008 #8. **Does not** close #8. **Does not** reopen ENV-E4 #28 / CSV-01 #18 / INGEST-SYNC-01 #75 (already CLOSED). Measured tunnel smoke is DEMO-HOST-02 #164 (`scripts/smoke_studio_host_online.py`).

**Honesty (DR-0004 Option A, same as section 0).** IAP or Cloudflare Access is the network door. DMS still has no per-user login. Anyone who can pass the tunnel acts as the configured steward. Identity in the ledger is the deployment, not a person. Say that before the buyer asks.

#### Prerequisites (who owns what)

| Owner | Must already be true | This repo does **not** |
|-------|----------------------|-------------------------|
| Platform / DevOps | prove host-harden UP (systemd + Spaces postgres + IAP to loopback `:8090`). Founder GO 2026-09-13: host-harden smoke PASSED | stand the host, open firewall, change bind address, touch the lake / `LIVE_KEY_ID` / OV e2-micro |
| Platform / DevOps | Tunnel recipe + published **Studio URL** (temp quick tunnel until durable `TUNNEL_TOKEN`) | invent `cloudflared` / `gcloud` / listen-on-all-interfaces commands |
| dms (this section) | Document the steward walk against the published origin | claim EPIC-008 COMPLETE |

Prefer `DMS_DEMO_FALLBACK=0`. A silent demo-number 200 is a lying affordance.

#### Tunnel start (cite Platform recipe -- do not run from dms)

**Platform+DevOps own the prove host and the tunnel.** Product writers do not start it.

Cite, do not duplicate:

- Founder GO on EPIC-008: https://github.com/Netie-AI/dms/issues/8 (2026-09-13) -- IAP/CF to loopback `:8090`; no public `:8090`; no `0.0.0.0/0`.
- In-repo analogue only: `deploy/compose/Caddyfile` + compose `api` `expose: ["8080"]` (API is not the public bind when Caddy is used). Prove replaces a public bind with IAP/CF in front of that loopback. **Do not** treat compose `api_dev` `ports: ["8090:8080"]` as the prove recipe -- that profile is local Vite, not host-harden.
- Recipe location: **outside this repo.** Platform/DevOps own the tunnel process, `TUNNEL_TOKEN`, and hostname. There is no `Netie-AI/netie-platform` / `platform` / `infra` git repo in the org listing as of 2026-09-13. Do not start `cloudflared` from dms.

Do **not** paste host commands here that bind `:8090` on `0.0.0.0` or add a `0.0.0.0/0` firewall rule.

#### Studio URL

`STUDIO_URL` (temp Cloudflare quick tunnel; **may rotate** until Platform installs a durable `TUNNEL_TOKEN`):

https://occurred-guest-guaranteed-practitioners.trycloudflare.com

- **Browser:** `{STUDIO_URL}/studio` -- SPA route (`StudioPage`). Same origin as `/api`.
- **API on prove:** `http://127.0.0.1:8090` on the **host**, loopback-only. Not opened publicly. The CF origin proxies to that loopback. Same-origin `/api` is the supported shape.
- **Platform smoke (2026-09-13):** `GET /` 200 (HTML `netie DMS`); `GET /api/health` 200, `database.backend=postgres`, `persistent=true`, `ask_mode=live`, `demo_fallback=false`. `/studio` 200.
- Cortex / OpenVault: whatever the prove unit files already use (`CORTEX_URL` / `OPENVAULT_URL` on the host). Do not publish them. Do not retarget `LIVE_KEY_ID`.

If the hostname 404s or TLS-fails, it rotated. Ask Platform for the current origin. Do not open `:8090`. Do not invent EPIC-008 COMPLETE.

#### 2-minute walk (open Studio -> point SQL or upload -> ask -> envelope)

Laptop Act A still uses `:3000` (section 4). This walk is prove.

1. **Open Studio.** Browser to `{STUDIO_URL}/studio` (temp CF URL above). Left nav **Studio**. Space chip: Finance (or the Space Platform seeded). Do not point the buyer at `http://127.0.0.1:8090` as "the product" -- that is the API loopback on the host.
2. **Point or upload.**
   - **SQL (SQLSRC-09):** panel **SQL Server / MySQL** (`SqlSourcePanel`). Host / database / user / password (sent once, not stored). **Extract into bronze**. Receipt names tables landed, `extracted_at`, truncated pulls, and declared-join violations. Password field clears after the request. Route: `POST /v1/studio/sources/sql`.
   - **File:** **+** (or Folder) -> `tests/fixtures/ingest/15_q3_sales_export.xlsx` (or the buyer's workbook). Receipt: ingested vs need-attention. Do not promise a serving sync that is still a log line.
3. **Ask.** Tick the landed file(s) -> **Ask about these**. Chat: one grounded question (units / revenue / whatever the receipt table actually holds).
4. **See the envelope.** Badge, answer or abstain, rows, sources. Abstain over a wrong green number. A green badge on abstention prose is a fail.

If health hangs or ask 503s: Platform/host (Cortex/OV on prove), not a dms bind-address fix. Do not open `:8090` to the world "to debug".

#### Measured smoke (DEMO-HOST-02 #164)

`scripts/smoke_studio_host_online.py` + recorded walk `scripts/smoke_studio_iap.md`. Fail closed: `STUDIO_ORIGIN` / `DMS_API_BASE` / `LOCAL_TUNNEL_PORT` have no `127.0.0.1:8090` default. Unset loopback is BLOCKED (`error.type=env.unset`, owner=Platform/tunnel), not PASS. Cursor cloud is not the certifying seat for VPC loopback. Ask/upload BLOCKED prints `error.type` -- never invent PASS.

```powershell
python D:\DMS\scripts\smoke_studio_host_online.py --self-check
$env:STUDIO_ORIGIN = "https://occurred-guest-guaranteed-practitioners.trycloudflare.com"  # may rotate
$env:DMS_API_BASE  = "$env:STUDIO_ORIGIN/api"
python D:\DMS\scripts\smoke_studio_host_online.py
# On prove only:
# $env:LOCAL_TUNNEL_PORT = "8090"
```

`verify_demo_live.py` stays the loopback 31/31 certify. This smoke does not re-run it and does not claim that floor.

---

## 3. Scripts cheat sheet (say / run)

### DMS

```powershell
# Start (OpenVault + Cortex L2 + API + UI + browser)
D:\DMS\scripts\windows\Start-DMS.bat
# or:
D:\DMS\scripts\windows\Start-DMSStack.ps1 -StartSiblings -EnableL2 -StartUi -OpenBrowser

# Gates before audience
python D:\DMS\scripts\verify_demo_live.py
python D:\DMS\scripts\verify_l2_vs_l1.py
python D:\DMS\scripts\smoke_live_ask.py
# Host-online (IAP/CF). Fail closed if STUDIO_ORIGIN / DMS_API_BASE unset. See section 2.1.
python D:\DMS\scripts\smoke_studio_host_online.py --self-check
# S4: fail if an upload landed in DMS's DuckDB but not the file chat reads
python D:\DMS\scripts\sync_bronze_to_serving.py --check

# Playground (L0-L3 probe + L4/L5 aspirations) — after stack is up
python D:\DMS\scripts\gen_playground_data.py
# Upload playground/data/* into a Space, then:
python D:\DMS\scripts\playground_ask.py --dry
python D:\DMS\scripts\playground_ask.py --space <space_id>
# Edit prompts in playground/my_questions.yaml and re-run --only <id>

# GEN-02 live climb (Platform; WRONG=0 law). No laptop default. See scripts/score_climb.md
python D:\DMS\scripts\score_curated.py --self-check
# Offline A/B (no keys). Retrieve + Cortex-miss bind_plan vs exact-match pack.
python D:\DMS\scripts\score_curated.py --ab
# Live isolated A/B (Platform + IAP). Certified-first vs free gen. CRAG grades.
#   $env:DMS_API_BASE = "https://studio.netie.ai/api"
python D:\DMS\scripts\score_curated.py --climb --ab
#   $env:DMS_API_BASE = "https://studio.netie.ai/api"
python D:\DMS\scripts\score_curated.py --climb
# Reports OK/LAYER/ABSTAIN/WRONG vs baseline @ 91c5cc99 (OK7 LAYER10 ABSTAIN9 WRONG0).
# Exit 1 = WRONG>0. Exit 2 = CONFIG (no URL). Exit 3 = BLOCKED (host unreachable).
# Not COMPLETE. Not 99.95%. Do not add --climb to GitHub CI.

# Expect live RED on synonym / empty-filter / Malay / RAG-sum until value-norm + EPIC-019.
# Metrics: precision-on-answered (law = 100%) vs coverage (grows; never buys a WRONG).
# Exit 1 = at least one confidently-wrong answer. DMS_URL defaults to http://127.0.0.1:8090.
#
# Oracle only (no stack; openpyxl recomputes — never hand gold / never DuckDB):
python D:\DMS\scripts\score_answers.py --docs D:\DMS\tests\fixtures\hostile_score --oracle-only
# Live stack (ingest those xlsx into a Space first):
#   $env:DMS_URL = "http://127.0.0.1:8090"
python D:\DMS\scripts\score_answers.py --docs D:\DMS\tests\fixtures\hostile_score --space <space_id>
python D:\DMS\scripts\score_answers.py --help
# Regen fixtures: python D:\DMS\scripts\gen_hostile_score_fixtures.py
# Pack stays red on purpose for synonym / empty-filter / Malay / RAG-sum until EPIC-019.

# Playground — tweak prompts (L0..L3 today; L4/L5 = aspiration labels only)
python D:\DMS\scripts\gen_playground_data.py
# Upload playground/data/* into a Space, then:
python D:\DMS\scripts\playground_ask.py --list
python D:\DMS\scripts\playground_ask.py --space <space_id>
# Copy questions.yaml -> my_questions.yaml, edit prompt: lines, re-run with --pack

# If UI dead / health hangs
Get-NetTCPConnection -LocalPort 8010,8090 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
# then Start-DMS.bat again
```

Desktop shortcut once: `D:\DMS\scripts\windows\Install-DesktopShortcut.bat`

### AirGPT

```powershell
cd D:\AirGPT
python clipdrop.py
# UI: http://127.0.0.1:8765

# Pre-score (optional, before show)
python tests\RAG\warehouse_eval.py --no-llm
python tests\RAG\edge_probe.py --space-id 8

# Set MAX depth + ask
# POST /api/rag/spaces/8/demo-variant  {"variant":"hybrid_max"}
# POST /api/rag/answers  {"space_id":8,"query":"Top 5 selling SKUs by revenue"}

# Open original file in Explorer
# POST /api/rag/reveal-path  {"path":"D:\\AirGPT\\tests\\RAG\\warehouse\\wh_01_sales_core.xlsx"}
```

### Upload fixture (DMS Studio)

`D:\DMS\tests\fixtures\ingest\15_q3_sales_export.xlsx`

### Shared warehouse files (AirGPT, for sync / dual demo)

| Role | Path |
|------|------|
| Clean sales core | `D:\AirGPT\tests\RAG\warehouse\wh_01_sales_core.xlsx` |
| Other warehouse books | `D:\AirGPT\tests\RAG\warehouse\wh_*.xlsx` |
| Scorebook | `D:\AirGPT\tests\RAG\results\warehouse_hybrid_latest.xlsx` |
| Ontology artifact | `D:\AirGPT\tests\RAG\results\ontology_space_8.json` |
| Messy stress (local) | `D:\AirGPT\tests\RAG\messy\` (gitignored) |

---

## 4. Demo flow with subtitles (10–12 min)

### Act A — DMS governed (5 min) — subtitle: "Never invent a number"

| # | Subtitle | Do / say | Exact prompt |
|---|----------|----------|--------------|
| A1 | Certified library | Chat @ `:3000` (laptop) or `{STUDIO_URL}` (prove, section 2.1) | `Top 5 selling SKUs by revenue` |
| A2 | Ops narrative | Point at Insights + chart | `Show warehouse capacity utilisation` |
| A3 | Space Finance | Switch Space chip → Finance | `What is our total spend by supplier country?` |
| A4 | Boundary | Switch → Warehouse Ops, same ask | Same as A3 — expect **abstain** |
| A5 | Multi-turn | Keep same chat | `What was total outbound revenue?` then `Divide that by 5` |
| A6 | Drill / CSV / Reveal | Optional | `How many SKUs…` → Show me why / Download CSV; if SourcePanel shows a file path → **Open original** |

**Say:** "Badge is the contract — L0 human SQL, L1 metric, L2 generated-then-gated, abstain over a wrong green number."

**Skip on stage:** none for E4 (closed #91). Object-level ACL is still `TRUE` (say that).

### Act B — DMS Studio ingest (2 min) — subtitle: "Your Excel into the Space"

| # | Subtitle | Do |
|---|----------|-----|
| B1 | Upload | Studio → Finance → `15_q3_sales_export.xlsx` (prove: same, via `{STUDIO_URL}/studio`, section 2.1). SQL path: SQLSRC-09 form on that page |
| B2 | Receipt | Show ingested=1, bronze table |
| B3 | Serve | If chat cannot see the new table: stop Cortex, `python scripts/sync_bronze_to_serving.py`, restart. Start-DMSStack does this copy before it starts the engine. |
| B4 | Ground | Chat grounded to that file → ask about `units_sold` |
| B5 | Library | Preview bronze rows |

### Act C — AirGPT MAX RAG (4–5 min) — subtitle: "Freeform over the real files"

| # | Subtitle | Do / say | Prompt |
|---|----------|----------|--------|
| C1 | Open pack | AirGPT UI → space **Warehouse Bench** (id `8`) | — |
| C2 | MAX | Set variant **hybrid_max** | — |
| C3 | Same KPI | Compare to DMS Top-5 numbers | `Top 5 selling SKUs by revenue` |
| C4 | Freeform | Show SQL lane vs retrieve | `what about top 6` / exclusion variants |
| C5 | Reveal | Citation → Reveal / `reveal-path` | Opens Explorer on `D:\AirGPT\tests\RAG\warehouse\...` |
| C6 | Edge | Guardrails | `; DROP TABLE Sales;--` / injection probe |

**Say:** "Same warehouse numbers as DMS certified Top-5 — but AirGPT keeps the **filesystem path** so you open the original workbook. Different trust model: hybrid RAG + ontology, not Cortex certified library."

### Act D — Side-by-side eval (optional 2 min) — subtitle: "When each product wins"

| Case | Prefer DMS | Prefer AirGPT |
|------|------------|---------------|
| Buyer needs badge + ledger | Yes | No |
| Space ACL / Finance vs Ops | Yes | Separate RAG spaces |
| Vague doc / notes / messy Excel | Weak (abstain or L2) | **hybrid_max** |
| Open original file in Explorer | **Open original** (`/v1/library/reveal`) | **reveal-path** |
| Exclusion / freeform follow-ups | L1/L2 + confirm chips | Fast, fewer confirm loops |
| Reorder / low-stock listing | DMS envelope (#91) | Try warehouse / inventory books |

---

## 5. Sync demo files (manual — DEMO-SYNC-01 skipped)

Goal: same workbooks visible in both products; AirGPT already stores absolute paths for Reveal.

**Epic-agent 2026-08-03:** no DEMO-SYNC-01 ticket. Manual steps below are sufficient for demo day (F20).

1. Keep AirGPT warehouse pack as source of truth: `D:\AirGPT\tests\RAG\warehouse\`
2. For DMS Studio demos, either:
   - upload the same xlsx into Finance / Warehouse Ops Spaces, or
   - use DMS fixture `15_q3_sales_export.xlsx` for the stranger path
3. DMS: SourcePanel **Open original** on filesystem `origin_uri` (allowlisted). AirGPT: Reveal / `reveal-path`.
4. Dual score: DMS `verify_demo_live` + AirGPT `warehouse_eval.py` / `edge_probe.py` (optional DUAL-EVAL-01 dms#27)

---

## 6. Ticket map (do not invent backlog)

| ID | Repo | State | Demo relevance |
|----|------|-------|----------------|
| #24 RAG-01 | dms | CLOSED | Chunk table + ingest |
| #33 RAG-02 | Cortex | CLOSED | Lexical/hybrid retrieve |
| #32 RAG-03 | Cortex | CLOSED | Route then doc-RAG |
| #23 RAG-04 | dms | CLOSED | Envelope sources + scope chip |
| #22 RAG-05 | dms | CLOSED | Cross-space chunk leak — adversarial ask green |
| #34 EPIC-015 | Cortex | OPEN / PARTIAL | Parent RAG epic — RAG-04/05 checked |
| #26 REVEAL-01 | dms | CLOSED | Explorer reveal from SourcePanel |
| #27 DUAL-EVAL-01 | dms | OPEN optional | Shared Top-5 + edge score vs AirGPT |
| #28 ENV-E4 | dms | CLOSED (#91) | Reorder/low-stock listings no longer 500 |
| #6 EPIC-003 | dms | OPEN | Space serving path; **memory store is intentional** |
| #25 SPACE-UI-ALL | dms | CLOSED (#90) | Runs/Amend scoped; Library/Studio clear on switch |
| #72 A-0007 | dms | CLOSED | Company default ACL is a real scope; missing/ungranted → 403 |
| #19–21 MCP | dms | MCP-01 flag-off | `DMS_MCP=0`; tools wrap existing HTTP |
| #18 CSV-01 | dms | CLOSED | Download CSV; do not reopen |
| #75 INGEST-SYNC-01 | dms | CLOSED | Upload receipt vs serving sync -- do not reopen |
| #163 DEMO-HOST-01 | dms | OPEN (this section) | Prove IAP/CF runbook + Studio copy check |
| #164 DEMO-HOST-02 | dms | OPEN | Smoke path: `scripts/smoke_studio_host_online.py`. Depends on Platform tunnel. Hostname ROTATE RISK until `TUNNEL_TOKEN`. Not COMPLETE |
| #8 EPIC-008 | dms | OPEN / INCOMPLETE | Core path CLOSED; host-online not COMPLETE from docs alone |

---

## 7. Pre-flight checklist (T-15)

Laptop:

- [ ] Kill stale :8010 / :8090 if health hangs
- [ ] Start DMS stack; open `:3000`
- [ ] `python D:\DMS\scripts\verify_demo_live.py` green
- [ ] One practice ask: Top 5 SKUs
- [ ] Start AirGPT `clipdrop.py`; space 8; hybrid_max
- [ ] Practice Reveal on one warehouse xlsx
- [ ] Defender exclusions optional: `D:\DMS`, `D:\Cortex`, `D:\OpenVault`, `D:\AirGPT`

Prove (IAP/CF) -- do not kill prove systemd; do not open `:8090` publicly:

- [ ] Platform tunnel UP (Platform/DevOps). Temp origin: https://occurred-guest-guaranteed-practitioners.trycloudflare.com -- may rotate until durable `TUNNEL_TOKEN`
- [ ] `GET /` and `/api/health` 200; health says postgres. Host API still `127.0.0.1:8090` (no `0.0.0.0/0`)
- [ ] Open `{STUDIO_URL}/studio` (section 2.1)
- [ ] One point-or-upload + one ask; envelope visible
- [ ] `python scripts/smoke_studio_host_online.py` (env from section 2.1). Record outputs in `scripts/smoke_studio_iap.md`. **Not** EPIC-008 COMPLETE

---

## 8. What is not demo-ready (do not promise)

- Postgres-backed Spaces as the **laptop** default (founder: memory + honest banner). Prove host-harden may already run Spaces postgres -- that is Platform's unit, not EPIC-003 CLOSED
- Object-level row ACL (predicates are still `TRUE`)
- Treating AirGPT and DMS as one stack behind one URL
- EPIC-008 COMPLETE / public `:8090` / a dms-owned tunnel recipe
