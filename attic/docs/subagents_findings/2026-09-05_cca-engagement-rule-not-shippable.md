# CCA: deciding from free text whether a question carries a filter is not solved

**Date:** 2026-09-05
**Keywords:** EPIC-CCA, CCA-05, intent, engagement, feature flag, R-0003 round two, R-0005, dms#148
**Found by:** a second independent verification run, attacking the fixes rather than the defects

## Expected vs actual

**Expected.** Round one of R-0003 found seven defects. All seven were fixed and the fixes
were tested. Round two was meant to confirm them.

**Actual.** Round two constructed new inputs in each defect's *class* rather than replaying
its reproduction, and most of the fixes did not hold:

| Fix | New input in the same class | Result |
|-----|-----------------------------|--------|
| dropped `market` from geo columns | `geo_country = ('APAC','EMEA','LA','NA')` | CERTIFIED Laos out of Latin America |
| negation reach and postfix | `residential is excluded` | CERTIFIED `IN ('RES')`, the exact inverse |
| negation, again | `Excluding tax, commercial revenue by month` | CERTIFIED `NOT IN ('COM')`, cue crossed a comma |
| geo exclusion abstains | `all of SEA other than Singapore` | CERTIFIED `country IN ('SG')` |
| coverage sentence honesty | two scanned columns | attributed one column's unmatched values to the other |

And two measurements that matter more than any single defect, taken against this product's
own vocabulary:

* **False engage: 46 of 106.** Ordinary questions engaged the cascade and then abstained,
  refusing an answer the product gives today. `Show all purchases from SUP-02`,
  `What is the commercial class of each vehicle?`, `Show the housing market trend for
  Klang Valley`, `Which products are in LA warehouse?`. The cue words that make a term
  filter-shaped (`all`, `any`, `no`, `in`, `only`, `class`, `market`, `segment`, `country`)
  are among the commonest words in a business question.
* **False miss: 35 of 37.** Asks that plainly name a filter were not recognised, so the
  stage recorded `(no recognised term)` as CERTIFIED and the trace went green over an
  unconstrained filter. `Commercial lease revenue for the year` against a warehouse holding
  `COM` and `RES` certifies with the class never bound.

Plus one defect of mine, verified: the hook read `tables or grantable_tables(...)`, and
`tables` is the request body's `grounded_tables`, unvalidated. `demo_acl` caught it on the
answering path, but a blocked cascade returns 200 *before* that check, and the abstain
envelope carries up to twelve distinct values per scanned column in its evidence. A caller
naming an ungranted table got its column values back from an endpoint that answers 403 for
the same table one line later.

## Repro steps

```
# false miss, the high-frequency one
run_cascade("Commercial lease revenue for the year", warehouse=<asset_class COM/RES>, tables=[...])
  -> certified=True, blocked_at=None, asset_class CERTIFIED "(no recognised term)"

# inverted filter, the high-severity one
bind_asset_class("residential is excluded", ...)  -> CERTIFIED  asset_class IN ('RES')

# boundary leak (fixed)
POST /v1/chat/ask {"question": "...SEA...", "grounded_tables": ["hr_confidential"]}
  -> 200, evidence: ["column=hr_confidential.country", "unmatched_values=Switzerland, ..."]
```

## Root-cause CLASS

**Two different problems were being solved by one mechanism, and only one of them is
solvable this way.**

*Binding* a term to landed values is a closed problem. The pack proposes, the column
decides, matching is exact on a normalised form, and the answer is checkable. That half is
sound and is measured by `scripts/cca_eval.py`.

*Deciding from free text whether a question carries a filter at all*, and with what
polarity, is natural-language understanding. Every attempt here was a token window over a
closed cue list. A window has no clause boundaries, so `Excluding tax, commercial revenue`
negates the class. A reach of one misses `residential is excluded`; a reach of three lets
`no matter if commercial` negate commercial. A cue list broad enough to catch `commercial
only` contains `only`, `all`, `in` and `class`, which appear in ordinary questions. Each
round traded one rate for the other, and neither round measured both until asked to.

The tests could not catch it because they are written from the same lexicon as the code.
A corpus of 30 cases built from ~13 distinct questions measures binder precision on a
hand-picked question set. It is not evidence about engagement rates in either direction,
and reporting `100 pct precision` from it invited exactly that reading.

## Which invariant applies

**A control that blocks legitimate work is itself a failure. Re-check after hardening.**
A control that refuses 43% of ordinary work while silently passing 95% of the work it
exists for makes the product worse in both directions.

Also **assert the artifact the customer receives** (hard rule 10): the false-miss case
ships a green trace asserting the constraints were settled, over an answer computed
without them. The audit trail is worse than absent, because it is confidently wrong.

## What was done

1. **The boundary leak is fixed.** The grant decides what the cascade may open; the
   request may narrow it and may never widen it. `grounded_tables` on the envelope now
   reports what was actually read. Regression test in `tests/test_cca_cascade.py`.
2. **Polarity fails closed.** Any polarity cue anywhere in a question stops this cascade
   certifying that stage, in *both* directions. No carve-out for a result that already
   reads as an exclusion: the eval gate caught that exemption within a minute, because
   `excluding tax, commercial property revenue` derives exclude(Commercial). This costs
   coverage on `commercial only`, which is the epic's own phrasing, and that trade is
   pinned by five `polarity abstain` cases in the corpus rather than left implicit.
3. **The ask-path hook ships OFF** (`DMS_CCA_CASCADE`, default `0`). Trunk-based with a
   feature flag is this repo's stated way to land incomplete work. Everything is built,
   tested and reviewable; the engagement rule needs to be measured rather than asserted
   before it decides a customer's answer.

## What is NOT fixed, and must not be claimed

The false-engage and false-miss rates. They are properties of the approach, not of a
lexicon entry, and no further tuning of `intent.py` should be presented as closing them.
Anyone turning the flag on owes a measurement of both rates against that deployment's own
question log, not against this repo's corpus.

## What would settle it

A question corpus that is not written by the person writing the lexicon: real customer
questions, labelled by someone else for "does this carry a filter, and with what
polarity". Then both rates are numbers rather than opinions, and the flag has a criterion
to flip on. Until that exists, the honest position is that DMS binds terms to landed values
well and does not know when a question is asking it to.
