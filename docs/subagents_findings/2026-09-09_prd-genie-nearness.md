---
keywords: [prd-agent, F88, Genie, Databricks, compound-AI, Genie-Space, knowledge-store, clarifying, Unity-Catalog, Genie-Code, Vector-Search, Model-Serving, P-DMS-29, P-DMS-33, DR-0003, F28, F87, EPIC-019, EPIC-CCA]
main_idea: No new mega-epic. Databricks Genie product/UI/Code/Spark stack is the same REFUSE class as DB-GPT (F87). Win conditions already live under EPIC-019 VQ, EPIC-CCA encodings, EPIC-015 L3 bar, EPIC-020 extract. First slice this session is none. Vault sealed so Chat 503s.
---

# PRD slice -- Databricks Genie nearness (2026-09-09)

PREFLIGHT: HIT. Reused `2026-09-07_prd-db-gpt-harness-semantica.md` (F87 REFUSE Genie-class), `2026-09-07_prd-bronze-cca-ontology.md`, `2026-08-28_three-subagent-scale.md` (Genie bar = precision not clone), `docs/decisions/0003-engine-side-accuracy-machinery.md`, P-DMS-29, P-DMS-33, F28, F66. Spawn: skip.

Laptop-ASCII only. PRD Agent Mode B. No tickets. No implement. No mega-epic.

Founder paste this session: Genie architecture distill (compound AI, Genie Space, knowledge store, NL->SQL+viz+clarifying questions, Unity Catalog) PLUS Genie Code (notebook gen/debug/explain) PLUS Databricks-on-Databricks (Unity Catalog, Vector Search, Model Serving, Spark). Ask: check nearness and improve build towards it.

This is the same class as F28 (Genie writeups = research) and F87 (DB-GPT = Genie/Chat2DB REFUSE) unless a buyer-visible gap is already authorized on an OPEN epic. Checked. No such unauthorized gap. Clone is REFUSE.

## Nearness (artifact named)

Not nearer to governed Chat by absorbing a Databricks Genie appliance. Buyer Chat is still `POST /v1/chat/ask`. Vault sealed, so live ask 503s rather than serving demo numbers (hard rule 11).

| Artifact checked | What is true on this disk |
|---|---|
| `D:\DMS\STATUS.md` 2026-09-08 | Vault **sealed**. EPIC-020 #108 in flight on #116. EPIC-CCA #132 OPEN, `DMS_CCA_CASCADE=0`. VQ-02 #40 CLOSED. EPIC-008 #8 OPEN. EPIC-019 #38 OPEN. Live ask 503. |
| `apps/api/dms_api/routes/chat.py` `POST /ask` | Buyer Chat. `compliance_gate` then `live_ask`. Missing Cortex = 503. Uncaught live fail (incl. `VaultSealed`) = 503 `live_ask_failed` unless `DMS_DEMO_FALLBACK`. |
| `packages/executor/dms_executor/__init__.py` `live_ask` | Order: VQ first, then `metric_tools`, then `bronze_sheet_ask`, then CCA (flag 0), then Cortex HTTP. Envelope E1-E12. No second Text2SQL path. |
| `packages/executor/dms_executor/verified_queries.py` + `apps/api/dms_api/routes/studio.py` `POST /v1/studio/verified-queries` | VQ-02 lives. Space-scoped `_verified_queries`. Studio register in `StudioPage.tsx`. |
| `packages/executor/dms_executor/openvault_model.py` | `VaultSealed` on sealed vault. No auto-unseal. |
| `apps/ui/src/components/SimpleChart.tsx` + `AnswerMessage.tsx` | SVG/CSS charts from envelope `chart`. Pie / hbar / line. Not a Genie viz product. |
| `apps/ui/src/pages/SpacesPage.tsx` `StudioPage.tsx` `AuditPage.tsx` | Spaces, Studio ingest, Audit ledger UI exist. |
| `packages/core/dms_core/ports.py` | Five ports. Catalog swap comment names Glue/Unity as a *port swap*, not a UC clone. |
| `packages/cortex_client` `compliance_gate` + `/v1/contract/ledger/append` | One ledger via Cortex HTTP. Mutation routes gated. |
| `docs/decisions/0003-engine-side-accuracy-machinery.md` | Option A (Genie-style prompt instructions) **rejected**. Accuracy = grain + verify + value dict + verified answers + CI. |
| `docs/decisions/0005-extract-only-is-the-answer-to-sql-sources.md` | No live ATTACH. F27 stands. |
| PRD-001 F28 / F66 / F87 | Genie levers already mapped to Wave 7. Unity Catalog lineage/RBAC **DECLINED** as a catalog build (F66). DB-GPT Genie-class **REFUSE** (F87). |
| GitHub OPEN | #132 EPIC-CCA, #116 SQLSRC-06, #108 EPIC-020, #38 EPIC-019, #29 EPIC-016, #8 EPIC-008. |
| PARKING_LOT | P-DMS-29 Genie writeups = research not build. P-DMS-33 L4/L5 badges parked. P-DMS-19 CRAG parked. |

