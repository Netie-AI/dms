# HELDOUT-PACK-D a blind held-out pack and its scoring hook (dms#257 proposal)

Keywords: HELDOUT-PACK-D, held-out, blind pack, EPIC-A1, dms-257, score_heldout, manifest, fingerprint, row judge, assert_envelope_valid, has_order_by_limit, ORDER BY LIMIT, classify_column, false positive, cross-check agreement

Main idea: a frozen pack of questions DMS's authors never saw, on a synthetic database nobody here built DMS for, plus a draft hook that scores it with the existing row judge. The pack lives outside this repo (git repo `E:\HeldOut-PackD`, tag `pack-d-v1`, commit `9c1c2e7`, manifest root `e63b422e26336c6cae5f824d1af69f68ee5236544160a0685d0f982fad2ceee7`). **No scored round was run.** There is no WRONG count, so there is no rule-of-three bound to print. Not COMPLETE. Not a baseline. EPIC-A1 and dms#257 stay open.

## What exists (measured)

- **Database.** 16 tables, 221,370 rows, 2,642,540 cells, 39 MB. Deterministic: a second build under a different hash seed gave identical per-table row hashes on all 16 tables (my own fingerprint code, not the builder's dump script).
- **Planted mess.** Soft-deleted suppliers/POs/invoices, 14 groups of suppliers sharing an exact name (plus case/space variants), non-MYR money with no MYR row in `fx_rates`, partial first and last months, a warehouse closing mid-window, nullable FKs, and status drift. Counts are in `db/generator_notes.md` in the pack.
- **Questions.** 304 written by 4 blind authors (76 each). After cross-check: **263 answerable** (88 easy, 96 medium, 79 hard) and **40 to refuse** (8 each of missing_data, ambiguous_entity, ambiguous_metric, out_of_range, relative_time). 105 of the 264 first-round answers are one-row; 27 of the 263 kept are top-N judged in order.
- **Cross-check.** 4 blind analysts saw only the question text. Round-1 agreement **303 of 304** (easy 88/88, medium 96/96, hard 79/80, refuse 40/40). The one non-agreement, HO-0102, was dropped (see finding 1). Round 2 had n=0, so there is no round-2 rate.
- **Personal-data scan, counts only.** Own patterns: 0 email, 0 date-of-birth, 0 phone, 0 national-id hits and 0 flagged column names over 2,642,540 cells (the scanner was first run on planted data and caught the planted email, date-of-birth and column-name cases; phone and national-id patterns were not planted). `dms_core.pii.classify_column` flags 3 columns (finding 2).
- **Hook tests.** `tests/test_score_heldout.py`: 27 passed on a synthetic pack in `tmp_path`. Every gate was broken in turn and a test went red each time. On the real pack the hook's `--self-check` passes with the pinned root, passes on the independent rebuild, and refuses a wrong database.

- **Instrument self-test (`--synthetic`, measured on the real pack, n=303).** The pack's own frozen gold is replayed through DMS's envelope constructor as a perfect answerer, then deliberately damaged. No DMS is asked and no model is called; this is not a score. Results:

  | responder | OK | ABSTAIN | WRONG | INVALID |
  |---|---|---|---|---|
  | perfect | 261 | 40 | 0 | 2 (masked by DMS's masker) |
  | all abstain | 0 | 303 | 0 | 0 |
  | one row doubled | 0 | 40 | 261 | 2 (masked) |
  | first number off by one unit of its own scale | 0 | 40 | 261 | 2 (masked) |
  | confident answer to the 40 refusals | 261 | 0 | 40 | 2 (masked) |
  | green badge on an abstention | 0 | 40 | 0 | 263 |

  Before the scale guard (finding 7) the off-by-one responder showed 8 of 261 answers judged OK, a hidden WRONG; the self-test is what found it.

## Findings for the PRD (feedback, not tickets)

1. **Scorer: `has_order_by_limit` reads the whole SQL.** `scripts/oracle_row_match.py` marks a gold as ordered when `ORDER BY` and `LIMIT` both appear anywhere. HO-0102's gold has them only inside a correlated subquery (latest FX rate). Measured: its result is identical across 1/2/4/8 threads and only the row order varies. Inferred, not run against DMS: a correct DMS answer in another row order would be judged WRONG (`rows_mismatch:values`). Dropped from the pack rather than rewritten. A fix belongs to the scorer's owner, with a test on a subquery `ORDER BY ... LIMIT` and an unordered outer query.
2. **DMS PII classifier flags 3 columns of a database with no personal data.** Measured: `supplier_items.valid_from` and `valid_to` are typed DATE, 2023-06-01 to 2025-10-30; `goods_receipts.delivery_note_no` is 7,630 non-null values, all shaped 2 letters + 7 digits. Inferred from reading `dms_core/pii.py`: the first two fire because `_whole_date_is_dob` treats any whole-value date column whose name is not in `_TYPED_DATE_COL` as a birth date; the third matches `_PASSPORT_FIND` (`[A-Z]{1,2}\d{7,9}`). Effect when scored: answers carrying those columns are masked and counted INVALID `masked_compare`, not OK or WRONG. Re-measured after merging main's `pii.py` rewrite (main `7a8d6c1`): the same 3 columns, same kinds. Related to the over-mask work (dms#284).
3. **Frozen packs need `* -text`.** The first freeze verified in its own folder but failed on a fresh clone for 9 files, because git normalised the CRLF files that Windows agents wrote. The pack now carries `.gitattributes` `* -text` and verifies on fresh clones under `core.autocrlf` true, input and false. Any future frozen pack needs the same.
4. **Agreement is not correctness.** Author and cross-checker are the same model family reading the same data dictionary, so a shared misreading passes. The 99.7 percent measures how reproducible the reading is.
5. **Selection effect.** Only agreed questions are kept, and authors were told to name output fields and state each definition. The pack measures answering unambiguous questions on an unseen schema. It understates the wrong-answer rate on loosely worded real questions.
6. **Refusals.** The 8 `ambiguous_metric` questions are judgement-dependent (a defensible analyst could pick a reading). The hook reports refusals per reason, never pooled.
7. **Scorer: `numeric_scale_from_sql` misreads `ROUND` with a comma in its argument.** It uses `ROUND\s*\([^,]+,\s*(\d+)\s*\)`, so `ROUND(AVG(COALESCE(x, 0)), 2)` is read as scale 0 and a `ROUND` with no such inner tail as no scale. Measured on the 263 pack golds: 18 are misread (7 as scale 0, 4 as scale 1, 7 as none; true scale 2 in all 18). Effect, measured with the off-by-one responder: the imported judge called 8 cent-level errors OK (errors under about 0.5 pass at scale 0, under 0.05 at scale 1); the no-scale cases fall back to exact comparison and are over-strict. My own cross-check compare shared the same regex, so all 18 golds were re-compared at the true scale: all 18 still agree, and the pack stands. The hook guards itself (`scale_guard`: re-compare at the parsed scale after the imported judge rules; an OK that differs becomes WRONG, a WRONG that only exists because of exact comparison becomes INVALID `scale_mismatch`). The scorer itself is unchanged; the fix belongs to its owner and, per Gating, should only tighten. Likely also affects `tests/fixtures/curated_ceo/oracles.yaml` golds with a comma inside `ROUND` (not measured).
8. **DMS's envelope masker masks correct answer values.** Measured on the perfect replay: 2 of 263 gold answers cannot be OK. HO-0186's `cumulative_spend_myr` (plain money, a running total such as 23467734.85) becomes `DMSMASK_phone_*` because the column's values look like phone numbers, and HO-0287's list of dates in `missing_rate_date` becomes `DMSMASK_dob_*`. The scorer correctly reports INVALID `masked_compare`. This is the answer-value side of finding 2, in the same code area as dms#284 and dms#318.
9. **The shared PII masker has a quadratic email pattern.** Measured on main `7a8d6c1` with only `dms_core.pii` imported: `_EMAIL_FIND` (`pii.py:125`, used at `:504` and `:790`) on `"a."` repeated takes 0.022 s at 2,000 characters, 0.09 s at 4,000, 0.36 s at 8,000 and 1.33 s at 16,000 (4x per doubling). `mask_envelope` with one 16,000-character `"a."` answer and one such row cell takes 4.1 s, and Python's `re` holds the GIL, so one long database cell can stall the API process today. Strings of short tokens did not reproduce on this pattern alone and are not claimed. It surfaced here because another PR (BANK-02, dms#269) newly runs the masker on question text and executed SQL. Any fix must return identical match spans (widen-only, dms#318); narrowing the pattern, chunking without a safe overlap, and a timeout that returns unmasked text are refused.

## Blindness, stated honestly

Authors and cross-checkers were told not to read the dms repo. The harness gave every agent the dms `CLAUDE.md` and a git-status listing at start (rules text and file names; no code, fixtures or questions). Access to the answer-key files by the cross-checkers can be verified from a transcript for 1 of 4; the other 3 transcripts are empty and file access times did not update on this machine, so for them it rests on instruction, on 89 percent of SQL texts differing (29 of 264 identical), and on their own reports describing real exploration.

## Hook (`scripts/score_heldout.py`)

- **Judge.** Imports `score_curated.judge_envelope_detailed` and `oracle_row_match` (#292/#299/#300 rules). Nothing copied. Refusal questions are judged by badge: a confident answer is WRONG.
- **Fail-closed preflight, before any network call.** Pack outside this repo; every manifest sha256 and the pinned root match; the pack's scan says PASS; every gold SQL reproduces its frozen rows on `--oracle-db`; every table of `--oracle-db` matches `db/fingerprint.json` (catches an edit no gold query reads). Any failure is CONFIG, never a score.
- **Badge contract (rule 10a).** Every served envelope goes through `assert_envelope_valid`. The row judge runs first so a WRONG is never hidden. Any other verdict on a violating envelope becomes INVALID `envelope:<E-code>`, so a green badge on an abstention cannot pass as ABSTAIN. Summaries print the served-badge tally and the violation codes.
- **Scale guard.** After the imported judge rules, answers are re-compared at the gold's parsed `ROUND` scale when it differs from the judge's reading (tighten-only toward WRONG; exactness-only WRONGs relabelled INVALID). Every summary prints how many gold queries it applies to and how many verdicts it changed.
- **`--synthetic`.** The instrument self-test described above. It exits non-zero if the instrument fails to tell a perfect answerer from the damaged ones, so a blind judge cannot be used to score DMS.
- **Output.** Every summary prints n, all outcome counts, answered, the rule-of-three line and a 95 percent upper bound. No target. Says it is not a baseline. Binds refuse `0.0.0.0` and public hosts.

## Swap (hard rule 6)

No new port, dependency, abstraction or config key. One script, one test file, and a constant (`PACK_D_ROOT_SHA256`) that is a pin, not a setting. Swap scenario for the root pin: a second pack is scored only with `--expect-root`, and every line then says it is not pack D.

## Routing (PRD Agent intake, 2026-10-05, read-only)

Proposed ledger rows F109 to F116 (the last ledger row is F108; F116 is finding 9, routed to the dms#318 PII lineage beside F110 and F115, independent of their decision record, BOUNDARY, to be seated ahead of OVERMASK-02). They are NOT yet appended: the ledger lives in the Netie repo, which has other people's uncommitted edits. Summary:
- Findings 1 and 7 go to EPIC-A1 #257 as new instrument tickets (scale first, then order; both are in `oracle_row_match.py`, one writer).
- Findings 2 and 8 are a BOUNDARY product over-mask in the dms#318 lineage and need a decision record before any slice.
- Finding 3 goes to Netie-KB.
- Findings 4 and 5 go to EPIC-A3 #265: accept Pack D as its first set only with a caveat line on every number, plus a second differently-sourced set before any sales number.
- The live round is an A3 acceptance run.

## Ceiling

The hook has never been run live. A scored round needs three things, per the intake:
- **Attach.** DMS has no DuckDB-file source (`db_connector.py` SourceKind is sqlserver, mysql, postgresql). Restore the pack into Postgres and extract it through the existing SqlSource path (as BIRD was), or use CSV ingest. A new DuckDB-file source type is refused (hard rule 6, no swap scenario).
- **Parity.** Inferred, not run: this hook fingerprints the frozen DuckDB (`--oracle-db`), but DMS would answer from its extracted copy. A round needs a parity check of the Space's extracted tables against `db/fingerprint.json` (row counts and per-table hashes after type normalisation), or an extract defect would read as a DMS WRONG.
- **Gates.** The A1 baseline gates and a grants preflight like dms#304's (the Space grants exactly the pack's 16 tables).

Cross-check never used a different model family; that needs a key path through OpenVault and is Platform's call. Not COMPLETE.
