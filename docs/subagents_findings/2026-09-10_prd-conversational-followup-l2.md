---
keywords: [prd-agent, F104, F103, F95, F91, F89, anaphora, follow-up, ordinal-skip, besides, VQ, CCA, L2, C7, chart_spec, EPIC-011, DR-0003]
main_idea: Parent labeled F103; F103 is already propose-select VQ. This paste is F104. Unique self-contained NL stays #38 + Cortex C7 L2. Ambiguous ordinal skip ABSTAINS. EPIC-011 stays CLOSED this session. Prompt-A REFUSE. Slice none.
---

# PRD slice -- conversational follow-up / L2 accuracy / ChatGPT chrome / viz (2026-09-10)

PREFLIGHT: PARTIAL. Reused `2026-09-10_prd-ai-chat-genie-code.md` (F95 Chat AI = Cortex L2 after gate + VQ #38; regex E9/E11 floor KEEP; clarify PARK; Genie Code REFUSE; slice none), `2026-09-09_prd-confusing-nl-e11.md` (F91 confusing NL is E11 remainder not a parser), `2026-09-09_prd-cortex-genie-l1l2.md` (F89 Cortex-as-Genie KEEP; LLM-as-L0 REFUSE), `2026-09-10_l1l2-vq-paraphrase-scale.md` (#38 paraphrases live; curated 10/14 L0 0 WRONG), `docs/decisions/0003-engine-side-accuracy-machinery.md` (Option A prompt-instructions REJECTED). Spawn: skip.

Laptop-ASCII only. PRD Agent Mode B. No tickets. No implement. No mega-epic. No PRD widen.

Founder paste this session: words like besides the first one, list the other top 5; more ambiguous natural asks; prompt-improve Cortex L2 to answer all questions super accurately; ChatGPT-level UI smoothness; generated visualisation workflow for everything.

Split, do not glue. Parent labeled **F103**; ledger F103 already propose-select VQ (`2026-09-10_prd-propose-select-vq.md`). This paste is **F104**. Do not reuse F95 / F91 / F89 / F103. F105 (setup-tokens) landed after this row; do not rewrite it.

## Nearness (artifact named)

Not nearer to a ChatGPT rewrite or a Genie Code notebook. Buyer Chat is still one governed POST of a single question string. A thread in the SPA is not engine memory. A polarity cue named "besides" is not ordinal skip. A rank window of "6 to 10" is not "the first one". No percentage. Ambiguous follow-ups that lack a unique gold must ABSTAIN.

| Artifact checked | What is true on this disk |
|---|---|
| `packages/cortex_client/cortex_client/models.py` `AskRequest` | Fields: question, space_id, tenant_id, session_id. No thread history / prior answer / prior rows. |
| `packages/cortex_contract/answer.py` `AskRequest` (Cortex wheel source) | question, session_id, space_id. Still no history. |
| `packages/executor/dms_executor/__init__.py` `live_ask` ~477 | One `AskRequest(question, session_id, space_id)` per submit. session_id is ACL bind + Cortex bind, not a DMS chat-memory blob. |
| Cortex `CortexOS/dms/answer_engine.py` `_SESSION` ~1055 | Engine remembers last **Cortex** turn keyed by session_id+space_id. `_is_anaphora` = them/those/these + arithmetic. Does **not** match "besides the first one" / "the other top 5" / "skip that one" / "what about the rest". `_remember` runs after a Cortex success. DMS L0 (VQ / metric tool / bronze sheet) never calls it. |
| `packages/executor/dms_executor/demo_ask.py` ~299 | Comment: bare top 10 / what about top 10 counts as sales rank (demo has no anaphora). Rank windows exist (`_rank_window`: N to M). "The first one" is unresolved. |
| `packages/executor/dms_executor/cca/cascade.py` `_POLARITY_CUES` | Includes **besides**, **other**, **skip**. If the flag flips, "besides the first one" can be exclusion polarity, not ordinal skip. `DMS_CCA_CASCADE` default **0**. |
| `apps/ui/src/pages/ChatPage.tsx` + `AppContext.tsx` `runAsk` | Thread UI + suggestion chips + sessionId. One `postAsk` per submit. No clarify loop. |
| `apps/ui/src/components/SimpleChart.tsx` + `AskResponse.chart_spec` | Cortex chart_spec maps to pie/hbar/line/bignum. Studio Publish-and-ask already charts grouped tools. Not every envelope infers a chart (`test_null_chart_spec_no_chart_when_rows_not_inferrable`). |
| `tests/fixtures/curated_ceo/questions.yaml` | Single-turn canonical asks. No follow-up turns. |
| `scripts/verify_demo_live.py` ~226 | Multi-turn "average of them" / "add 2000" as **honesty** gates (`envelope_compute_or_abstain`), not gold answers. |
| Cortex#19 EPIC-011 | **CLOSED**. FOLLOWUP-01..03 closed. Completeness: broader live phrasings beyond corpus remain opportunistic. F9 bound: not anaphora-only. |
| `STATUS.md` 2026-09-10 | #116 OPEN. F95 Chat-AI/clarify/Genie-Code STOP. F41 queued. Do not kill :8090/:8010. |
| GitHub OPEN (verified `gh`) | #132 #116 #108 #38 #29 #27 #8. WIP two-epic: #108/#116 and #38. |

A green VQ top-5 is not a remembered prior for "the other top 5". Cortex L2 cannot resolve anaphora it never saw.

## Human-ask classes (do not glue)

| Class | Example | Unique gold? |
|---|---|---|
| Ordinal skip | besides the first one; skip that one; removing the first one, show top 5 | **No** (ranks 2-6 vs 2-5 vs recompute-excluding-#1) -> ABSTAIN |
| Rest of ranking | what about the rest; next 5; numbers 6 to 10 | **Yes** only if N-to-M is named; else ABSTAIN |
| Same measure, new filter | same but for warehouse B | **Yes** if encoding exists (VQ/CCA); else ABSTAIN |
| Pronoun arithmetic | average of them; add 2000 | EPIC-011 CLOSED; honesty compute-or-abstain |
| Polarity exclude | besides SKU-X; excluding SKU-00397 | CCA #132 when flag can ship; not ordinal skip |
| Underspecified | the other top 5 (of what list?) | ABSTAIN Wave 7 |

AirGPT Act C (`DEMO_RAG.md`): `what about top 6` / `removing the first one, show me top 5` are demo edge prompts, not DMS gold. DISTILL self-contained rank phrasing into VQ paraphrases under #38. Do **not** DISTILL AirGPT RAG as L0.

## Analog table

TAS-DMS Chat. TAS-CORTEX C7 L2 after gate. TAS-AIRGPT for freeform RAG (demo runbook Act C: `what about top 6`). Do NOT DISTILL Genie Code / ChatGPT rewrite. BAN n8n / grok-bot / AGPL / leaked Claude Code. Palantir P1 PARK.

Prefer extend-live #38 VQ paraphrases + Cortex HTTP L2. Do not add AskRequest history this session.

| Piece | KEEP / OPEN / REFUSE / PARK / NO epic | Live analog | Why |
|---|---|---|---|
| (a) Self-contained unique paraphrases / rank windows / warehouse-B encodings | OPEN #38 + Cortex C7 L2 | Studio VQ; `demo_ask` N-to-M windows; live paraphrases 10/14 L0 0 WRONG | Coverage grows only on unique gold. |
| (a) Ambiguous ordinal skip / "the first one" / "the other top 5" | KEEP Wave 7 ABSTAIN | envelope E9; success assertion compute-**or-abstain** | No unique resolution. Inventing ranks is a P0. |
| (a) CCA "besides"/"other"/"skip" as polarity | OPEN #132; flag 0 | `_POLARITY_CUES` | Do not flip. Do not reuse as ordinal parser. |
| (a) Confusing NL despite/excluded | KEEP F91 | E11 remainder | Not this paste. Not a new parser. |
| (a) Clarify-and-offer-similar | PARK F88/F95 | Chat chips are not a clarify loop | PRD ~357. Unpark = amendment. |
| (a) Reopen EPIC-011 / add AskRequest history / feed L0 into `_SESSION` | CLOSED; do not reopen this session | Cortex#19 COMPLETE engine tickets | WIP already two. Decomposition remainder is real; seating it now is a third epic. F9: not anaphora-only. |
| (a) Anaphora regex product in DMS `demo_ask` | REFUSE F28/F83 | demo path is not buyer L0 | Regex stays E9/E11 constructor floor. |
| (b) Prompt-improve so L2 answers every NL ask | KEEP F89 path; REFUSE DR-0003 A | Cortex `_cortex.ask` after VQ/tool/sheet miss | Coverage appetite. Precision-on-answered stays 100.00 pct. |
| (b) LLM as L0 / classify every ask | REFUSE F83/F89 | steward SQL | 0 confidently wrong. |
| (c) ChatGPT-level interactive chrome | KEEP thread+chips; PARK EPIC-023 | `ChatPage.tsx` | Accuracy first. F95 stands. |
| (c) ChatGPT rewrite / Genie Code notebook | REFUSE F88/F95 | AirGPT/OpenIDE exist | Do not widen #29. |
| (d) Generated viz on certified rows | KEEP | SimpleChart + `chart_spec` + Studio publish-and-ask | Already the workflow. |
| (d) Viz for **every** AI answer / new viz product | REFUSE appetite | `test_null_chart_spec_no_chart_when_rows_not_inferrable` | Not every envelope has a chart shape. Palantir P1 PARK. |

## Routing (not a PRD widen)

No mega-epic. WIP #108/#116 and #38 already in flight. Do not seat a third. Do not reopen EPIC-011 this session.

| Piece | Route |
|---|---|
| (a) Conversational / anaphoric / ordinal-skip | **Split.** Unique self-contained = **OPEN #38** + Cortex C7 L2. Ambiguous = **Wave 7 ABSTAIN** (not F91 parser). CCA polarity trap = **OPEN #132** flag 0. Clarify = **PARK F88/F95**. EPIC-011 = **CLOSED**, do not reopen now. |
| (b) Prompt-improve L2 to answer all questions | **KEEP** existing F89 + #38. **REFUSE** DR-0003 Option A. Coverage appetite, not a PRD widen. |
| (c) ChatGPT-level UI smoothness | **PARK F88 / EPIC-023**. KEEP existing chatbot chrome. **REFUSE** rewrite. |
| (d) Generated visualisation workflow | **KEEP** SimpleChart + chart_spec. **REFUSE** viz-every-answer product. |

## First irreversible slice this session

**None.**

Parent must not delete `_UNOBSERVED_ASK`, add an L0 classifier, add a clarify loop, flip `DMS_CCA_CASCADE`, edit system prompts as accuracy, invent ranks 2-6 vs 2-5, or reopen EPIC-011.

If already seated on an OPEN issue, continue only that issue:

- **#38** -- VQ pack / unique paraphrases (including self-contained "what about top 6" if gold is unique). Do not add prompt guidelines (DR-0003 A).
- **#108 / #116** -- extract-only. Not this paste.
- **#132** -- harvest; do not flip the flag; do not dual-write `cca/`.

## What the parent MAY implement now

**From this paste: none.**

## What the parent must NOT build

- Thread history on `AskRequest` this session
- Feed L0 VQ rows into Cortex `_SESSION` this session
- Anaphora regex that guesses "the other top 5"
- Clarify loop / ChatGPT rewrite / Genie Code
- Delete or bypass `_UNOBSERVED_ASK`
- Flip `DMS_CCA_CASCADE`
- System-prompt accuracy (DR-0003 A REJECTED)
- LLM as L0
- Kill `:8090` / `:8010`

## Ledger

Appended **F104** on PRD-001. F103 remains propose-select VQ. F105 (later) is setup-tokens. Next id after this file was written: check ledger tail (do not reuse).
