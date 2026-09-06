---
keywords: [prd-agent, EPIC-CCA, ModelProviderPort, OpenVault, OmniRoute, constructor, palantir-parked, classify, d2b88bd, F85]
main_idea: No new mega-epic. First irreversible work is a DMS OpenVault ModelProviderPort adapter plus pack-certify proposer under open EPIC-CCA, flag stays 0, no Cortex classify route. Proposer code exists only on origin/claude/build-dms-tickets-scale-4df6a9 (d2b88bd), not HEAD. Palantir P1 parked. Unlimited tokens is not a claim.
---

# PRD slice -- AI semantic MDS / offline keys (2026-09-06)

PREFLIGHT: PARTIAL. Reused CCA engagement harvest, F81-F84, OmniRoute LIFT, Constructor SKIP, Palantir PARK, analog-map-plans-hub.

Laptop-ASCII only. PRD Agent. No tickets. No implement.

## Nearness (artifact named)

Not a delivered AI-driven MDS. Buyer Chat is `POST /v1/chat/ask` with CCA off.

| Artifact checked | What is true on this disk |
|---|---|
| `D:\DMS\STATUS.md` | In flight **EPIC-020 (#108) + EPIC-024 (#109)**. EPIC-CCA ships OFF (`DMS_CCA_CASCADE=0`). |
| `D:\DMS\packages\core\dms_core\ports.py` | `ModelProviderPort.complete(prompt)` declared. Swap scenario OpenVault -> Azure OpenAI. |
| `D:\DMS\packages\core\dms_core\__init__.py` | Re-exports the Protocol. Zero classes implement it. |
| `D:\DMS\packages\executor\dms_executor\__init__.py` `get_serving_engine` | Only `ServingEnginePort` has a caller (`Executor`). Matches TAS-DMS section 7 scaffold. |
| `D:\DMS\packages\executor\dms_executor\cca\` | Word-list recogniser (`intent.py` PREFIX_CUES + two-token window). Charter in `cca/__init__.py` still says an LLM may propose. **No `proposer.py` on HEAD.** |
| `D:\DMS` HEAD `1b6513718` | `d2b88bd10` is **not** an ancestor. Proposer lives on `origin/claude/build-dms-tickets-scale-4df6a9` as draft **dms PR #155 CONFLICTING** (do not dual-write). |
| `git show d2b88bd10` | Adds `cca/proposer.py` (AskProposer / pack certify). Follow-up `afb4ca179` records word-list miss **78.05 pct (64/82)** vs model **0.00 pct (0/82)** on 545 labelled; independent in-scope positives **0** so flag cannot flip. Ceiling, not shipped-API number (`ANTHROPIC_API_KEY` absent on that trial). |
| `D:\DMS\docs\subagents_findings\2026-09-05_cca-engagement-independent-1408.md` | Independent merge n=2099: FE 33/1723 (1.9%), FM 176/211 (83.4%). HOLD flag. |
| `D:\Cortex\contract\openapi-1.2.0.json` | Six published contract paths: `/v1/contract/ask`, `drillthrough`, `submit`, `tools`, `ledger/append`, `ledger/verify`. **No classify.** |
| `D:\Cortex\CortexOS\api\contract_routes.py` | Same six plus `POST /jwks/refresh`. Still no classify. |
| `D:\Cortex\CortexOS\constructor_graph.py` | Compile Constructor JSON -> AgenticDSL. Not a second orchestrator. Not n8n. |
| `D:\OpenVault\OpenMW\openmw\openvault\vault\providers.py` | Curated catalog (openai, anthropic, openrouter, groq, google, mistral, nvidia, deepseek, together, fireworks, cerebras, huggingface, ollama, cortex, litellm, github_models, deepgram, siliconflow). Not a 9-hop product. |
| `D:\OpenVault\OpenMW\openmw\openvault\vault\fallback.py` | `ROLE_ORDER = (primary, backup, cheap, free)` -- four roles. 9Router is the **competitor name** in OpenVault PRD F4 / FAQ, already owned by **EPIC-OV-FREEROUTE #15**. |
| `D:\Netie\TAS\README.md` | Lane 2 OmniRoute SHIPPED gateway. Lane 4 Constructor SKIP 665. Lane 7 Palantir **PARK**. BAN n8n / grok-bot / Guaca AGPL / OpenWillow GPL / leaked CC. |
| Landing / Chat | Governed Ask exists in DMS UI. Landing does not prove CCA. Constructor Pages is a ghost (F80). |

No percentage. Invisible ModelProviderPort does not move nearness until Chat uses it under a flag the buyer can see, which it must not yet.

## Routing (not a PRD widen)

Glued founder ask = F39/F71/F73/F78/F83 class. Split:

1. **Smart recognition on Ask** -- already **EPIC-CCA Netie-AI/dms#132** (F81 LLM proposes, pack certifies; F84 allows F81-shaped engagement). No new epic.
2. **Keys / free APIs / OmniRoute** -- already **OpenVault EPIC-OV-FREEROUTE #15** (F4/F11). Offline because Cloudflare Containers is **OV F23 NEEDS-YOU**, not a DMS host. Laptop loopback stays free (DR-0013).
3. **Semantic layer / ontology chat** -- already Wave 7 **EPIC-019** + gated **EPIC-021**. Cortex O1-O5 shipped. Do not clone Palantir.
4. **Constructor workflows + insights** -- Cortex `constructor_graph.py` compile is in-engine (F10). Insights = **EPIC-013**. Generative apps = **EPIC-022** precision-gated. Cortex does not own Constructor UI (F4).
5. **Scale Cortex** -- ticket-runner ops, not a DMS epic.
6. **Unlimited tokens** -- **REFUSE as a claim**. FreeRoute hops exhaust; 402 pack_exhausted is the honest path.

## Epic slices (irreversibility, not value)

WIP already two in flight (020 + 024), both human-inspectable. **Do not light a third.** New work is **queued** under existing parents. One writer on `cascade.py`.

| Order | Slice | Repo | Contract impact | Depends on | Tier | In flight? |
|---|---|---|---|---|---|---|
| 1 | **EPIC-CCA remainder -- OpenVault ModelProviderPort + pack-certify proposer, `DMS_CCA_CASCADE` stays 0** | Netie-AI/dms | **none** | OpenVault loopback FreeRoute (existing `/v1` chat-completions proxy). **Not** a Cortex classify route. | FOUNDATION then CAPABILITY | **queued** |
| 2 | **EPIC-OV-FREEROUTE remainder -- vault more free/BYOK keys offline; hop walk already exists** | Netie-AI/OpenVault | none | Keys in the sealed vault. HT1-HT5 HUMAN_STOP stand. | FOUNDATION (custody) | queued in OV, not DMS WIP |
| 3 | **EPIC-019 trusted assets / value dictionary** | Netie-AI/dms (+ Cortex pack) | additive only if Studio wire still missing fields | EPIC-018 instrument (STATUS: #35 CLOSED 2026-09-05; confirm on gh) | CAPABILITY | queued |
| 4 | **EPIC-013 insights after rows** | DMS + Cortex | none if bullets ride `answer`/`assumptions` | rows on L0/L1/L2 | CAPABILITY | queued |
| 5 | **EPIC-021 customer-shaped semantic layer** | Cortex pack + DMS | none preferred | EPIC-020 has landed one real customer schema | CAPABILITY | gated |
| 6 | **EPIC-022 generative apps over ontology** | DMS + Constructor compile | none | EPIC-018 two-wave 100.00 pct precision-on-answered and coverage >= 60 pct | SURFACE | gated |

### First irreversible slice THIS offline session can build (no Cortex classify)

**EPIC-CCA remainder, ticket-shaped (Epic Agent files; PRD Agent does not):**

> WHEN a maintainer wires `ModelProviderPort` in DMS to OpenVault FreeRoute (loopback keys; `env` is a disclosed cache, never a second vault) and runs the pack-certify proposer from the d2b88bd split (model proposes spans, `binder.certify_pack` decides), THE SYSTEM SHALL record proposals or a loud `degraded` (no silent word-list fallback), SHALL NOT default `DMS_CCA_CASCADE` on, SHALL NOT add `/v1/contract/classify`, and SHALL NOT invent pack members. Asserted on `tests/test_cca_proposer.py` replay plus one live OpenVault hop or an explicit degraded reason. Customer Chat stays the ungated path until in-scope independent filter-positives >= 8 and both rates <= 5 pct.

Do not mint a parallel proposer. **dms PR #155** already holds `d2b88bd10` and is CONFLICTING. First writer rebases that draft: OpenVault `ModelProviderPort` as the live transport (CortexProposer stays inert until a later classify split). Flag stays 0. Do not merge the whole Claude scale branch.

Visible pair while this is invisible: keep 020/024 as the inspectable WIP. Do not queue four foundation epics.

### If Cortex classify is ever wanted later (not this session)

Contract-stability split, three epics, never one:

- EPIC-A Cortex classifier behind **existing** contract (none)
- EPIC-B contract minor + `scripts/export_openapi.py` (additive)
- EPIC-C DMS adopts the field (none)

Breaking the wire is a **decision record**, not an epic.

## Out of scope

- Palantir P1 / AIP / Foundry / SuperRepo / MDS-as-Palantir (PARK; H6; P-DMS-30/31)
- Cortex `/classify` this wave
- Flipping `DMS_CCA_CASCADE` default
- LLM-as-L0 (F83)
- Intent-regex sprawl / Malaysian aliases (F28, F84)
- Unlimited tokens, 250-provider clone, NVIDIA classifier (OmniRoute SKIP)
- Cloudflare Containers as the path (OV F23; founder said cloud container failed)
- Second orchestrator in DMS / Control / Crew (NETIE.md)
- n8n, grok-bot copy, Guaca AGPL, OpenWillow GPLv3, leaked Claude Code, Activepieces 665
- Live federation / MCP-into-customer-DB (F27 / DR-0005)
- EPIC-022 while precision gate unmet
- A sixth port

## File contention

`cascade.py` + `proposer.py` already have a writer: **dms PR #155**. A second CCA epic or a second branch on those files is the refuse condition. 020/024 do not share those files. Do not also seat EPIC-006 C7 on the same ask-path rewrite.

## Analog

TAS-DMS Chat live -- extend `cca/` + ports. TAS-OPENVAULT lane 2 KEEP gateway, LIFT keys into it. TAS-CONSTRUCTOR SKIP clone; use `constructor_graph.py`. TAS-PALANTIR PARK.

## Feedback ledger append (do not rewrite history)

DMS F85, Cortex F25, OpenVault F24 -- same intake, three homes.

## Status

`NEEDS-YOU F85 rebase CONFLICTING dms PR #155 onto OpenVault ModelProviderPort (do not dual-write cca/); park dms#108 or #109 or WIP-override to seat it; Palantir P1 parked; do not claim unlimited tokens`

PRD Agent does not implement and does not file tickets.
