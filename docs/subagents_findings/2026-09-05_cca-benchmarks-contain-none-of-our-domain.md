# 1232 public benchmark questions contain zero of the filters CCA gates

**Date:** 2026-09-05
**Keywords:** EPIC-CCA, CCA-05, engagement, independent labels, in-scope floor, F84, dms#148
**Corpus:** `tests/fixtures/cca_eval/engagement_labels_external.json` (1232), `_authored.json` (480),
`engagement_labels.json` (77)
**Scorer:** `python scripts/cca_engagement.py`

## Expected vs actual

**Expected.** The 77-question product log gave false-engage 0/76 and false-miss 1/1. A miss rate on
one case is an anecdote, so the ask was: get a real n from questions nobody wrote for this system,
label them blind, and find out whether the engagement rule is good enough to turn
`DMS_CCA_CASCADE` on.

**Actual.** The n arrived and the rate still cannot be computed, for a reason nobody had named:

| | independent (n=1284) | authored stress (n=374) |
|---|---|---|
| false-engage | **0.08 pct** (1/1237) | 1.18 pct (4/339) |
| false-miss | **100 pct** (47/47) | 48.6 pct (17/35) |
| filter-positives the packs actually claim | **0 of 47** | 1 of 35 |

All 47 independent filter-positives are **geo**. Not one of 1232 questions from five public
benchmarks names a lease-versus-buy tenure, a commercial-versus-residential property class, or an
industry sector. And 46 of the 47 geo filters name the United States, Italy, Spain, Aruba,
Afghanistan, Russia, Europe or North America - places the shipped pack, eleven Southeast Asian
states, never claimed.

So the headline 100 pct is close to meaningless as a verdict on the cue rule. It says the pack does
not cover the world, which was never in dispute and is written on the pack.

## Repro steps

1. `python scripts/cca_engagement.py` on this branch.
2. Read the `in-scope pos` line: `0 of 47`.
3. Read the per-origin table: every miss sits under WikiSQL, Spider, GeoQuery or IMDB, and every one
   of those rows is a country the pack never listed.

## Root-cause CLASS

**A benchmark can satisfy every floor a criterion names and still measure nothing, when the floors
count cases rather than cases-of-the-kind-under-test.**

The old criterion was: both rates <= 5 pct, n >= 40 ordinary, n >= 8 filter-positive. This corpus
clears the filter floor almost six times over and answers none of the question, because "a labelled
filter" and "a labelled filter this system claims to handle" are different populations and only the
second one is evidence. A corpus drawn from outside the product's domain will always fill the first
and can never fill the second.

The same shape appears in the earlier round of this epic: 30 golden cases built from 13 questions
gave 100 pct binder precision and told nobody anything about engagement. Different corpus, same
mistake - counting what is easy to count.

## What changed

`MIN_IN_SCOPE_FILTER = 8` is now part of `shippable`. A filter-positive counts as in scope when it
names a non-geo kind (the class, tenure and sector packs are not geographically bounded) or a geo
whose place the pack lists. Today that count is **0**, so the flag cannot flip no matter what the
two rates say. The floor is strictly harder than what it replaces; nothing was relaxed.

Nothing in `packages/executor/dms_executor/cca/` was touched. No alias was added for any country
that appears in these misses, and adding one would be exactly the retune-against-the-labels failure
this whole exercise exists to avoid.

## What this run DOES establish

**The false-engage failure does not reproduce outside property vocabulary.** The 46/106 figure that
put the hook behind a flag was measured on property-domain questions. Against 1237 ordinary
questions from real NL2SQL benchmarks and eight authored international business lanes, the cascade
engaged wrongly **once**:

    "Find suppliers with no purchase orders."   -> proposed sense

`purchase` is a strict alias and `no` is a prefix cue two tokens ahead, so the cue rule reads a
tenure filter in a procurement question. One in 1237. The other four false engages are all in the
authored stress set and all the same shape ("Malaysia Airlines account", "residential address",
"leasing agent", "purchase orders ... not on the approved list") - a proper noun or a job title
carrying a pack word.

That is a real result and it is worth having: the cue rule is quiet. It is quiet partly because it
recognises very little, which is the other half of the same coin and is precisely what the miss rate
would tell us if the corpus contained anything to miss.

## What it does NOT establish, stated so nobody quotes the good half

- **Nothing about the miss rate.** n(in-scope) = 0.
- **Nothing about polarity.** 6 cases hit the fail-closed polarity class and are counted apart.
- **Nothing about answers.** The scorer calls `engages` and `_proposals` in process. It never posts
  `/v1/chat/ask`, never starts the API, never reaches Cortex, and says nothing about badges, values
  or rendered text.
- **The 0.08 pct is not "the false-engage rate of DMS".** It is the rate on this corpus, whose
  register skews to well-formed annotator prose. Real users type worse. The authored stress lanes
  were built to probe that and score 1.18 pct, and they are not evidence either, for the opposite
  reason.

## Label quality, since the whole result rests on it

- 1712 questions, 43 blind labellers, 40 each. Every one confirmed it did not read the lexicon.
- **Cohen's kappa 0.831** on a 196-question double-labelled sample with a re-worded rubric and a
  different labeller who never saw the first labels; raw agreement 96.4 pct, 7 disagreements.
- Every one of the 189 positives was adversarially challenged: 177 upheld, 4 overturned, 8 left
  uncertain. 150 sampled negatives were hunted for missed positives: none found.
- A consistency pass grouped all 1712 by question shape and found 13 groups labelled against
  themselves, covering 59 labels. Those, plus everything an auditor could not settle, were marked
  **uncertain** rather than adjudicated by me. Uncertain cases are counted apart from both rates:
  an honest "we do not know" costs nothing, and the lexicon's author picking the side that scores
  better costs everything.

## Which invariant applies

**Don't claim "0 wrong" below n=300 (rule of three)** - and its converse, which this run is: do not
claim a rate at all when n of the relevant class is zero, however large the irrelevant class is.
Also **separate verified fact from assumption**: the 0.08 pct is measured, the miss rate is
undefined, and the table above says which is which.

## What would actually settle it

A corpus of questions that name the filters this product gates: Southeast Asian countries, property
class, lease versus buy, agricultural sectors. Public benchmarks do not contain them, and this run
is the evidence for that claim rather than an assertion of it. The only sources that would are:

1. a real DMS customer question log, once one exists;
2. a property or commodity industry question set from outside this repo, if one is public;
3. questions written by someone in the domain who has never seen this system.

Authoring them here is what tier `C_authored` already does, and its 48.6 pct miss rate is a hint,
not a number - the same people who know what is being measured wrote the exam. Until one of the
three above lands, the honest position is unchanged and now better evidenced: DMS binds terms to
landed data well, does not reliably know when a question is asking it to, and cannot currently be
measured on the second half at all.
