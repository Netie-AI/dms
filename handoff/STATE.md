# handoff/STATE.md

Netie Lead and Claude Code share this file. Read RULES.md first, then this file, then CLAUDE_CONTINUE.md.
The block between the MACHINE markers is generated: `python scripts/handoff/update_state.py`. Run it before you write.
Everything else is hand-kept. Update it in the same PR as the work. No secret values anywhere in this file.

**Hand part last updated:** 2026-11-27 17:43 SGT by lead (seed, from the founder ask t1846u)

## Goal

DMS is a fully AI-driven alternative to DB-GPT, Cortex Analyst and Genie. WRONG=0, and reconfirm-with-Yes instead of abstaining.
Ship DMS, Cortex and the existing apps end to end. The 52-question pack in `scripts/verify/` is the number of record.
Palantir-style solutions and the three-agent app generator are deferred until there are clients.

## Mains (hand-kept pointers; the machine block below is the live truth)

| Repo | Merged today (SGT) | Squash |
|------|--------------------|--------|
| OpenVault | #193 fix(freeroute): skip a per-hop 402 and try the next hop | `729be72a` |
| OpenVault | #194 fix(freeroute): service tier and persist limiter limited_by (merged 17:39 SGT, after the 17:38 brief that called it draft) | `1d7595fb` |
| Cortex | #490 FreeRoute: drop the urllib lock, send OpenVault role behind a flag | `30463b00` |
| DMS | #569 COMPILE-CHECK-01: do not serve compile SQL the AI checker rejects | `2f61927f` |

OpenVault main `1d7595fb` is #194. The box OpenVault still runs `729be72a`, so it does not yet have #194.

## Box stack (Netie Lead's box, as of 2026-11-27 17:38 SGT, verify before relying)

| Part | Port | Notes |
|------|------|-------|
| Postgres 17 | 5432 | |
| OpenVault | 5000 | at `729be72a`. Starts sealed after a restart: unseal with `ovctl.py unseal`. Started via `/home/box/netie-run/bin/ov-api.sh` with the Claude role policy file (`route_policy.claude.json`, local, backed up in `/home/box/netie-run/backups/`) |
| Cortex | 8010, 8011 | being moved from `304bf84` to `30463b00` |
| DMS | 8090 | at `497d1bf`. DMS main is `2f61927f` |
| nginx | 3080 | basic auth |

Studio hosting: the netie-studio VM runs cloudflared under systemd. The box reaches it through a reverse SSH link
(`/workspace/studio-host/revtunnel.sh`) to box nginx :3080. The revtunnel is UNSUPERVISED: if the box restarts, studio.netie.ai goes dark until someone restarts the script. The VM firewall allows only port 22.

## OV routing facts

