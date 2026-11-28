# handoff/STATE.md

Netie Lead and Claude Code share this file. Read RULES.md and LESSONS.md first, then this file, then CLAUDE_CONTINUE.md.
The block between the MACHINE markers is generated: `python scripts/handoff/update_state.py`. Run it before you write.
Everything else is hand-kept. Update it in the same PR as the work. No secret values anywhere in this file.

**Hand part last updated:** 2026-11-27 17:51 SGT by lead (founder decisions on t1846u, plus t1847u-t1850u facts)

## Goal

DMS is a fully AI-driven alternative to DB-GPT, Cortex Analyst and Genie. WRONG=0, and reconfirm-with-Yes instead of abstaining.
Ship DMS, Cortex and the existing apps end to end. The 52-question pack in `scripts/verify/` is the number of record.
Fix every known defect until the product is usable and correct for real users, then connect it to AirGPT for skills, agents and slides.
Palantir-style solutions and the three-agent app generator stay deferred until there are clients.
The Constructor drag-and-drop canvas already exists: reuse and improve it. Do not build a second graph.

## Mains (hand-kept pointers; the machine block below is the live truth)

| Repo | Merged today (SGT) | Squash |
|------|--------------------|--------|
| OpenVault | #193 fix(freeroute): skip a per-hop 402 and try the next hop | `729be72a` |
| OpenVault | #194 fix(freeroute): service tier and persist limiter limited_by (merged 17:39 SGT) | `1d7595fb` |
| Cortex | #490 FreeRoute: drop the urllib lock, send OpenVault role behind a flag | `30463b00` |
| DMS | #569 COMPILE-CHECK-01: do not serve compile SQL the AI checker rejects | `2f61927f` |

OpenVault main `1d7595fb` is #194 and is live on the box (verified: process SHA, see box stack). DMS main is `2f61927f`.

## Box stack (Netie Lead's box, as of 2026-11-27 17:51 SGT, verify live before relying)

| Part | Port | Notes |
|------|------|-------|
| Postgres 17 | 5432 | |
| OpenVault | 5000 | live at `1d7595fb` (worker 2022340, started 17:44 SGT). Starts sealed after a restart: unseal with `ovctl.py unseal`. Started via `/home/box/netie-run/bin/ov-api.sh` with the Claude role policy file (`route_policy.claude.json`, local, backed up in `/home/box/netie-run/backups/`) |
| Cortex | 8010, 8011 | live at `30463b00` with `CORTEX_CALL_DEADLINE=1` and `CORTEX_FREEROUTE_ROLE=1` |
| DMS | 8090 | at `497d1bf`. DMS main is `2f61927f` |
| nginx | 3080 | basic auth |

Studio hosting: the netie-studio VM runs cloudflared under systemd. The box reaches it through a reverse SSH link
(`/workspace/studio-host/revtunnel.sh`) to box nginx :3080. The revtunnel is UNSUPERVISED: if the box restarts, studio.netie.ai goes dark until someone restarts the script. A keepalive is in flight after a one-minute drop at 17:37 SGT. The VM firewall allows only port 22.

## OV routing facts

