# HELDOUT-PACK-D a blind held-out pack and its scoring hook (dms#257 proposal)

Keywords: HELDOUT-PACK-D, held-out, blind pack, EPIC-A1, dms-257, score_heldout, manifest, fingerprint, row judge, assert_envelope_valid, has_order_by_limit, ORDER BY LIMIT, classify_column, false positive, cross-check agreement

Main idea: a frozen pack of questions DMS's authors never saw, on a synthetic database nobody here built DMS for, plus a draft hook that scores it with the existing row judge. The pack is external and is not in this repo. The hook pins it by manifest root hash only: `e63b422e26336c6cae5f824d1af69f68ee5236544160a0685d0f982fad2ceee7`. **No scored round was run.** There is no WRONG count, so there is no rule-of-three bound to print. Not COMPLETE. Not a baseline. EPIC-A1 and dms#257 stay open.

## What exists (measured)

- **Database.** Synthetic, and not in this repo. Deterministic: a second build under a different hash seed gave identical per-table row hashes (own fingerprint code, not the builder's dump script).
- **Planted mess.** The generator plants dirty-data cases (soft deletes, duplicate names, missing conversions, partial periods, a mid-window close, nullable FKs, status drift). The counts stay in the pack.
- **Questions.** Written by blind authors. After cross-check, some are answerable and some are to refuse. Refuse reasons stay separate. One-row answers and ordered top-N answers both exist.
- **Cross-check.** Blind analysts saw only the question text. Round 1 had a single non-agreement; that question was dropped (finding 1). Round 2 had n=0, so there is no round-2 rate.
- **Personal-data scan, counts only.** Own patterns: no email, date-of-birth, phone, or national-id hits, and no flagged column names. The scanner was first run on planted data and caught the planted email, date-of-birth and column-name cases; phone and national-id patterns were not planted. `dms_core.pii.classify_column` still flags columns (finding 2).
- **Hook tests.** `tests/test_score_heldout.py`: 27 passed on a synthetic pack in `tmp_path`. Every gate was broken in turn and a test went red each time. On the real pack the hook's `--self-check` passes with the pinned root, passes on the independent rebuild, and refuses a wrong database.

- **Instrument self-test (`--synthetic`, measured on the real pack).** The pack's frozen gold is replayed through DMS's envelope constructor as a perfect answerer, then deliberately damaged. No DMS is asked and no model is called; this is not a score and not a baseline. The perfect replay is not all OK: the masker turns some correct values into INVALID `masked_compare`. Damaged responders (all abstain, a doubled row, a first number off by one unit of its own scale, a confident answer on a refusal, a green badge on an abstention) come apart from the perfect replay. Before the scale guard (finding 7) the off-by-one responder had cent-level errors judged OK; the self-test is what found that. No accuracy figure.

## Findings for the PRD (feedback, not tickets)

1. **Scorer: `has_order_by_limit` reads the whole SQL.** `scripts/oracle_row_match.py` marks a gold as ordered when `ORDER BY` and `LIMIT` both appear anywhere, including only inside a subquery. Measured: one such result is identical across thread counts and only the row order varies. Inferred, not run against DMS: a correct DMS answer in another row order would be judged WRONG (`rows_mismatch:values`). That question was dropped from the pack rather than rewritten. A fix belongs to the scorer's owner, with a test on a subquery `ORDER BY ... LIMIT` and an unordered outer query.
2. **DMS PII classifier flags columns of a database with no personal data.** Measured: typed DATE columns whose names are not in `_TYPED_DATE_COL` are treated as birth dates by `_whole_date_is_dob`; a short letter-plus-digit document number matches `_PASSPORT_FIND`. Effect when scored: answers carrying those columns are masked and counted INVALID `masked_compare`, not OK or WRONG. Re-measured after merging main's `pii.py` rewrite (main `7a8d6c1`): same kinds. Related to the over-mask work (dms#284).
3. **Frozen packs need `* -text`.** The first freeze verified in its own folder but failed on a fresh clone, because git normalised CRLF files that Windows agents wrote. The pack now carries `.gitattributes` `* -text` and verifies on fresh clones under `core.autocrlf` true, input and false. Any future frozen pack needs the same.
4. **Agreement is not correctness.** Author and cross-checker are the same model family reading the same data dictionary, so a shared misreading passes. Agreement measures how reproducible the reading is.
5. **Selection effect.** Only agreed questions are kept, and authors were told to name output fields and state each definition. The pack measures answering unambiguous questions on an unseen schema. It understates the wrong-answer rate on loosely worded real questions.
6. **Refusals.** `ambiguous_metric` refusals are judgement-dependent (a defensible analyst could pick a reading). The hook reports refusals per reason, never pooled.
7. **Scorer: `numeric_scale_from_sql` misreads `ROUND` with a comma in its argument.** It uses `ROUND\s*\([^,]+,\s*(\d+)\s*\)`, so a `ROUND` whose first argument contains a comma is read as the wrong scale, and a `ROUND` with no such inner tail as no scale. Effect, measured with the off-by-one responder: the imported judge called some cent-level errors OK; the no-scale cases fall back to exact comparison and are over-strict. Re-compare at the parsed scale still agreed, and the pack stands. The hook guards itself (`scale_guard`: re-compare at the parsed scale after the imported judge rules; an OK that differs becomes WRONG, a WRONG that only exists because of exact comparison becomes INVALID `scale_mismatch`). The scorer itself is unchanged; the fix belongs to its owner and, per Gating, should only tighten. Likely also affects `tests/fixtures/curated_ceo/oracles.yaml` golds with a comma inside `ROUND` (not measured).
8. **DMS's envelope masker masks correct answer values.** Measured on the perfect replay: some gold answers cannot be OK. A plain money running total is masked as a phone, and a list of dates is masked as a date of birth. The scorer correctly reports INVALID `masked_compare`. This is the answer-value side of finding 2, in the same code area as dms#284 and dms#318.
9. **The shared PII masker has a quadratic email pattern.** Measured on main `7a8d6c1` with only `dms_core.pii` imported: `_EMAIL_FIND` (`pii.py:125`, used at `:504` and `:790`) on `"a."` repeated takes 0.022 s at 2,000 characters, 0.09 s at 4,000, 0.36 s at 8,000 and 1.33 s at 16,000 (4x per doubling). `mask_envelope` with one 16,000-character `"a."` answer and one such row cell takes 4.1 s, and Python's `re` holds the GIL, so one long database cell can stall the API process today. Strings of short tokens did not reproduce on this pattern alone and are not claimed. It surfaced here because another PR (BANK-02, dms#269) newly runs the masker on question text and executed SQL. Any fix must return identical match spans (widen-only, dms#318); narrowing the pattern, chunking without a safe overlap, and a timeout that returns unmasked text are refused.

## Blindness, stated honestly

Authors and cross-checkers were told not to read the dms repo. The harness gave every agent the dms `CLAUDE.md` and a git-status listing at start (rules text and file names; no code, fixtures or questions). Access to the answer-key files by the cross-checkers can be verified from a transcript for 1 of 4; the other 3 transcripts are empty and file access times did not update on this machine, so for them it rests on instruction and on their own reports describing real exploration.

## Hook (`scripts/score_heldout.py`)

- **Judge.** Imports `score_curated.judge_envelope_detailed` and `oracle_row_match` (#292/#299/#300 rules). Nothing copied. Refusal questions are judged by badge: a confident answer is WRONG.
- **Fail-closed preflight, before any network call.** Pack outside this repo; every manifest sha256 and the pinned root match; the pack's scan says PASS; every gold reproduces its frozen rows on `--oracle-db`; every table of `--oracle-db` matches the frozen per-table fingerprint (catches an edit no gold query reads). Any failure is CONFIG, never a score.
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
- Findings 4 and 5 go to EPIC-A3 #265: accept this held-out pack as its first set only with a caveat line on every number, plus a second differently-sourced set before any sales number.
- The live round is an A3 acceptance run.

## Ceiling

The hook has never been run live. A scored round needs three things, per the intake:
- **Attach.** DMS has no DuckDB-file source (`db_connector.py` SourceKind is sqlserver, mysql, postgresql). Restore the pack into Postgres and extract it through the existing SqlSource path (as BIRD was), or use CSV ingest. A new DuckDB-file source type is refused (hard rule 6, no swap scenario).
- **Parity.** Inferred, not run: this hook fingerprints the frozen DuckDB (`--oracle-db`), but DMS would answer from its extracted copy. A round needs a parity check of the Space's extracted tables against the frozen per-table fingerprint (row counts and per-table hashes after type normalisation), or an extract defect would read as a DMS WRONG.
- **Gates.** The A1 baseline gates and a grants preflight like dms#304's (the Space grants exactly the tables in the frozen fingerprint).

Cross-check never used a different model family; that needs a key path through OpenVault and is Platform's call. Not COMPLETE.