No percentage. A Genie architecture diagram is not a customer envelope. A green ontology bench on AdventureWorks variants is not Genie UAT.

## Analog table

TAS-DMS Chat + extract. TAS-CORTEX ontology YAML + F8 + semantic_layer + `agent_task`. TAS-OPENVAULT FreeRoute. TAS-AIRGPT / OpenIDE for code-ish surfaces (not DMS Chat). TAS-PALANTIR PARK. BAN n8n / grok-bot / AGPL / 665.

Prefer extend-live. Do not DISTILL Genie into a parallel ask path.

| Genie piece | KEEP / LIFT / SKIP / COPY-none / PARK / REFUSE | Live analog (named) | Why |
|---|---|---|---|
| Compound AI / multi-agent orchestration | KEEP live Cortex; REFUSE DMS clone; COPY-none | Cortex `agent_task` + FreeRoute verify. PRODUCT_ROLES: Cortex owns generic multi-agent. NETIE.md plane 3. | Second orchestrator in DMS is F28/F83/F87. Ask path stays linear. F83 LLM is not L0. |
| Genie Space (curated datasets) | KEEP | Spaces + Studio ingest + bronze/gold tables + Space ACL | Already shipped. Not a Unity Catalog clone. |
| Genie Space sample queries | KEEP / OPEN #38 | VQ-02 `_verified_queries` + Studio POST. EPIC-019 remainder = pack/L0 authority not a new store. | PRD Wave 7: curated examples are the coverage engine. Already authorized. |
| Genie Space column annotations | KEEP / OPEN #132; gated EPIC-021 | CCA encodings + ontology YAML. Cascade flag 0. | Value search is hard rule 12 + CCA binders, not prompt text. |
| Genie Space textual guidelines / knowledge store (prompt mappings, SQL instructions) | REFUSE as accuracy mechanism; PARK P-DMS-29 | DR-0003 Option A rejected. Microsoft even warns the LLM may ignore instructions. | Do not add a prompt-instruction store beside VQ. Pinned SQL is the KEEP analog. |
| NL -> SQL generator as L0 | REFUSE | L2 FreeRoute already exists, verified, E9. L0 is VQ/metrics/bronze-sheet. | F83. Distil finding: harvest questions, do not wire a Text2SQL model. |
| Clarifying questions vs fail-closed abstain | KEEP abstain; SKIP Genie clarify UX; COPY-none | Envelope `abstained` + CCA abstain naming missing encodings/spellings (#132). | PRD names "no clarify-and-offer-similar UX" as a gap, not as an epic. Chatty clarify buys coverage with a second guess. Fail-closed is the law. |
| Visualizations | KEEP | `SimpleChart` + EPIC-013 insights `chart_spec` | Do not clone Genie viz chrome. |
| Unity Catalog permissions / audit | KEEP live; REFUSE UC product; COPY-none | Space ACL, `compliance_gate`, Cortex ledger, `AuditPage`. F66 declined UC lineage/RBAC as a catalog build. | Catalog *port* may later swap Glue/Unity (`ports.py`). That is not shipping UC. |
| Unity Catalog metric views / Spark / lakehouse stack | REFUSE | DuckDB serving + extract-only (DR-0005). Grain guard lives in `scripts/ontology.py` (not on Chat until F41/EPIC-021). | Plane 1/warehouse brand swap already F27 STOP. |
| Genie Code (notebook Python/SQL gen, debug, explain) | REFUSE second product | Pointer + Excel Copilot = EPIC-016 #29. AirGPT/OpenIDE exist. DMS is ChatGPT-for-Excel/databases, not a notebook IDE. | Same class as DB-GPT code-exec. Would contend with Chat and with #29. |
| Vector index / unstructured RAG as **number** ask path | REFUSE | L3 bar: doc-RAG = prose + citations only (EPIC-015). E9: no executed query = no figure. | F26/F32 measured this failure. P-DMS-19 CRAG parked. |
| Vector / hybrid retrieve as **doc** path | KEEP already sliced | EPIC-015 Space-scoped RAG. Not this paste's job. | Do not put Vector Search in front of SQL. |
| Model Serving / Databricks stack | REFUSE | OpenVault FreeRoute buys tokens. NETIE.md plane 1 is not ours. | Five ports. No sixth. No Spark serving runtime in DMS. |
| Feedback loop (useful / not useful) | PARK; maps to NO epic | No thumbs UI in Chat (`apps/ui` grep: none). Bakeoff/oracles already score correctness, not thumbs. | Founder call. Do not mint EPIC-FEEDBACK. DR-0003 already rejected LLM-as-judge at request time. |
| L4/L5 Genie-class badges | PARK P-DMS-33 | Envelope stops at L3. Playground labels are aspirations. | Build via #38 / gated 021, not new badges. |

## Do we already have the Genie win conditions?

Yes, under existing epics. Databricks' own published bar (PRD Wave 7 / F28) is precision-on-answered on curated questions after curation passes, not open-domain clone.

| Win condition | Where it already lives | Status |
|---|---|---|
| Precision-on-answered = 100%, coverage free | EPIC-017 #33 CLOSED, EPIC-018 #35 CLOSED, envelope E9-E12 | KEEP. Hostile pack: 0 WRONG when it answers. |
| Trusted assets / sample queries | EPIC-019 #38. VQ-02 #40 CLOSED (Studio register + in-space L0). Remainder: L0 authority must not stamp `cortex=None` (F83). | OPEN remainder, not a missing product. |
| Semantic + value search | Hard rule 12 + EPIC-CCA #132 encodings/binders. Customer-shaped semantic layer = gated EPIC-021 (unlock: #116 real schema). | OPEN #132 flag 0. Do not flip. |
| Fail-closed vs invent | Abstain envelope + CCA blocked_at + tools-only abstain | KEEP. Opposite of Genie/Power BI fallback-to-general-knowledge (DR-0003). |
| Curated Space of datasets | Spaces + Studio + EPIC-020 extract | OPEN #108/#116 verify. |
| Governance / audit | Space ACL + Cortex ledger + Audit UI | KEEP shipped. |

Genie Code, Spark, Model Serving, Unity Catalog product, knowledge-store prompts, and thumbs-up loops are **not** win conditions on this PRD.

## Routing (not a PRD widen)

Same class as F28/F39/F66/F87. Split. Do not mint Genie-for-SME / Databricks-stack / notebook-assistant.

| Piece | Route |
|---|---|
| Compound AI in DMS / AWEL-class / second Text2SQL | **REFUSE** (F87/F83). Cortex is the only orchestrator. |
| Genie Space chrome / knowledge-store prompt instructions | **REFUSE**. Analog = VQ SQL + CCA encodings, already OPEN. |
| Sample queries / trusted assets remainder | **OPEN EPIC-019 #38** (L0 authority / pack). Do not add a second store. |
| Column encodings / ambiguous filters | **OPEN EPIC-CCA #132**. Flag stays 0. Harvest NL asks only (F84 Distil rule). |
| Extract MSSQL/MySQL -> bronze, then ask | **OPEN #108 / #116**. DR-0005 extract-only. Not live Spark/UC. |
| Demo stranger path residual | **OPEN EPIC-008 #8**. Not Genie UAT. |
| Excel Copilot / notebook-shaped last mile | **OPEN EPIC-016 #29** is Pointer->Excel Copilot, **not** Genie Code. Do not widen #29 into notebooks. |
| Doc RAG / Vector Search as SQL path | **REFUSE** numbers. **KEEP** EPIC-015 L3 prose (already sliced; not in this OPEN set as a writer). |
| Unity Catalog / Model Serving / Spark | **REFUSE**. F66 + NETIE.md plane 1 + F27. |
| Clarifying-question UX | **PARK** (no epic). Authorized analog = CCA abstain-with-spellings on #132. |
| Useful/not useful thumbs | **PARK** (no epic). Founder call. |
| L4/L5 badges | **PARK P-DMS-33**. |
| Genie/AI-BI writeups as a build queue | **PARK P-DMS-29**. Read only. |
| Spaces, Studio ingest, SimpleChart, OpenVault, Cortex ledger, Space ACL | **KEEP** shipped. |
| Cloning Databricks Genie product/UI | **REFUSE** (already F87 DB-GPT class). |

## First irreversible slice this session

**None.**

Vault still sealed. Buyer Chat 503s. #116 is the verifying step (R-0003) and needs passphrase + a real SQL Server and MySQL. Seating a Genie Space / knowledge store / clarify UX / notebook assistant would contend for `live_ask` / envelope / Chat with EPIC-019 and EPIC-CCA -- the same refuse as F87.

WIP: #108 in flight. Do not seat a third Genie epic.

## What the parent MAY implement now (no new epic)

From **this paste: nothing.**

If already seated on an OPEN issue, continue only that issue:

- **#108 / #116** -- extract-only Studio/sql_source. Not Unity Catalog. Not live ATTACH.
- **#38** -- VQ L0-authority remainder only after unseal (needs Cortex). Do not add prompt guidelines.
- **#132** -- harvest labelled questions; do not flip `DMS_CCA_CASCADE`; do not dual-write `cca/`.

None of those is "build towards Genie." They already were the Genie *win-condition* path (F28).

## What the parent must NOT build

- A Databricks Genie product, Genie Space UI, or knowledge-store of prompt/SQL instructions
- A second Text2SQL / compound-AI path beside `live_ask`
- Genie Code notebook gen/debug/explain in DMS
- Unity Catalog / Spark / Model Serving / Vector Search as the number path
- Clarifying-question chat in place of fail-closed abstain
- Thumbs useful/not-useful as an accuracy mechanism
- L4/L5 badges
- Auto-unseal, second OpenVault, kill :8090/:8010

## Status

`NEEDS-YOU F88 stands with F86/F87: unseal OpenVault then #116 chat prove; REFUSE Genie product/UI/Code/UC/Spark/Model Serving; PARK P-DMS-29/33 and thumbs/clarify UX; KEEP Spaces+VQ+SimpleChart+ACL+ledger; win conditions already on #38 #132 #108/#116`