- Role policy starts every role on Anthropic: Sonnet for sql, tool, summarize; Opus for reason, grain, ontology. Fallback order: groq, openrouter, google, nvidia. Cortex sends `model = chosen.requested`; OpenVault ignores it on a role walk.
- The 429s came from the TokenBudgetLimiter, not a provider. The `box:dms-cortex` key was on the free tier (20 rpm, 40k tpm). It rejects in `app.py` before any hop, which is why `hop_attempts` shows no 429. The per-hop 402 fall-through (#193) does not cover this case. hp2-569 recorded 0 limiter 429s after the box moved to #194. Earlier parallel runs had 340 limiter 429s.
- OpenVault [#194](https://github.com/Netie-AI/OpenVault/pull/194) MERGED `1d7595fb` and is live on box :5000. A test call from the DMS key was answered by Claude.
- Retier of the `box:dms-cortex` key to the service tier is set for 18:21 SGT today unless the founder objects. It is a key and limit change: the founder 1-hour objection window applies, and Netie Lead never raises limits on its own.

## hp2-569 measured results (not PASS)

Source: `/workspace/dms-demo-20261127/par/summary-hp2-569.md`. The summary header says the run used DMS `04ade63` and a Cortex eval hot-patch, not Cortex main. All flags off, Claude serving. 0 limiter 429s. Run cost $2.89 in Claude. Not a live score of current main alone. Not PASS.

| Arm | n | correct | WRONG | AI | rule |
|-----|---|--------:|------:|---:|-----:|
| off-c52 | 52 | 41 | 1 | 14 | 19 |
| off-packH | 32 | 13 | 2 | 12 | 3 |

Known faults from that run:

- Compile lane serves wrong-grain SQL on `h_stock_value_by_location` and `h_below_reorder_all` (WRONG:ontology_compile, 2).
- AI `cq_sales_top3_volume` has no ORDER BY (WRONG:model, 1).
- Studio ask timed out at `DMS_INSIGHTS_TIMEOUT_S=8` (Cortex generate is about 14s). Timeout raise to 60 is in flight.
- Two rule answers were still wrong even though the AI had better SQL next to them. Direction: when rule SQL and AI SQL disagree, the AI wins after a shape check that the answer fits the question. The AI also gets a self-check for missing ORDER BY / LIMIT on a top-N ask.

## Done today (2026-11-27 SGT)

- OpenVault [#193](https://github.com/Netie-AI/OpenVault/pull/193) merged `729be72a`
- OpenVault [#194](https://github.com/Netie-AI/OpenVault/pull/194) merged `1d7595fb` (17:39 SGT) and is live on box :5000 (worker 2022340, 17:44 SGT)
- Cortex [#490](https://github.com/Netie-AI/Cortex/pull/490) merged `30463b00` and is live on :8010/:8011
- DMS [#569](https://github.com/Netie-AI/dms/pull/569) merged `2f61927f`
- DMS [#567](https://github.com/Netie-AI/dms/pull/567) ADJUST-SQL-VQ-01 and [#565](https://github.com/Netie-AI/dms/pull/565) UNICODE-IDENT-01 are the two merges before it on main
- Founder decisions recorded: Claude Code may write product code while Lead is down (RULES, CLAUDE_CONTINUE); `handoff/` is DR-0007; LESSONS.md seeded from today's failures

## In flight

Live list: the machine block. Hand notes only:

- DMS [#570](https://github.com/Netie-AI/dms/pull/570) DMS-AI-ABSTAIN-01, draft, published head still `8ad10a13` as of 17:51 SGT. Flag `DMS_ASK_LADDER`, default off. Touches `/ask`, so tier:full. A reviewer found a banned phrase regex for vague questions and a clash with #569. The writer was sent back to rebase onto #569 and replace the phrase rule with an AI clarify step (ask back with 1 to 3 interpretations). That new head is not up yet. Stub counts in the current body are not a live 52. Not PASS.
- DMS [#571](https://github.com/Netie-AI/dms/pull/571) this handoff, draft, `tier:fast`.
- Retier of `box:dms-cortex` to the service tier at 18:21 SGT unless the founder objects (Lead or founder only).
- `DMS_INSIGHTS_TIMEOUT_S` raise 8 to 60, and Cortex switched to the DMS service token file (no token in this file). Then re-test Studio with real questions. Lead committed a working Studio demo and a step-by-step guide tonight, by 21:00 SGT, honest about remaining wrongs.
- Studio revtunnel keepalive, after the 17:37 SGT drop.
- Rebuild plan and defect list landing in `/workspace/dms-rebuild-20261127` (tickets dir still empty at 17:51 SGT). Copy into `handoff/` later. Do not invent tickets from it until files exist.
- DMS has many long-lived draft PRs (see the machine block). Do not close, rebase or delete any of them without the founder.

## Next steps per lane

Claude Code takes the top NEXT item of a lane only when it is concrete, or after the founder names the item in an issue.

### NL-13 Cortex / A-B

- State: box Cortex :8010/:8011 are on `30463b00` with `CORTEX_CALL_DEADLINE=1` and `CORTEX_FREEROUTE_ROLE=1`.
- NEXT 1: after the Insights timeout raise and the service-token switch, smoke Studio asks against this Cortex and record live SHA, latency and outcome. A merge is not a smoke.
- NEXT 2: keep the Cortex prove pin `279cbd85` where it is. No pin move without the founder.

### NL-14 OpenVault

- State: #194 merged `1d7595fb` and is live on box :5000 (worker 2022340, started 17:44 SGT).
- NEXT 1: retier the `box:dms-cortex` key to the service tier at 18:21 SGT unless the founder objects. Key and limit change: 1-hour objection window. Lead or founder only. After the retier, confirm a test call and that limiter 429s stay at 0. Never print the key.
- NEXT 2: leave prove-host OpenVault redeploys for the founder GO.

### NL-17 handoff

- State: this PR, dms#571, branch `handoff/lead-claude-sync`. DR-0007, RULES, STATE, CLAUDE_CONTINUE, LOG, LESSONS.
- NEXT 1: after merge, start the 90 percent usage routine and the GitHub-watch routine (wording is in the #571 body; nothing is created until the founder or Lead adopts them).
- NEXT 2: copy the rebuild plan from `/workspace/dms-rebuild-20261127` into `handoff/` once it exists as markdown. Do not copy constructor source or secrets.

### NL-18 DMS rebuild

- Standing bar (t1847u, t1848u, t1849u, t1850u): usable, correct for real users first. Understand ordinary language, ask back when a question is vague, check data access on every question, AI-written SQL that is checked, every button works, one-click connectors, Constructor canvas reused for the ontology graph (not a second drag-and-drop). Compete with Databricks Genie and Snowflake Cortex Analyst on a trusted semantic model, clarifying questions, verified-query retrieval, and a public score. AirGPT skills, agents and slides come after the defect list passes. Slides get a design review before they ship. Lead will send a Studio demo and guide by 21:00 SGT tonight, honest about remaining wrongs.
- #570 is the abstain-ladder and AI-clarify PR (see In flight).
- NEXT 1: land the timeout raise and the Cortex service-token switch, then re-test Studio with real questions and write the 21:00 SGT demo guide (Lead on the box).
- NEXT 2: #570 rebase: remove the phrase regex, add the AI clarify step, rebase onto #569, label tier:full, wait for CI on the new exact head, one different-model reviewer, then a pre-merge Check (it touches `/ask`).
- NEXT 3: when rule SQL and AI SQL disagree, the AI wins after a shape check; add an AI self-check for missing ORDER BY / LIMIT on a top-N ask (`cq_sales_top3_volume`).
- NEXT 4: stop the compile lane serving wrong-grain SQL on `h_stock_value_by_location` and `h_below_reorder_all`.
- Every DMS PR in this lane reports AI-vs-rules counts on the 52. Not PASS until an armed live run says so.

## Blockers and standing limits

1. Claude Code and building: decided (t1846u). Claude may write product code and open PRs when Lead is down or out of quota, following RULES.md. Claude may merge only `tier:fast` or docs PRs on green CI with a different-model AGREE. Claude never merges `tier:full`, including its own.
2. File law: decided (t1846u). `handoff/` is DR-0007.
3. Prove-host redeploys need the founder GO. Prove pins (DMS `cbf7de87`, Cortex `279cbd85`) do not move without the founder.
4. The 18:21 SGT service-tier retier is inside the founder objection window. Do not raise the limit early and do not print the key.

## Machine block

<!-- MACHINE:BEGIN -->
Generated 2026-11-29 00:03 SGT by scripts/handoff/update_state.py. Do not hand-edit.

### Netie-AI/dms

main: `5d9b383d` T2 FDE-SURVEY-CONNECT-01: site survey and connector setup tests (#678)

open PRs: 47 (cap 60)

| PR | draft | head | tier | CI on head | title |
|---|---|---|---|---|---|
| #672 | y | `0c177d5b` | - | 12 ok | SCIM-01: SCIM 2.0 user and group provisioning behind DMS_SCIM |
| #671 | y | `c5820225` | - | 12 ok | DMS-GOV-01: manager governance console behind DMS_GOV_CONSOLE |
| #668 | y | `568d0ee9` | - | 12 ok | Generality harness: one scoreboard, recorded slice in CI |
| #665 | y | `6e87464b` | - | no checks | Accuracy levers on the AI floor, flags off |
| #663 | y | `c0b52fed` | - | 16 ok | HARNESS-SEC-01: gate provider keys, SQL, and demo bearers |
| #662 | y | `b94e6140` | - | no checks | BIG-SCHEMA-01: never send an empty schema |
| #661 | y | `dab5f750` | - | 12 ok | Create doc and slides through the shared provider adapter |
| #660 | y | `70173c66` | - | no checks | READY-07: AI sell profile for Studio ask |
| #658 | y | `32f67dd2` | - | 12 ok | DMS-CREATE-01: harness for Claude and Cursor |
| #628 | y | `1c1de82c` | - | no checks | NL19-NO-SQL-GENERATE: retry one empty generate, then ask back |
| #621 | y | `fec45062` | - | no checks | NL19-EXPLAIN-REPAIR: trace binder failures and repair within the call budget |
| #616 | y | `d85a4171` | - | no checks | UX-CLARIFY-CARD-01: early clarify card before the SQL loop |
| #613 | y | `ee1dd4c0` | - | no checks | FDE-ONTO-LINKS-01: propose undeclared join links for steward confirmation |
| #612 | y | `73da9eac` | - | 15 ok | FDE-EVAL-PACK-01: steward definitions, tie-aware gold, raw-LLM control |
| #607 | y | `be67fa52` | - | no checks | FEAT-METRIC-CUBE-01: Governed metric cubes the model plans against |
| #606 | y | `408d33e8` | - | no checks | UX-GRANT-DATA-01: ask back when a measure is outside the Space grant |
| #605 | y | `cd620c54` | - | no checks | UX-CHART-01: one sentence and a chart of the served rows |
| #604 | y | `b9c36920` | - | no checks | UX-FEEDBACK-01: Yes, Fix it, and Request review |
| #603 | y | `a4c02ce9` | - | 11 ok | FEAT-PIPELINE-STEP-01: Python SDK, CLI, Airflow operators, dbt recipe, signed we |
| #601 | y | `f737c265` | - | no checks | UX-SIGNIN-01: in-app sign-in behind DMS_APP_LOGIN (default off) |
| #596 | y | `8e548ab6` | - | 11 ok | FEAT-VALUE-MATCH-01: entity and value matching for misspelled filter values |
| #581 | n | `28e1e059` | tier:fast | 12 ok | handoff: state refresh |
| #563 | y | `05539dbc` | - | no checks | WORD-RULES-OUT-01: rank ontology measures when DMS_NO_WORD_RULES is on |
| #543 | y | `b7059751` | - | 5 ok, 2 FAIL | FDE-ONBOARD-01: one command onboards a customer's files into a Space and drafts  |
| #542 | y | `6195feb3` | - | 11 ok | T5 install hardening: #388 env sentinel, #387 IPv4 + numeric-host guard, #377 at |
| #539 | y | `0b292401` | - | no checks | Answer fidelity: FIG-FIDELITY-01 (#508) E14, BIRD-ALLOWLIST-01 (#304) preflight, |
| #536 | y | `befc5712` | - | 4 ok | ENV-EXAMPLE-SENTINEL: a placeholder left in a secret refuses boot, by name (Refs |
| #532 | y | `1fdc548b` | - | 5 ok, 2 FAIL | T1 track, do not merge: epic #178 answer-path P0s (#531, #527, #524, #528, #523, |
| #510 | y | `10f9761a` | - | 10 ok | IPHONE-PWA-01 to 05: installable iPhone app, one-handed use, one-row composer, p |
| #509 | y | `4891318e` | - | 3 ok, 1 FAIL | feat(executor): "how full is each warehouse" serves the certified capacity metri |
| #505 | y | `c5089449` | - | 4 ok | fix(ao): AO-MASK-01 - AO compile prompts carry only masked or non-personal sampl |
| #501 | y | `df17dcc4` | - | 3 ok | feat(bench): Lane 1 model-in-isolation bench on the curated 52, with the product |
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

main: `7d6945b4` FDE-CORTEX-SPACE-CATALOG-01: chooser reads the Space catalog (#494)

open PRs: 9 (cap 60)

| PR | draft | head | tier | CI on head | title |
|---|---|---|---|---|---|
| #488 | n | `4327dcc5` | - | 5 ok, 1 FAIL | chore(deps): bump duckdb from 1.5.5 to 2.0.1 |
| #487 | n | `71a645bc` | - | 6 ok | chore(deps-dev): bump @babel/runtime from 8.0.8 to 8.1.3 in /demo/dms-ui |
| #486 | n | `f365a522` | - | 6 ok | chore(deps-dev): bump @babel/core from 8.0.9 to 8.1.3 in /demo/dms-ui |
| #480 | y | `3e526b26` | - | 6 ok | T2 Cortex security: gate ungated routes (#263), workflow reads (#368), redact ro |
| #476 | n | `06a86599` | - | 5 ok, 1 FAIL | chore(deps-dev): bump @playwright/test from 1.64.1 to 1.65.1 in /demo/dms-ui |
| #475 | n | `9e8e0475` | - | 5 ok, 1 FAIL | chore(deps): bump next from 16.4.3 to 16.4.6 in /demo/dms-ui |
| #472 | y | `b9f1f939` | - | 5 ok, 1 FAIL | docs(findings): DMS iPhone PWA, fixed phone button covered the demo banner |
| #471 | n | `741b408c` | - | 6 ok | chore(deps): bump setuptools from 83.0.0 to 84.2.1 |
| #393 | y | `8163389f` | - | 5 ok, 1 FAIL | chore(deps): bump multidict from 6.9.1 to 7.1.0 |

### Netie-AI/OpenVault

main: `ceab7c77` fix(freeroute): fail closed on uncapped paid hops and stamp downgrades (#198)

open PRs: 7 (cap 60)

| PR | draft | head | tier | CI on head | title |
|---|---|---|---|---|---|
| #199 | y | `e85cd980` | - | no checks | feat(vault): tenant seats behind OV_SEAT_PLANE |
| #196 | y | `3f333218` | tier:full | 11 ok | feat(guard): require credential on every GET outside an allowlist (READY-02) |
| #174 | y | `b2159830` | tier:full | 9 ok | fix(vault): refuse an app grant whose label cannot be issued |
| #173 | y | `24f495a5` | - | 9 ok | feat(freeroute): wire the Anthropic adapter as an opt-in tier (F110) |
| #171 | y | `c5619981` | - | 3 ok, 1 FAIL | DRAFT (founder decision): superapp planner |
| #170 | y | `371c4311` | tier:full | 9 ok | OV: site passwords and TOTP seeds are secrets, not LLM hops (F38, F39) |
| #99 | y | `0bc13acb` | tier:full | 9 ok | Fork OmniRoute and Openship into OpenVault as FreeRoute and FreeBuild (DR-0018) |

<!-- MACHINE:END -->
