# DMS + AirGPT demo runbook

**Audience:** founder / buyer laptop demo  
**Honesty:** DMS = governed SQL + Space ACL (Cortex HTTP). AirGPT = freeform hybrid RAG over real files (Explorer reveal). Do not collapse them into one product pitch.

**Last aligned:** 2026-08-25

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
**Do not demo this on a shared or internet-reachable host**, and do not answer "yes" to
"is it access-controlled per user" - the honest answer is "not yet; that is Option B in
`docs/decisions/0004-the-authentication-trust-boundary.md`, and it is not built."

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
| Postgres Spaces | Yellow by design | EPIC-003 #6 OPEN | Founder choice B: **in-memory for demo**; never claim persisted |
| Reorder / low-stock asks | Green on envelope | ENV-E4 #28 CLOSED (#91) | Listings abstain or cite; no customer 500. Live Cortex still un-run |
| Stack stability | Yellow | ops, not a ticket | Kill stale :8010/:8090 before show; Defender slows Python |
| Explorer reveal on citation | Green | REVEAL-01 dms#26 CLOSED | SourcePanel **Open original** → `POST /v1/library/reveal` (allowlisted roots); AirGPT still has `reveal-path` |

---

## 2. Ports and open URLs

| Product | Start | UI | API / health |
|---------|-------|-----|--------------|
| **DMS** | `D:\DMS\scripts\windows\Start-DMS.bat` | http://127.0.0.1:3000/ | http://127.0.0.1:8090/health |
| Cortex | started by stack | — | http://127.0.0.1:8010/health |
| OpenVault | started by stack | :3010 | http://127.0.0.1:5000/api/healthz |
| **AirGPT** | `cd D:\AirGPT; python clipdrop.py` | http://127.0.0.1:8765 | same host |

Full AirGPT path: `D:\AirGPT\tests\RAG\DEMO_RAG.md`

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
# S4: fail if an upload landed in DMS's DuckDB but not the file chat reads
python D:\DMS\scripts\sync_bronze_to_serving.py --check

# Playground (L0-L3 probe + L4/L5 aspirations) — after stack is up
python D:\DMS\scripts\gen_playground_data.py
# Upload playground/data/* into a Space, then:
python D:\DMS\scripts\playground_ask.py --dry
python D:\DMS\scripts\playground_ask.py --space <space_id>
# Edit prompts in playground/my_questions.yaml and re-run --only <id>

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
| A1 | Certified library | Chat @ `:3000`, company or any Space | `Top 5 selling SKUs by revenue` |
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
| B1 | Upload | Studio → Finance → `15_q3_sales_export.xlsx` |
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
| #19–21 MCP | dms | OPEN blocked | Parked |

---

## 7. Pre-flight checklist (T-15)

- [ ] Kill stale :8010 / :8090 if health hangs
- [ ] Start DMS stack; open `:3000`
- [ ] `python D:\DMS\scripts\verify_demo_live.py` green
- [ ] One practice ask: Top 5 SKUs
- [ ] Start AirGPT `clipdrop.py`; space 8; hybrid_max
- [ ] Practice Reveal on one warehouse xlsx
- [ ] Defender exclusions optional: `D:\DMS`, `D:\Cortex`, `D:\OpenVault`, `D:\AirGPT`

---

## 8. What is not demo-ready (do not promise)

- Postgres-backed Spaces as default (founder: memory + honest banner)
- Object-level row ACL (predicates are still `TRUE`)
- Treating AirGPT and DMS as one stack behind one URL
