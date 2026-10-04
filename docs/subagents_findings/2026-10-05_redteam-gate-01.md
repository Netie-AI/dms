# DMS gate red team (Plan C): findings for the PRD

**Status:** feedback for DMS PRD. Not a ticket, not a fix. **Local run, unverified** until Verify reruns it on the merge commit.  
**Date:** 2026-10-05 (run 2026-10-02, 14:57 local, host clock Asia/Kuala_Lumpur)  
**Code under test:** `origin/main` f0e6c61 plus the harness commit 75d1130 (branch `claude/redteam-gate-01`). Since then main gained two commits (PIN-STRIP-01 #334, a bronze-grant test #335); the 45 proposed tests below were rerun on main 7a8d6c1 + harness and behaved as stated. The full 338-case sweep was not rerun on 7a8d6c1.  
**Scope:** the DMS side of the answer gate with Cortex and the model **stubbed**. The model SQL is **injected** (written by attackers as SQL a model could plausibly emit; never observed from a model). Cortex-side enforcement, the F5 compliance gate, the typed-plan path and any real model are **not exercised**. No live lane was run.

## 1. Headline

- **338 adversarial cases**, 5 families, one frozen snapshot (corpus copied to `tests/redteam/corpus/`).
- Mechanical grader: **231 WRONG**, 37 ABSTAIN, 70 CORRECT. Every WRONG carried a confident badge.
- Three blind judges agreed on all but 2 cases (336 unanimous); judge majority: 228 WRONG. A **separate verifier re-derived the correct answer blind from the data** for all 231 suspected wrong answers: **207 confirmed wrong, 19 ambiguous, 5 not confirmed**.
- **Floor on the confirmed count.** 73 of the 207 involve a case a verifier listed as a judgment call a reasonable person could dispute (for most, the verifier said its verdict held under every alternative reading it checked, but that is its own claim). Without them the floor is **134**; also dropping the 5 that depend on today's date, **129**. Quote the floor, not 207, until Verify reruns it.
- **Controls (correct model SQL): 0 wrong of 88** (rule-of-three upper bound 0.034); 63 answered correctly; 25 wrongly abstained (over-abstention). The gate does not corrupt a right answer; it fails to catch a wrong one.
- **These are adversarially chosen cases. The WRONG share (about 68%) is a property of the test design, not an error rate and not an accuracy number.** It says what the gate does not catch, not how often a model emits it.

## 2. Why the answers were wrong

A raw model `query_sql` that parses, runs and touches only granted tables is served as `L2_VALIDATED`. Nothing on main checks that the SQL **means** what was asked. Verified in the current tree:

| What the gate checks for raw SQL | Where | What it does not check |
|---|---|---|
| reserved `$as_of`, hostile-SQL regex, granted table names, `EXPLAIN` parses | `generative_ask.py:637-663` (`validate_compiled_sql`) | join cardinality, grain, measure, filter polarity, ties, period boundaries |
| the raw-SQL branch submits after those checks | `generative_ask.py:1222-1281` (`kind == "sql"`) | it never goes through `Ontology.compile`, so the typed-plan fan-out/grain guards do not apply |
| honest coverage text for raw SQL | `ontology.py:275-281` (`coverage_from_sql_path`) | it records "query_sql not slot-compiled; grain coverage unknown" and the badge stays confident |
| table names after FROM/JOIN | `envelope.py:360-372` (`_sql_cited_labels`) | a second table in a comma join (`FROM a, b`) was not scanned: A-056 read an ungranted table (Cortex's own manifest check, stubbed here, may catch it in production) |
| declared-ontology refusal of unsafe joins in SQL | `generative_ask.py:1236-1252`, guarded by `declared is not None` | the product call `executor/__init__.py:731-759` passes no `ontology=`; I did not trace whether `declared` can be set another way (the Mapper reported it dead; unverified) |
| empty result set is withheld (hard rule 12) | `envelope.py:1447-1459` | a one-row aggregate over nothing (`COUNT=0`, `SUM=NULL`, `COALESCE(...,0)`) is deliberately allowed (comment at :1449) and passes confidently |
| time qualifiers | `qualifiers.py:344-356` (`_honors_time_filter`) | token match: any `WHERE`, or the year/month word anywhere in the SQL text including a column alias, satisfies "last month" / "in September 2026" |
| SQL-source ingest | `db_connector.py:471` (`str(v)`), `bronze.py:491` (`VARCHAR`) | everything is stored as text on main, so text money and dates reach the gate untyped (typed ingest is on open PRs #311/#316) |

So **`L2_VALIDATED` means "it ran", not "it is right"**. That is the badge-label defect: the badge claims more than the gate established.

## 3. Results per family

Mechanical verdicts (strict grader: case-sensitive strings, NULL is not blank, column names compared). n is cases; bounds are exact 95% intervals, or 3/n when the count is zero. **Not accuracy rates.**

| Family | n | CORRECT | WRONG | ABSTAIN | WRONG bound | verifier: confirmed / ambiguous / not confirmed |
|---|---|---|---|---|---|---|
| A fan-out / double counting | 59 | 16 | 40 | 3 | 40/59 = 0.68, exact 95% CI [0.54, 0.79] | 40 / 0 / 0 |
| B grain / measure / row count | 64 | 11 | 45 | 8 | 45/64 = 0.70, exact 95% CI [0.58, 0.81] | 39 / 4 / 2 |
| C dates / time zones / periods | 61 | 12 | 43 | 6 | 43/61 = 0.70, exact 95% CI [0.57, 0.81] | 34 / 9 / 0 |
| D type traps | 68 | 16 | 46 | 6 | 46/68 = 0.68, exact 95% CI [0.55, 0.78] | 43 / 0 / 3 |
| E wording traps | 86 | 15 | 57 | 14 | 57/86 = 0.66, exact 95% CI [0.55, 0.76] | 51 / 6 / 0 |
| **all** | 338 | 70 | 231 | 37 | 231/338 = 0.68, exact 95% CI [0.63, 0.73] | 207 / 19 / 5 |

By case kind (mechanical):

| Kind | n | CORRECT | WRONG | ABSTAIN |
|---|---|---|---|---|
| trap (model SQL is wrong) | 213 | 0 | 201 | 12 |
| control (model SQL is right) | 88 | 63 | 0 | 25 |
| abstain-expected (data cannot answer) | 37 | 7 | 30 | 0 |

Of the 37 abstain-expected cases (the data cannot truthfully support a figure), 30 were answered confidently. Of the 213 traps, 12 were stopped (by abstaining).

## 4. Confirmed-wrong causes (verifier, blind derivation)

| Cause | Confirmed cases | Representative repro (a proposed test, no disputed reading) |
|---|---|---|
| FANOUT | 29 | `A-013`: What is the average risk score of the suppliers that we hold stock from? |
| WRONG_GRAIN | 19 | `B-049`: In f32_ambiguous_scope.xlsx on the Sales sheet, what is the top 1 SKU per category by sales_value_myr? |
| TEXT_TYPE_COERCION | 18 | `D-047`: What is the largest single shipping cost? |
| DROPPED_ROWS | 17 | `B-054`: What is the total outbound quantity in kg? |
| OTHER | 17 | `B-057`: How many shipments are delayed? |
| UNANSWERABLE_ANSWERED | 17 | `B-045`: What is the total revenue by customer? |
| PERIOD_BOUNDARY | 14 | `C-018`: Which SKUs expired before 2026-10-02? |
| WRONG_MEASURE | 13 | `B-051`: In f32_ambiguous_scope.xlsx on the Sales sheet, what are the top 3 categories by sales_value_myr, as a percentage of the total? |
| DROPPED_FILTER | 12 | `B-012`: What is the average sale size in kg? |
| TIES_LIMIT | 12 | `E-001`: Which SKU has the highest stock quantity? |
| WORDING_MISREAD | 10 | `E-029`: Which SKUs have at least 900 kg in stock? |
| EMPTY_AGG_CONFIDENT | 9 | `B-044`: What is the average unit cost of SKU-OMEGA? |
| WRONG_FILTER_POLARITY | 7 | `E-069`: In f32_ambiguous_scope.xlsx on the Sales sheet, show the top 3 categories with the lowest sales_value_myr |
| NULL_HANDLING | 6 | `D-005`: How many inventory SKUs have never been shipped? |
| TZ_SHIFT | 4 | `C-007`: What was the total outbound quantity in kg on 2026-09-30? |
| STALE_OR_FIXED_PERIOD | 2 | `C-022`: What was the total outbound quantity in kg last year? |
| UNGRANTED_ACCESS | 1 | see the per-case file |

Each representative case, with the injected SQL, what the customer was served and the blind-derived correct answer, is in the per-case file (section 8) and in `tests/redteam/test_redteam_findings.py` as a strict xfail.

## 5. Badge labelling

- `L2_VALIDATED` on **193 of 207** confirmed-wrong answers; the other 14 carried `L0_CERTIFIED` (sheet lane) or `L1_GOVERNED_METRIC` (pack lane): B-033, B-034, B-048, B-049, B-050, B-051, D-062, D-063, D-064, D-065, E-067, E-068, E-069, E-070. The verifier set `badge_ok=false` wherever the answer was wrong, so that field follows from the verdict and is **not an independent finding**; the independent point is section 2 (what the gate established).
- **UI mitigates `L2_VALIDATED`, not `L0`/`L1`.** The Studio chip renders `L2_VALIDATED` as "generated - check sources" (`apps/ui/src/lib/badgeCopy.ts:15`), so for L2 the customer-visible over-claim is softer than the API badge value. The unhedged over-claims are the L0/L1 cases above.
- The harness's own badge-label audit (`tests/redteam/rt_badge.py`) found **0 violations**: it checks that the label is *consistent* with route, rows and abstain flag. It cannot see that the label is *unearned*. Both statements are true; the second is the finding.
- Label oddities seen in dev runs (attacker-reported unless noted): a `SUM` over zero rows served `total_kg=None` with a synthesised `v_count=1` value (E-040/E-042, C-049); a PII-mask token corrupting served prose while the rows are right (B-028: `share_pct=1.DMSMASK_account_01`, verifier-noted); the sheet lane serving `L0_CERTIFIED` while ignoring "excluding X", a `for sku X` filter, "lowest" and unknown measures (E-067..E-070, B-048..B-051), with nothing in `assumptions` about the ignored clause.
- Near-miss pack phrases never earned a wrong `L1` in the cases that tried (the matcher is exact-normalised). The pack lane itself served wrong figures under `L1_GOVERNED_METRIC` on dirty keys and NULL-supplier lines: D-062..D-065, B-033 and B-034 are in the confirmed count (D-063, D-064 and B-033 also rest on how "spend" is read, which the verifier flagged; B-035 is ambiguous).

## 6. Over-abstention (correct SQL refused)

25 of 88 controls were refused on main. Observed causes (attacker and builder dev runs; not separately verified): a CTE name read as an ungranted table (`validate:ungranted:<cte>`); the E10 shape check matching "per kg" or "last N days" and demoting a correct scalar; QUAL-GUARD requiring the word `destination` for "by location"; two or more grain words routing to the multi-grain compile, which ignores the model SQL. This is the cost side: a safer gate must not buy safety by refusing correct answers.

## 7. A measurement of in-flight work (PR #311), not a recommendation

I replayed the same 338 cases against PR #311's own head (14d6765, `GRAIN-GUARD-01`) with the harness copied in. **It was not merged with main**: the merge conflicts in five files (`generative_ask.py`, `envelope.py`, `__init__.py`, `pii.py`, CHANGELOG), and hand-resolving another writer's 57-file PR would make the result unattributable. Mechanical verdicts only, not re-judged; local run, unverified.

- All cases: WRONG 231 -> 112; ABSTAIN 37 -> 165; CORRECT 70 -> 61. Newly wrong: 0.
- Of the 207 confirmed-wrong cases: 96 become ABSTAIN, 11 become CORRECT, **100 are still served wrong**.
- Controls answered correctly: 63 -> 39 of 88. Most abstains on #311 cite `fan_out_unanalysable:scope` (79 of the flipped confirmed-wrong cases): the guard fails closed when it cannot analyse the SQL.

| Cause | confirmed on main | still WRONG on #311 | ABSTAIN | CORRECT |
|---|---|---|---|---|
| FANOUT | 29 | 1 | 28 | 0 |
| WRONG_GRAIN | 19 | 9 | 10 | 0 |
| TEXT_TYPE_COERCION | 18 | 10 | 8 | 0 |
| DROPPED_ROWS | 17 | 8 | 9 | 0 |
| OTHER | 17 | 7 | 10 | 0 |
| UNANSWERABLE_ANSWERED | 17 | 9 | 0 | 8 |
| PERIOD_BOUNDARY | 14 | 8 | 6 | 0 |
| WRONG_MEASURE | 13 | 11 | 2 | 0 |
| DROPPED_FILTER | 12 | 10 | 2 | 0 |
| TIES_LIMIT | 12 | 6 | 6 | 0 |
| WORDING_MISREAD | 10 | 8 | 2 | 0 |
| EMPTY_AGG_CONFIDENT | 9 | 3 | 4 | 2 |
| WRONG_FILTER_POLARITY | 7 | 4 | 3 | 0 |
| NULL_HANDLING | 6 | 3 | 3 | 0 |
| TZ_SHIFT | 4 | 1 | 3 | 0 |
| STALE_OR_FIXED_PERIOD | 2 | 2 | 0 | 0 |
| UNGRANTED_ACCESS | 1 | 0 | 0 | 1 |

Read: #311 closes fan-out (28 of 29 become abstains) at an over-abstention cost, and leaves measure, filter, period, wording, ties and text-type causes mostly untouched. Who owns that residue was not established here: this report did not search issues exhaustively, and the PRD intake maps each class to an owner.

## 8. Evidence and reproduction

- Per-case evidence (question, injected SQL, served badge/route/rows, blind-derived SQL and rows, issue, judge grades) for all 231 suspected cases: `docs/subagents_findings/2026-10-05_redteam-gate-01.cases.jsonl`.
- Corpus: `tests/redteam/corpus/{a..e}.yaml` + `.ext.sql` (338 cases; synthetic seed plus extension rows, no personal data). Harness: `scripts/redteam_run.py`, `tests/redteam/`.
- Proposed tests: `tests/redteam/test_redteam_findings.py`: 37 strict xfails (one per verified cause class, disputed readings excluded) and 8 controls. Result on main 7a8d6c1 + harness: **8 passed, 37 xfailed, 46 s**. On #311's head the same module gives 15 XPASS(strict) and 22 xfail, and all 8 controls fail (selected as the controls #311 refuses, so that 8 of 8 is by construction; the unbiased figure is the full-sweep control count above).
- Run: `python scripts/redteam_run.py --cases tests/redteam/corpus/a.yaml --family a --out <dir> --run-id x` (no network, no keys, no model).

## 9. Limits and disclosures

- Injected SQL is inferred. The attackers chose wrong SQL on purpose, so none of this measures a model. A live run (real model through OpenVault) was **not** done and needs a decision on the key path.
- Cortex enforcement, the F5 gate, the ledger chain and the 1000-row cap are stubbed or not emulated. A-056 (ungranted table via comma join) in particular may be caught in production.
- Typed-plan paths (top-N `ORDER BY ... DESC`, default `LIMIT 50`) are unreachable because the stub injects only `query_sql`; they are untested here, not cleared.
- Attackers, judges and verifiers are all the same model family; correlated blind spots are possible. The verifier flagged judgment calls a reasonable person could dispute (e.g. A-003, A-015, A-016, E-003/E-004/E-009/E-010, D-002, D-008/D-009, D-021/D-023/D-025, B-002/B-011/B-019/B-024, C-008/C-014, C-011..C-013). The verifier held its verdict under every alternative reading it checked for the confirmed ones; the disputed readings are excluded from the proposed tests.
- Independence incidents: (1) two judges shared a scratch folder and one overwrote another's helper script, so each re-graded the affected packets from its own chunk; (2) judges 1 and 2 share 49 of 338 verbatim-identical short reason strings (judge 3 shares none), all restating the same numbers, and I found no evidence of copied grades; (3) the harness builder ran `import dms_api` once, which made 4 health probes to 127.0.0.1:5000 (OpenVault was offline, no key was minted); (4) attackers ran the harness on their own cases while writing them, and their own readings were not used.
- Clock: 10 cases read today's date; the run date was 2026-10-02 and verifiers used `DATE '2026-10-02'`. Rerunning on another day changes those.
- The sample is one small synthetic warehouse. Nothing here generalises to a customer's data.

## 10. Questions for the PRD

1. **Badge semantics.** Should a raw-model-SQL answer be allowed `L2_VALIDATED` when only parse, grant and `EXPLAIN` ran? (A product decision, not mine.)
2. **Owner for the residue** after #311: measure, filter, period, wording, ties, text types, and unanswerable-but-answered (about 100 of 207 on #311's head).
3. **Over-abstention budget**: how much correct-answer loss is acceptable for fail-closed guards (#311: 24 of 63 correct controls refused)?
4. **Live lane**: approve a run through OpenVault with a stated key path and a DeepSeek exclusion that can actually be enforced (the Cortex build scouted ignored the model pin).
5. **Fate of the proposed tests**: they live under `tests/redteam/`, not `tests/invariants/`; promoting any to an invariant needs an `INVARIANT-CHANGE:` commit from someone with that authority.
