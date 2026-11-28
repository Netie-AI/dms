---
keywords: [SERVING-PRECHECK-01, serving_precheck_missing, badges_unverified, cortex_l2_off, BADGE-GUARD-01, dms-303, dms-336]
main_idea: "A round is not baseline-eligible without the serving path, inode, mtime, snapshot hash, and a positive row count for every table the pack SQL names. Until BADGE-GUARD-01 the summary and the dms#299 grid print badges_unverified: cortex_l2_off next to n and no badge or crag figure. Not COMPLETE."
models: [grok-4.7]
workflow: ticket-runner
reuse: golden_rule
status: raw
cite: agent: serving-precheck-01
repo: DMS
date: 2026-10-06
---

# SERVING-PRECHECK-01: serving precheck and unverified badges

PREFLIGHT: MISS (no prior findings row for SERVING-PRECHECK-01).

## Scope amendment this doc records

PR #336 comment https://github.com/Netie-AI/dms/pull/336#issuecomment-5990263936 (jian-hong, 2026-10-05) said the second scope amendment was not on head `44154fd` and the PR body did not quote it. The amendment is issue #303 comment 5938984915 (2026-10-01 19:29Z). Quoted here as asked:

> **DMS Epic: second scope amendment to SERVING-PRECHECK-01 (Refs dms#303, adds to comments 5938639520, 5938769006, 5938840371; per Gating, follows dms#263 comment 5938944656)**
>
> The scorer, not whoever writes up the round, reports badges as unverified. This rides in SERVING-PRECHECK-01 because it's the same summary path and the same file, so it needs no new slot.
>
> - Until BADGE-GUARD-01 is stamped, the dms#299 grid and the round summary print no breakdown by badge or by `crag`. They print one line, `badges_unverified: cortex_l2_off`, next to n.
> - Must-fail test: a record shaped like 2026-09-25's (42 answers, all `L2_VALIDATED`/`ontology_plan` with `crag: "validated"`, plus 10 abstains). On `fee155e4` it produces a figure keyed by badge or `crag`. On the head it produces none, and the line is present.
> - Must stay unchanged: outcome counts, n, and every existing reason in `baseline_ineligible_reasons`.
>
> The PR body quotes this comment too.

The comment's own list of that amendment:

- One line `badges_unverified: cortex_l2_off` beside n, and no breakdown by badge or `crag`, until BADGE-GUARD-01 is stamped.
- A must-fail test with a record shaped like 2026-09-25's: 42 answers `L2_VALIDATED` / `ontology_plan` with `crag: "validated"`, plus 10 abstains.

## Where it lives

`scripts/score_curated.py` `live()` and `grid_score_hook` print `badges_unverified: cortex_l2_off` next to n. Case lines do not print badge or `crag=`. Badge and crag stay on the stored case. `ab_offline` still prints its pack `crag=` aggregate. `tests/test_badge_unverified_01.py` is the 42 + 10 shape through `live()`.

`baseline_eligibility` appends `serving_precheck_missing` when the round lacks the serving path, inode, mtime, snapshot hash, or a positive row count for a table the pack's correct SQL names, or when that SQL does not parse to a table. `tests/test_serving_precheck_01.py`.

Does not close dms#303. Not COMPLETE. No live scored round.
