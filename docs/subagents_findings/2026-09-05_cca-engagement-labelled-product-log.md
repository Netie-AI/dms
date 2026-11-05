# Independently labelled product questions: both engagement rates are numbers

**Date:** 2026-09-05
**Keywords:** EPIC-CCA, CCA-05, engagement, independent labels, false engage, false miss, DMS_CCA_CASCADE, dms#132
**Found by:** founder request after R-0003 round two shipped the hook OFF

## Expected vs actual

**Expected.** A question corpus not written by the lexicon author, labelled for
"does this carry a filter, and with what polarity", so false-engage and
false-miss stop being opinions.

**Actual.** 77 unique questions harvested from the product's own chat surfaces
(free-form demo, hostile pack, playground, CEO walkthrough, Constructor, live
demo, L2 bakeoff). Zero from `cca/*.py` or `tests/fixtures/cca_eval/corpus.json`.
Labels written by a separate accuracy pass that was forbidden to read the
lexicon.

| Slice | n | Result |
|-------|---|---------|
| ordinary (carries_filter=false) | 76 | false-engage **0/76** |
| filter-positive | 1 | false-miss **1/1** (`ff_outbound_by_supplier_country`, geo/include, Malaysia) |
| uncertain | 0 | counted apart |
| shippable | no | miss n=1 < floor of 8; miss rate 100 pct |

The one named filter in this log is "goods from Malaysian suppliers". The
cascade proposed no geo stage. SKU BETA, city KL, hazmat only, ignore Wide_Fill,
warehouse A, grouping by country, and Malay category rankings were labelled
not-CCA, and the cascade stayed out of all of them.

## What this is not

The 46/106 and 35/37 figures from round two were measured on constructed
property-domain vocabulary (purchases from SUP-02, commercial class of each
vehicle, commercial lease revenue). That set is not this harvest. This harvest
is the questions DMS actually asks today, which are logistics. CCA was built
for property / SEA / agriculture. The product log barely names those filters,
so the miss rate has n=1: a real example, not a ship number.

Do not retune `intent.py` to catch "Malaysian". A lexicon that chases its own
labels is how the last round reported 100 pct precision on a hand-picked set.

## Repro

```
python scripts/cca_engagement.py
```

Exit 0 reports. Exit 1 only if the labelled file is not a measurement
(harvested question dropped, lexicon source, no labeler, no why).

## Which invariant applies

**A control that blocks legitimate work is itself a failure**, and a control
that cannot be measured in both directions must not gate a customer ask.
Hard rule 10: the artefact this time is the two rates, not generated SQL.

## What was done

`tests/fixtures/cca_eval/engagement_labels.json`, `scripts/cca_engagement.py`,
`tests/test_cca_engagement.py`. Flag default remains 0. Ceiling to flip it:
both rates <= 5 pct, n>=40 ordinary, n>=8 filter-positive.

## What is NOT fixed

Deciding from free text whether a question carries a filter. Binding a term
to landed values was already solved. The flag stays off.
