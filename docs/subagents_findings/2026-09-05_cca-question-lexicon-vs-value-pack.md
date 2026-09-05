# CCA: a term pack cannot be both the question lexicon and the value lexicon

**Date:** 2026-09-05
**Keywords:** EPIC-CCA, CCA-05, binder, false engage, R-0005, dms#137, dms#148
**Tickets:** CCA-02 #134, CCA-03 #135, CCA-04 #136, CCA-05 #137, CCA-08 (new)

## Expected vs actual

**Expected.** Landing the constraint cascade in front of L0 would abstain on asks whose
filters cannot be bound, and leave every other ask on the path it already took. The
engagement check reads the question with the same term packs the binder uses to match
landed column values, which looked like the drift-free choice: one list, no chance of the
lexicon and the binding disagreeing.

**Actual.** The full suite went from 611 passed to 610 passed / 1 failed on the first
wiring. `tests/test_vq02_verified_register.py::test_ask_in_space_is_l0_foreign_space_misses`
turned `L0_CERTIFIED` into `ABSTAIN`. The question was:

    VQ-02 steward: capacity of warehouse A?

`warehouse` is a genuine alias of the `Commercial` asset class, because a warehouse is a
commercial property type and a `property_type` column really does spell it that way. It is
also this product's word for a physical location, and `locations` is a demo warehouse
table. So the cascade engaged, tried to bind an asset class, found no `asset_class` column,
and abstained on a question that had answered correctly for months.

A second instance of the same class was found by reading rather than by the suite: the
`Buy` sense carried the bare aliases `sale` and `sold`. "How many units sold last month" is
core product vocabulary in a logistics app. It would have engaged, scanned
`transactions.txn_type`, found `inbound` and `outbound`, matched no tenure member, and
abstained. No test covered it, so it would have shipped.

## Repro steps

1. `git checkout 623650f~1` (the wiring commit's parent), then apply only the `live_ask`
   hook from `packages/executor/dms_executor/__init__.py`.
2. `export PYTHONPATH=apps/api:packages/core:packages/cortex_client:packages/executor:packages/ledger`
3. `DMS_SKIP_CONTROL_PLANE_TESTS=1 python -m pytest tests/test_vq02_verified_register.py -q`
4. Observe `assert 'ABSTAIN' == 'L0_CERTIFIED'`.
5. For the unwritten second case:
   `python -c "from dms_executor.cca.sense import propose_senses; print(propose_senses('how many units sold last month'))"`
   printed `('Buy',)` before the fix and `()` after.

## Root cause CLASS

**A vocabulary serving two different questions needs two different lists.**

The packs answer two questions that only look alike:

* *Is this landed value a member?* Wants maximum coverage of encodings, including short
  codes (`COM`, `RES`) and every noun a schema might use. A wrong answer here means a
  filter matches nothing, so the list must be generous.
* *Is this word in the question a filter term?* Wants maximum specificity. A wrong answer
  here means an ask that works today stops working, so the list must be conservative.

Merging them optimises one direction and silently pays for it in the other. The generous
list is exactly wrong for question reading, and the bug's severity scales with how ordinary
the shared word is: `shoplot` is safe, `warehouse` and `sold` are not.

This is not specific to CCA. Any place in this repo where one declared vocabulary is used
both to *recognise intent* and to *match data* has the same shape.

## Fix

`QUESTION_ALIASES` in `cca/asset_class.py` and `cca/sense.py`, a narrowed pack built from
it for question parsing only, with the full pack still used for value certification.
`cca/segment.py` had already split them (`SEGMENT_TERMS` vs the packs) and needed no change,
which is the pattern the other two now follow. Bare common nouns survive only in
property-shaped or tenure-shaped forms: `warehouse property`, `office space`, `for sale`,
`sale price`, `to let`.

Second, independent fix in `live_ask`: the verified-query hook now runs **before** the
cascade and is not gated. A steward who registered that exact question against that exact
SQL is the human certification the cascade substitutes for on the inferred path, and a term
the cascade cannot bind must not overrule a person who already decided.

## Which invariant applies

**A control that blocks legitimate work is itself a failure. Re-check after hardening.**
(R-0005 in the repo's ledger; the global invariant list carries the same rule.)

The cascade is a precision control. Precision controls are graded on both sides, and the
side that gets measured is the side that gets tested. The corpus of hostile asks measures
the first side. Nothing measured the second until the existing suite objected, and one
failing test is thin evidence for a product-wide vocabulary change.

So `tests/test_cca_cascade.py` now parametrises seven of the product's own questions and
asserts `engages(...) is False` for each. That list is the regression surface for this
class, and it should grow whenever a pack gains an alias.

## What this does not prove

Seven questions is not a corpus. The non-engagement guard covers the demo warehouse's
vocabulary, not a customer's. A deployment whose schema uses `class`, `category` or
`market` as ordinary business columns can still engage the cascade on an ask that meant
none of it, and the honest signal there is an abstention naming a column the customer will
recognise as irrelevant. Rule of three says do not claim a false-positive rate from a
sample this size.