- Role policy starts every role on Anthropic: Sonnet for sql, tool, summarize; Opus for reason, grain, ontology. Fallback order: groq, openrouter, google, nvidia. Cortex sends `model = chosen.requested`; OpenVault ignores it on a role walk.
- The 429s come from the TokenBudgetLimiter, not a provider. The `box:dms-cortex` key is on the free tier (20 rpm, 40k tpm, `DEFAULT_TIERS` in OpenVault `ratelimit.py`). It rejects in `app.py` before any hop, which is why `hop_attempts` shows no 429. The per-hop 402 fall-through (#193) does not cover this case.
- Fix: OpenVault [#194](https://github.com/Netie-AI/OpenVault/pull/194) MERGED `1d7595fb` (head was `8a9d9f35`): service tier plus persisted `limited_by`. The box must be moved to `1d7595fb` before the retier does anything.
- Retiering the `box:dms-cortex` key is PENDING at or after 18:20 SGT today, on a box OpenVault that has #194. It is a key and limit change: the founder 1-hour objection window applies, and Netie Lead never raises limits on its own.
- hp1 table: NOT SEEDED. The brief named it but did not include its contents. Netie Lead pastes it here at the first real update.

## Done today (2026-11-27 SGT)

- OpenVault [#193](https://github.com/Netie-AI/OpenVault/pull/193) merged `729be72a`
- OpenVault [#194](https://github.com/Netie-AI/OpenVault/pull/194) merged `1d7595fb` (17:39 SGT)
- Cortex [#490](https://github.com/Netie-AI/Cortex/pull/490) merged `30463b00`
- DMS [#569](https://github.com/Netie-AI/dms/pull/569) merged `2f61927f`
- DMS [#567](https://github.com/Netie-AI/dms/pull/567) ADJUST-SQL-VQ-01 and [#565](https://github.com/Netie-AI/dms/pull/565) UNICODE-IDENT-01 are the two merges before it on main

## In flight

Live list: the machine block. Hand notes only:

- DMS [#570](https://github.com/Netie-AI/dms/pull/570) DMS-AI-ABSTAIN-01, draft, head `8ad10a13` (opened 17:39 SGT). Flag `DMS_ASK_LADDER`, default off. Touches `/ask`, so tier:full, and it has no tier label yet. Its own body reports, on a stub with no model: ladder off correct=43 wrong=0 reconfirm=5 ai=0 rule=43. Armed live 52 with a model: not measured.
- Box OpenVault to `1d7595fb` (unseal after restart), then the retier.
- Cortex boxes :8010/:8011 move to `30463b00`, then smoke.
- DMS has many long-lived draft PRs (see the machine block). Do not close, rebase or delete any of them without the founder.
- No PR is PASS. Armed grade52 is not run on current main. 52 not remeasured.

## Next steps per lane

Lane names come from the founder ask. NL-13 and NL-14 detail was not in the brief that seeded this file, and no GitHub issue or PR carries those names, so those two are placeholders. Netie Lead fills them at its first update. Claude Code takes the top NEXT item of a lane only after it
has been filled in, or after the founder names the item in an issue.

### NL-13

- NEXT: Netie Lead to write the first concrete ticket here (id, branch, acceptance command).

### NL-14

- NEXT: Netie Lead to write the first concrete ticket here.

### DMS-AI-ABSTAIN

- Standing direction (from DMS STATUS.md): escalate plan, self-correct, stronger model via OpenVault, then reconfirm with Yes. Direct abstain only for ungranted tables, PII and destructive SQL. No phrasing lanes. No hand-coded word rules. An AI-call error must not serve a GEN-01 L2 (P0, #430).
- State: #570 is this lane's PR (draft, `8ad10a13`).
- NEXT 1: label #570 tier:full, wait for CI on that exact head, get ONE reviewer of a different model from the writer on that head, then a pre-merge Check on that head (it touches `/ask`).
- NEXT 2: run the armed live 52 (ladder on, a live OpenVault model, no stub) and put correct, wrong, reconfirm, ai and rule counts in the PR. This needs the box stack and the retier, so Lead or a founder-approved run. The count is not measured today. Not PASS.
- Every DMS PR in this lane reports AI-vs-rules counts on the 52.

### Platform (not a lane, but on the list)

- NEXT 1: move the box OpenVault to `1d7595fb`, then retier `box:dms-cortex` at or after 18:20 SGT inside the founder window. Founder or Lead only.
- NEXT 2: put the Studio revtunnel under a supervisor (systemd user unit or a restart loop). Needs the box, so Lead only.

## Blockers and open questions for the founder

1. Claude Code and building. The standing rule was: Grok writers (grok-4.7 high, never fast) build, the Claude credit is for running and testing DMS. The founder's t1846u ask is for Claude Code to continue the work while Netie Lead is down. Until the founder confirms Claude may write product code, CLAUDE_CONTINUE.md limits Claude to: review, run and test, measure, docs, STATE and LOG upkeep, and draft PRs for ordinary tickets. Founder, please say yes or no.
2. File law. DMS CLAUDE.md says five files per repo, no sixth, and no per-agent or per-session files. `handoff/` is a deliberate exception that the founder asked for. If the founder wants it recorded as a decision, that needs a DR number (0007 is unused but IDs are never reused, so Lead picks the number).
3. Prove-host redeploys need the founder GO. Prove pins (DMS `cbf7de87`, Cortex `279cbd85`) do not move without the founder.

## Machine block

<!-- MACHINE:BEGIN -->
Generated 2026-11-27 17:44 SGT by scripts/handoff/update_state.py. Do not hand-edit.

### Netie-AI/dms

main: `2f61927f` COMPILE-CHECK-01: do not serve compile SQL the AI checker rejects (#569)

open PRs: 35 (cap 60)

| PR | draft | head | tier | CI on head | title |
|---|---|---|---|---|---|
| #570 | y | `8ad10a13` | - | no checks | DMS-AI-ABSTAIN-01: climb generative misses before abstaining |
| #563 | y | `05539dbc` | - | no checks | WORD-RULES-OUT-01: rank ontology measures when DMS_NO_WORD_RULES is on |
| #543 | y | `b7059751` | - | 5 ok, 2 FAIL | FDE-ONBOARD-01: one command onboards a customer's files into a Space and drafts  |
| #542 | y | `e08f4664` | - | 3 ok, 1 FAIL | T5 install hardening: #387 TLS host guard (carried) + IPv4 serve; #388 and #377  |
| #539 | y | `79b60ca6` | - | 4 ok | Answer fidelity: FIG-FIDELITY-01 (#508) E14, BIRD-ALLOWLIST-01 (#304) preflight, |
| #538 | y | `3d972da8` | - | 5 ok, 2 FAIL | CONNECT-ASK-05: a join check that raises abstains check_unavailable (no 503, no  |
| #536 | y | `befc5712` | - | 4 ok | ENV-EXAMPLE-SENTINEL: a placeholder left in a secret refuses boot, by name (Refs |
| #535 | y | `7392a270` | - | 4 ok, 3 FAIL | CATALOG-SCHEMA-01: the catalog reads the schemas the grants name (Refs #518) |
| #534 | y | `056908af` | - | 5 ok, 2 FAIL | fix(executor): ONTO-SCOPE-01 the ask reads the asking Space's ontology items, no |
| #533 | y | `e9136d1b` | - | 4 ok, 3 FAIL | ONTO-PROFILE-03: steward review screen for a file Space's proposed keys and link |
| #532 | y | `1fdc548b` | - | 5 ok, 2 FAIL | T1 track, do not merge: epic #178 answer-path P0s (#531, #527, #524, #528, #523, |
| #529 | y | `6c0014d1` | - | 4 ok | JOIN-HOLE-01 (P0): the join allowlist counts every relation a FROM names |
| #522 | y | `70fb535a` | - | 4 ok | CONNECT-ASK-03: a Space with no ontology answers one-table SQL only (unverified_ |
| #521 | y | `a8b9c316` | - | 4 ok | ONTO-PROFILE-01: measure a file Space and propose keys and links (Refs #514) |
| #519 | y | `77c3bbfc` | - | 4 ok | fix(deps): pin duckdb>=1.1,<2 to match the Cortex engine (F-TBD-28) |
| #510 | y | `50185f3f` | - | 4 ok | IPHONE-PWA-01 to 05: installable iPhone app, one-handed use, one-row composer, p |
| #509 | y | `4891318e` | - | 3 ok, 1 FAIL | feat(executor): "how full is each warehouse" serves the certified capacity metri |
| #505 | y | `c5089449` | - | 4 ok | fix(ao): AO-MASK-01 - AO compile prompts carry only masked or non-personal sampl |
| #501 | y | `df17dcc4` | - | 3 ok | feat(bench): Lane 1 model-in-isolation bench on the curated 52, with the product |
| #491 | y | `6054a8ca` | - | no checks | feat(insights): INS-PROOF-03 steward pins - single-use preview token, register m |
| #489 | y | `cfe2523b` | - | no checks | feat(ui): ACT-03 governed actions on AmendPage - receipt never says Applied with |
| #461 | y | `099e1fd8` | - | 2 ok, 1 FAIL | feat(measure-lane): confirmed measures answer at the staged badge, steward route |
| #457 | y | `3d2d6478` | - | 3 ok | fix(chat): ASK-CLARIFY-01 supersede #419: gate the re-ask text, per-request clar |
| #455 | y | `b68d7576` | - | 3 ok | feat(executor): WIRE-01c send dms_payload, read plan+SQL stamps, reconfirm absta |
| #454 | y | `e63cd84f` | tier:full | 3 ok | fix(executor): one grant function, one parser: one read-only SELECT, every table |
| #451 | n | `e0b425dd` | tier:full | no checks | fix(executor): hold L2 until the grant and lake-read grain checks pass on every  |
| #444 | y | `e246cbba` | tier:full | 3 ok | INTENT-SPEC-01: check writer SQL against a second-route spec (supersedes #409) |
| #443 | y | `77ea7630` | tier:full | 3 ok | fix(ask): keep the labelled ranking compile, with one refusal for every ladder r |
| #435 | y | `3902002a` | tier:full | 3 ok | GOLD-LEAK-01: stop serving answers from the scoring fixture |
| #431 | y | `82ddf161` | - | 3 ok | AI-LADDER-PLAN-01: plan, then one higher OpenVault tier |
| #422 | y | `af271892` | tier:full | 3 ok | GRANT-KEY-01: key grants by source + table |
| #398 | y | `81090c5a` | tier:fast | 3 ok | ASK-GUIDE-01: guided path for vague abstains behind DMS_ASK_CLARIFY |
| #397 | y | `305cd768` | tier:full | 3 ok | RUNID-SURFACE-01: surface SQL submit run_id on ask envelopes |
| #379 | y | `5b79e33a` | tier:full | 3 ok | STUDIO-SELECT-01 (D): Studio table/column selection -> Cortex /ask payload (Refs |
| #378 | y | `5c1109e1` | tier:full | 2 ok, 1 FAIL | CONNECT-01: read-only connector layer via one interface (Refs #366) |

### Netie-AI/Cortex

main: `30463b00` FreeRoute: drop the urllib lock, send OpenVault role behind a flag (#490)

open PRs: 11 (cap 60)

| PR | draft | head | tier | CI on head | title |
|---|---|---|---|---|---|
| #488 | n | `4327dcc5` | - | 5 ok, 1 FAIL | chore(deps): bump duckdb from 1.5.5 to 2.0.1 |
| #487 | n | `71a645bc` | - | 6 ok | chore(deps-dev): bump @babel/runtime from 8.0.8 to 8.1.3 in /demo/dms-ui |
| #486 | n | `f365a522` | - | 6 ok | chore(deps-dev): bump @babel/core from 8.0.9 to 8.1.3 in /demo/dms-ui |
| #481 | y | `0bc6974b` | - | 5 ok, 1 FAIL | Track T4: Cortex accuracy and gates (#408 + JOIN-GRANT-ZW, #465, #426 partial, # |
| #480 | y | `22c6a95f` | - | 5 ok, 1 FAIL | T2 Cortex security: gate ungated routes (#263), workflow reads (#368), redact ro |
| #479 | y | `d4a62b63` | - | 5 ok, 1 FAIL | JOIN-GRANT-SUBQUERY-01: the join grant decides every table a statement reads (#4 |
| #476 | n | `06a86599` | - | 5 ok, 1 FAIL | chore(deps-dev): bump @playwright/test from 1.64.1 to 1.65.1 in /demo/dms-ui |
| #475 | n | `9e8e0475` | - | 5 ok, 1 FAIL | chore(deps): bump next from 16.4.3 to 16.4.6 in /demo/dms-ui |
| #472 | y | `b9f1f939` | - | 5 ok, 1 FAIL | docs(findings): DMS iPhone PWA, fixed phone button covered the demo banner |
| #471 | n | `741b408c` | - | 6 ok | chore(deps): bump setuptools from 83.0.0 to 84.2.1 |
| #393 | y | `8163389f` | - | 5 ok, 1 FAIL | chore(deps): bump multidict from 6.9.1 to 7.1.0 |

### Netie-AI/OpenVault

main: `1d7595fb` fix(freeroute): add service tier and store limiter limited_by (#194)

open PRs: 6 (cap 60)

| PR | draft | head | tier | CI on head | title |
|---|---|---|---|---|---|
| #174 | y | `b2159830` | tier:full | 9 ok | fix(vault): refuse an app grant whose label cannot be issued |
| #173 | y | `24f495a5` | - | 9 ok | feat(freeroute): wire the Anthropic adapter as an opt-in tier (F110) |
| #171 | y | `c5619981` | - | 3 ok, 1 FAIL | DRAFT (founder decision): superapp planner |
| #170 | y | `371c4311` | tier:full | 9 ok | OV: site passwords and TOTP seeds are secrets, not LLM hops (F38, F39) |
| #169 | y | `b842fbcb` | - | 8 ok, 1 FAIL | feat(vault): Anthropic Messages adapter module (unwired, opt-in tier, F110) |
| #99 | y | `0bc13acb` | tier:full | 9 ok | Fork OmniRoute and Openship into OpenVault as FreeRoute and FreeBuild (DR-0018) |

<!-- MACHINE:END -->
