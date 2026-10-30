---
keywords: [prd-agent, F89, Cortex, Genie, L1, L2, live_ask, tools_only, DEMO_TOOLS, F83, EPIC-012, EPIC-019]
main_idea: Cortex-as-Genie is KEEP (PRODUCT_ROLES), not a Databricks clone. Unblock Cortex L1/L2 on ungrounded Finance asks when extra _metric_tools exist -- live_ask defect, not #38 F83 and not EPIC-012 reopen. Object-ground tools-only KEEP. L0 stays steward SQL. Week-ship-all REFUSE.
---

# PRD slice -- Cortex as Genie for DMS, L1/L2 (2026-09-09)

PREFLIGHT: HIT. Reused `2026-09-09_prd-genie-nearness.md` (F88 REFUSE Genie product), `2026-09-09_unseal-curated-vq.md` (vault unsealed, 7/14 L0), `2026-09-08_live-ask-after-unseal.md` (:8090 extra tools intercept Cortex), F83 (LLM is not L0). Spawn: skip.

Laptop-ASCII only. PRD Agent Mode B. No tickets. No implement. No mega-epic.

Founder this session: "Cortex needs to be the engine to power whole dms cortex is the databricks genie for dms :) continue scale build L1 and L2 need to ship the whole product by this week dy"

Split, do not glue: (a) Cortex powers Chat L1/L2, (b) trusted L0 stays steward SQL, (c) ship-everything-this-week.

## Nearness (artifact named)

Not nearer to a Databricks Genie appliance. Nearer to governed Chat if ungrounded Finance asks reach Cortex HTTP. No percentage. Week deadline is appetite, not a PRD widen (same class as F39/F71/F78).

| Artifact checked | What is true on this disk |
|---|---|
| `STATUS.md` 2026-09-09 | Vault **unsealed**. Curated 7/14 L0, 0 WRONG. Cascade 0. Open: #38 #116 #108 #132 #29 #27 #8. No SQL host :3306/:1433. |
| `docs/PRODUCT_ROLES.md` | Cortex = governed orchestration **engine**. DMS does not own generic multi-agent. KEEP Cortex-as-Genie-for-DMS. |
| `packages/executor/dms_executor/__init__.py` `live_ask` ~390-477 | Order: VQ, metric_tool, bronze_sheet, then if `object_ground` OR `len(registered)>len(DEMO_TOOLS)` -> `tools_only_abstain` NEVER Cortex. Else `_cortex.ask` (L1/L2). |
| `packages/executor/dms_executor/metric_tools.py` `tools_only_abstain` | Docstring: grounded object asks do not reach Cortex. The extra-tools OR is wider than that docstring. |
| `docs/subagents_findings/2026-09-08_live-ask-after-unseal.md` | :8090 extra `_metric_tools` -> tools-only on free-text SKU. :8091 origin/main "What is our total spend?" -> L2_VALIDATED. |
| `docs/subagents_findings/2026-09-09_unseal-curated-vq.md` | Top 5 SKUs now VQ L0. Extra-tools intercept still blocks other ungrounded Finance asks. |
| `_BADGE_MAP` in `__init__.py` / `envelope.py` | L1 = Cortex `governed_metric` / `catalog`. L2 = Cortex `generated` / `session` / `query_skill`. Envelope stops at L3. |
| `scripts/verify_l2_vs_l1.py` | Live L0/L1 vs L2 bakeoff. `DMS_L2_ENABLED` default 0; `Start-DMSStack -EnableL2` sets DMS process; Cortex process must also have it. |
| PRD Wave 5 EPIC-012 | **CLOSED**. Basic L2 FreeRoute exists. Do not reopen wholesale. |
| Cortex#11 (gh OPEN) | EPIC-001 / F40 engine half. Free-form is **not a measurement** until this closes (STATUS R-0011). |
| Cortex#104 (gh OPEN) | C7-05 Serve L2 on L0/L1 miss. Parent EPIC-006 #17. Blocked by C7-01..04. Not a DMS epic. |
| GitHub OPEN DMS | #132 #116 #108 #38 #29 #27 #8. Matches STATUS. |
| PARKING_LOT P-DMS-33 | L4/L5 badges parked. |

A green VQ pack is not Cortex-powered Chat. A tools-only abstain on Finance is not L1/L2.

## Analog table

TAS-DMS Chat. TAS-CORTEX plane 3 engine + `agent_task`. TAS-OPENVAULT FreeRoute. TAS-AIRGPT / OpenIDE not Chat. TAS-PALANTIR PARK. BAN n8n / grok-bot / AGPL / 665.

Prefer extend-live `live_ask` Cortex HTTP. Do not DISTILL Databricks Genie UI.

| Piece | KEEP / LIFT / SKIP / COPY-none / PARK / REFUSE | Live analog | Why |
|---|---|---|---|
| Cortex as Databricks Genie for DMS | KEEP | PRODUCT_ROLES + NETIE.md plane 3 + `_cortex.ask` | Engine, not a product clone. F88 already KEEP compound AI in Cortex. |
| Databricks Genie UI / Space chrome / Code / Spark / UC | REFUSE | F87/F88 | Same class as DB-GPT. |
| LLM as L0 | REFUSE | F83. L0 = VQ / metric_tool / bronze_sheet steward SQL | DuckDB local in executor is allowed. Engine L0 stamp with cortex=None is the #38 remainder. |
| Object-grounded tools-only (`objects.*`) | KEEP | `tools_only_abstain` docstring | Isolation. Named-sheet already survives (`test_named_sheet_survives_tools_only_space`). |
| Extra `_metric_tools` blocking ungrounded Finance | REFUSE that guard | `live_ask:429` `len(registered)>len(DEMO_TOOLS)` | Gap vs :8091. Not object isolation. |
| L1 governed_metric | KEEP / OPEN path | Cortex HTTP + `_BADGE_MAP` | Must be reachable after VQ/tool/sheet miss. |
| L2 generated/session/query_skill | KEEP (EPIC-012 CLOSED) | `verify_l2_vs_l1.py`, `-EnableL2` | Ops flag, not a new epic. Cortex#104 is engine serve-on-miss, blocked, do not seat from DMS. |
| Ship all epics this week | REFUSE as PRD widen | F39/F71/F78 glued ops | Appetite. Name what can vs cannot. |
| L4/L5 badges | PARK P-DMS-33 | Envelope L0-L3 | |

