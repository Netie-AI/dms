# Slim inventory (2026-11-28)

DMS is a thin harness: Cortex workflow, OpenVault routing, and the harness itself (context, plan, generate SQL, execute, check, retry or escalate, governance, visible proof). This file is the inventory for that slim. Decisions are ARCHIVE, KEEP, or KEEP-CORE.

ARCHIVE means `git mv` into `attic/`. Nothing was hard-deleted. Source SHA at the move: `fa0eeef7026e5c09c2ebffd7b573087bb41fc700`. See `attic/README.md`.

Rule paths that still serve answers are KEEP. They sit behind `DMS_RULES_LEGACY`. Unset, or any token other than the digit `0`, keeps today's serves. A typo stays on. Digit `0` is the pure-AI switch: those lanes do not submit and do not serve. Grants, abstain, ontology, and a model SQL leg stay. `DMS_AI_FIRST` is not redefined.

## What the rule paths serve

Exec stub, `grade52 --main`, mode=stub-exec. Same line on `fa0eeef` and after rebase onto `5b8da59`.

| Switch | correct | wrong | reconfirm | ai | rule | of 52 |
|--------|---------|-------|-----------|----|------|-------|
| unset (default) | 43 | 0 | 5 | 0 | 43 | L1=23, compile=20 |
| `DMS_RULES_LEGACY=0` | 0 | 0 | 34 | 0 | 0 | rule serves 0; abstain=10, refusal_ok=8, clarify=0 |

43 of 52 are rule-served today. The other 9 are abstain parts on the stub (reconfirm=5, refusal_ok=4). Digit `0` drops rule-served to 0 of 52 and marks none wrong. There is no model on this stub, so correct falls to 0. That is the switch, not a deleted lane.

`DMS_RULES_OFF=1` still holds the word and compile lanes and still leaves L1 serving. `DMS_RULES_LEGACY=0` holds L1 as well, without turning the rest of the AI-first profile on.

## Line count and cold start

Live tree versus origin/main `5b8da59` (#656). Excludes `attic/`, `.git`, `.venv`, `node_modules`, `__pycache__`, `dist`, and `generated`. Count is files and newline count. The delta matches the earlier count against `fa0eeef`: -3 product files, -787 product lines, -120 docs files.

| Set | Before (files / lines) | After (files / lines) |
|-----|------------------------|------------------------|
| product py (`apps` `packages` `scripts`) | 312 / 136885 | 309 / 136098 |
| product py+ts+tsx | 436 / 155359 | 433 / 154572 |
| docs md | 196 / 7319 | 76 / 4339 |
| text (py ts tsx md yml) | 1026 / 307206 | 905 / 303631 |

Gitignored `playground/data/` is not in the count. `attic/` is not in the after column. The after column includes this file.

Cold start is a fresh process importing `dms_api.app`, `DATABASE_URL` unset, three runs. Against `5b8da59`. An earlier trio against `fa0eeef` was 1.0635, 0.7270, 0.6944 before and 0.7443, 0.7023, 0.6928 after.

| | seconds |
|--|---------|
| before `5b8da59` | 0.8219, 0.7586, 0.7671 |
| after this tree | 0.7479, 0.7585, 0.7399 |

The steady imports sit in the same band. The first-import gap is process noise, not a harness change. Docs moved out of the import path.

## ARCHIVE

| Path | What it does | Who imports it | Why |
|------|----------------|----------------|-----|
| `attic/scripts/repro_capped_parent_orphans.py` | One-off repro: a `max_rows` cap invents orphans `verify()` used to miss. | Nobody. Historical cite in CHANGELOG only. | The product check is in tests. |
| `attic/scripts/repro_refused_badge.py` | F40 repro for a refused badge mapped to abstain. | Nobody. Comment in `__init__.py` points at the attic path. | Customer path is already `map_ask_response_to_envelope`. |
| `attic/scripts/stress_dms_http.py` | In-process HTTP stress. Sets `DMS_DEMO_FALLBACK=1`. | Nobody. | A fallback-on stress script is not a gate. |
| `attic/scripts/try_changes.py` | Narration harness for an old recovery wave. | Nobody. Cited from `docs/archive` only. | pytest already asserts those claims. |
| `attic/docs/subagents_findings/` (121 files, dated before 2026-11-01) | Session findings. 103 are named from the index. 18 had no index row (listed in `attic/README.md`). | Nobody. The index is a table of paths, not an import. | Doc sprawl. November rows stay put because open writers append them. |

## KEEP (still serves, or still a gate)

| Path | What it does | Who imports it | Why |
|------|----------------|----------------|-----|
| `packages/executor/dms_executor/rules_legacy.py` | `DMS_RULES_LEGACY`. `rules_off_lane` and `hold_rule_serve`. | `__init__.py`, `generative_ask.py`, `bronze_sheet_ask.py`, `measure_link.py`. `demo_ask`, `demo_pack`, and `keyword_routes` call `rules_off_lane`. | The one pure-AI switch. Default is current behaviour. |
| `packages/executor/dms_executor/demo_pack.py` | L1 phrase pack. | `__init__.py` (`maybe_pack_ask`), `grade52.py`, `generative_ask.py`. | Serves 23 of 52. Held when the switch is `0`. Not removed. |
| `packages/executor/dms_executor/generative_ask.py` | Ontology compile, then model SQL. | `__init__.py` `live_ask`, and the ask helpers (`ask_ladder`, `scale_miss`, `verified_queries`, and others). | Compile serves 20 of 52. Submit of compile and ladder is held when `hold_rule_serve` is true. READY-01 also edits this file. This change is the serve predicate only. |
| `packages/executor/dms_executor/keyword_routes.py` | Question-word measure lock. | `generative_ask.py`, `semantic_retrieve.py`, `scale_miss.py`, rule tests. | Word lane. `_rules_off` follows the switch. |
| `packages/executor/dms_executor/demo_ask.py` | Regex router for demo questions. | `__init__.py`, `demo_pack.py`, `generative_ask.py`, `verified_queries.py`. | Word lane. A rules-off envelope is returned, and a Cortex client still goes to `live_ask`. |
| `packages/executor/dms_executor/bronze_sheet_ask.py` | Sheet phrase lane. | `__init__.py`. | Same hold, via `rules_off_lane`. |
| `packages/executor/dms_executor/measure_link.py` | Measure ranker. Keyword lock. | `generative_ask.py`, `ontology.py`, `semantic_retrieve.py`. | `no_word_rules_enabled` is true when the switch is `0`, so the keyword lock drops with it. |
| `packages/executor/dms_executor/semantic_retrieve.py` | Retrieval. One keyword lock calls `no_word_rules_enabled`. | Ask path. | Not edited. It inherits the switch. Left in place because retrieval is also an AI-first surface. |
| `packages/executor/dms_executor/ai_first.py` | AI-first profile. `ai_first_enabled`. | `__init__.py`, `generative_ask.py`, `arbiter.py`, `rules_legacy.py`. | KEEP. Not redefined. READY-01 owns the profile. |
| `packages/executor/dms_executor/verified_queries.py` | Steward replay of a verified query. | Ask path. | KEEP. Governance replay, not a phrase lane. Digit `0` still showed rule=0 on the 52, so this path is not serving that pack. |
| `packages/executor/dms_executor/abstain.py` | `build_abstain` and the abstain envelope. | Ask path. | KEEP-CORE in behaviour. Listed here so the abstain builder is not read as a phrase rule. Direct abstain for ungranted, PII, and destructive SQL stays. |
| `scripts/verify/grade52.py` | Grades the 52. CI self-test. | Tests and the verify CLI. | KEEP. A scorer, not a second product path. |
| `scripts/score_answers.py` | `--oracle-only` hostile score. CI. | CLI. | KEEP. Different gate from grade52. |
| `scripts/score_bird.py` | `--self-check`. CI. | CLI. | KEEP. Different gate. |
| `scripts/verify/word_rule_allowlist.txt` | Ceiling of word-rule sites. Keys may only shrink. | `scripts/verify/rule_inventory.py`, `tests/test_no_word_rules_ast.py`. | KEEP. Line numbers moved when `import os` left three modules. Keys did not grow. |
| `validate_compiled_sql` (in the executor) | Compiled-SQL shape check. | Compile submit. | KEEP. Not a duplicate of the gates below. It checks a different claim. |
| `ai_lane_sql_check` | Model-SQL check on the AI lane. | AI lane. | KEEP. Different claim from compile validation. |
| `space_ontology.check_sql_against_space` | SQL must match the Space ontology. | Ask submit. | KEEP. Ontology gate. |
| `semantic_import.validate.check_sql` | Import-time SQL check. | Semantic import. | KEEP. Import gate, not the ask gate. |
| `grant_struct.serve_gap` | Grant gap before a serve. | Ask path. | KEEP-CORE grants. Listed so it is not folded into a "duplicate checker" delete. |
| `scripts/show_lineage.py` | Pipeline proof CLI. | CLI. | KEEP. Proof, not a dead script. |
| `docs/archive/` | Retired text linked from `CLAUDE.md` and `AGENTS.md`. | Docs links. | KEEP. Already an archive. Moving it breaks those links. |
| `docs/POWERBI_DUCKLAKE.md` | Viz note. | `viz_bakeoff`, README, PARKING_LOT. | KEEP. Linked. |
| `docs/EXCEL_COPILOT_GOVERNED_PROMPT.md` | Governed copilot prompt. | `apps/ui` `copilotPrompts.ts`. | KEEP. Product text. |
| `docs/AUTONOMY_ROADMAP.md` | Roadmap. | `SOFTWARE_DESIGN.md`. | KEEP. Linked. |
| `docs/L2_MODEL_BAKEOFF.md` and the json beside it | Bakeoff record. | `bakeoff_l2_models.py` writes them. `tests/test_bakeoff_l2_models.py` reads them. | KEEP. A test fixture in docs clothing. |
| `DMS_API_URL`, `DMS_ARCH_AMENDMENT`, `DMS_ARCH_AMENDMENT_2026`, `DMS_EXPORT_DIR`, `DMS_JWT_SECRET`, `DMS_TECHNICAL_ARCHITECTURE` | Names that appear in docs. No Python reader. | None. | KEEP as historical names. No product module to delete. Removing them from old docs is a rewrite, not a slim. |
| `DMS_ASK_CLARIFY` | Popped by `grade52` and capture scripts. No product reader. Product flag is `DMS_AI_CLARIFY`. | Test harness only. | KEEP. The pop keeps the stub profile stable. |

No unused product module under `apps/` or `packages/` had zero references. The slim that is safe today is the doc pile and four scripts, plus the switch.

## KEEP-CORE (not archived)

| Path | What it does | Who imports it | Why |
|------|----------------|----------------|-----|
| `packages/executor/dms_executor/db_connector.py`, `apps/api` connect routes | Connectors. | Ingest and Studio. | KEEP-CORE. A source connection is the harness, not a phrase rule. |
| `grant_struct.py`, `acl.py`, `demo_grants.py` | Grants and permissions. | Ask and Studio. | KEEP-CORE. |
| Audit and compliance call-through (`compliance_gate` to Cortex) | Governance. | Mutation routes. | KEEP-CORE. One ledger, via Cortex HTTP. |
| `insight_proof.py`, `route_stamp.py`, SQL / rows / check on the envelope | Proof and explain. | Ask response. | KEEP-CORE. Visible proof stays. |
| Ontology modules (`ontology.py`, `space_ontology.py`) | The Space ontology. | Compile and grants. | KEEP-CORE. A SQL-source Space answers through its own ontology. |
| Studio (`apps/ui` Studio, studio routes) | Ingest and confirm UX. | UI. | KEEP-CORE. Also an open writer (READY-04, create). Not moved. |
| Auth, session, `middleware_actor.py` | Sign-in. | API. | KEEP-CORE. READY-02 is editing these. Not moved. |
| `SERVING_DIALECT = "duckdb"` in `demo_warehouse.py` | Serving dialect pin. | SQL parse and compile. | KEEP-CORE. |
| `build_abstain` in `abstain.py` | Abstain builder. | Ask path. | KEEP-CORE. |
| `.importlinter`, `tests/invariants/`, `packages/cortex_client/` | Import contracts and the Cortex HTTP client. Contract pin is the vendored wheel. | CI and the API. | KEEP-CORE. This change does not touch them. |
| OpenVault client (`freeroute`, manifest minter, `packages/ao_vendor` `ov_client`) | Routing and mint. DMS does not hold provider keys. | Ask and compile. | KEEP-CORE. |

## Not moved (open writers)

These paths match the work the user named. They stay where they are. This branch edits two of them, and only the serve predicate.

| Writer | Paths left in place | What this branch did |
|--------|---------------------|----------------------|
| READY-01 AI-first SQL | `ai_first.py`, `ask_ladder.py`, `generative_ask.py`, `compute.py`, `insights.py`, `docs/plans/AI_SQL_SHIFT.md`, ask path in `__init__.py` | `generative_ask._submit_validated` and the L1 hold in `__init__.py` call `hold_rule_serve`. `ai_first_enabled` is unchanged. |
| READY-04 onboarding | tenant, ingest, Studio sources, `scripts/fde`, `scripts/ops/provision_tenant.py`, `docs/fde`, `ready_flow.py` | Not edited. |
| READY-02 auth | auth, session, admin, `middleware_actor.py`, `mcp_server/auth.py`, UI sign-in | Not edited. |
| Create app / doc / slides | `apps/ui`, `scripts/brief.py`, Studio chat / doc / slides | Not edited. The copilot prompt doc stays because the UI imports it. |

`STATUS.md` was not edited. It is a live <=60 line board and other writers update it. `CHANGELOG.md` and `docs/subagents_findings/INDEX.md` got one new row each. November finding files were not moved.