## Routing (not a PRD widen)

No mega-epic. No EPIC-012 reopen. No tickets from this row.

| Piece | Route |
|---|---|
| Cortex powers Chat L1/L2 | **KEEP** already authorized. Gap = extra-tools intercept. **live_ask defect, no new epic.** |
| Unblock Cortex when extra metric tools exist | **Not #38 F83** (that is L0_CERTIFIED with cortex=None). **Not EPIC-012** (CLOSED; L2 machinery exists). **Repo-infra / ask-path defect.** Parent MAY implement now. |
| Trusted L0 steward SQL | **OPEN #38** F83 remainder. VQ/tools/bronze_sheet DuckDB KEEP. Do not mint engine L0 with cortex=None. |
| Object-ground tools-only | **KEEP**. Do not delete `object_ground` branch. |
| Ship-everything-this-week | **REFUSE** PRD widen. Ops appetite. |
| Clone Genie product/UI | **REFUSE** F87/F88. |
| LLM as L0 / second orchestrator | **REFUSE** F83. |
| EPIC-012 L2 reopen | **REFUSE**. Flag/ops only. |
| Cortex#104 C7-05 | **Cross-ack only.** Cortex EPIC-006. Blocked. Do not seat from DMS. |
| Cortex#11 F40 free-form | **OPEN** Cortex. Cannot close from DMS this week. Free-form stays unmeasured. |
| Extract MSSQL/MySQL | **OPEN #108/#116**. No host. Cannot ship this week. |
| CCA encodings | **OPEN #132**. Flag stays 0. Do not flip. |
| Excel Copilot | **OPEN #29**. Not this session. |
| Two-minute demo | **OPEN #8**. Not this session. |
| Dual-eval AirGPT | **OPEN #27**. Not seated. |
| L4/L5 / Genie writeups / thumbs / clarify UX | **PARK** P-DMS-33 / P-DMS-29. |

## What CAN ship this week vs cannot

CAN (buyer Chat on this lake, vault already unsealed):

1. L0 VQ + authored tools + named-sheet (already 7/14 L0, 0 WRONG).
2. Cortex L1/L2 path **live** on ungrounded Finance asks after the extra-tools guard is narrowed, with `-EnableL2` on **both** DMS and Cortex processes.
3. Precision-on-answered 0 WRONG held (abstain over invent).

CANNOT (false if claimed this week):

- #116 / #108 COMPLETE (no :3306/:1433).
- #132 cascade on (flag 0, engagement not shippable).
- #29 Excel Copilot last mile.
- #8 two-minute demo asset done.
- Cortex#11 stranger free-form as a measurement (F40 engine).
- All open epics closed.
- L4/L5 badges.

## First irreversible slice this session

**Yes.** File: `packages/executor/dms_executor/__init__.py` `live_ask` ~429.

Narrow the guard to `object_ground` only (KEEP). Stop using `len(registered) > len(DEMO_TOOLS)` to skip `_cortex.ask` on ungrounded company-default asks.

Buyer-visible assertion: `POST /v1/chat/ask` on Finance (no `objects.*` ground, question is not a VQ / tool-name / named-sheet hit) while extra `_metric_tools` exist SHALL return a Cortex-mapped envelope (`L1_GOVERNED_METRIC` or `L2_VALIDATED` or Cortex `ABSTAIN` via `map_ask_response_to_envelope`) -- NOT tools-only text "Ask by tool name". Same Space, `objects.*` grounded ask SHALL still tools-only abstain. Named-sheet L0 SHALL still win before Cortex (`test_named_sheet_survives_tools_only_space`). Hard rule 11: no demo fallback. Do not kill :8090/:8010. Do not second OV. Do not flip `DMS_CCA_CASCADE`.

WIP: #108 in flight. This defect is not a third epic.

## What the parent MAY implement now

1. Narrow `live_ask` extra-tools intercept as above. One test: extra registered tools + ungrounded Finance ask reaches Cortex (mock `_cortex.ask`), object-ground still abstains.
2. Continue **#38 F83** L0-authority remainder separately (do not conflate with 1).
3. Seed more lake-matched VQs under #38.
4. Ops: `Start-DMSStack.ps1 -EnableL2` and confirm Cortex process env; run `verify_l2_vs_l1.py` / `score_curated.py --live`.

## What the parent must NOT

- Mint a mega-epic or reopen EPIC-012
- Clone Genie UI / Genie Code / Spark / Unity Catalog
- Put LLM as L0 or stamp `L0_CERTIFIED` with `cortex=None` as engine authority
- Delete object-ground tools-only
- Flip `DMS_CCA_CASCADE`, kill :8090/:8010, start a second OpenVault
- Seat #116 without a real SQL host
- Seat Cortex#104 / EPIC-006 from DMS
- Claim week-complete / Cortex#11 closed / L4/L5 badges
- `DMS_DEMO_FALLBACK=1`

## Status

`DONE F89: Cortex-as-Genie KEEP; extra-tools intercept is live_ask defect this session; L0 stays #38 F83; week-ship-all REFUSE`
