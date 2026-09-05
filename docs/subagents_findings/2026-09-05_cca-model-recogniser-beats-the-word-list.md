# A model recogniser takes the miss rate from 78 pct to zero, and the flag still cannot flip

**Date:** 2026-09-05
**Keywords:** EPIC-CCA, CCA-05, proposer, ModelProviderPort, in-scope floor, F84, dms#155
**Artifacts:** `packages/executor/dms_executor/cca/proposer.py`,
`scripts/cca_proposer_bench.py`, `tests/fixtures/cca_eval/proposer_trial_model.jsonl`

## Expected vs actual

**Expected.** The founder asked whether any of the cascade uses AI, and said that without one it
stays a regex. The check: no. `ModelProviderPort` is declared in `dms_core/ports.py` as one of the
five sanctioned ports and has **zero implementations**; Cortex serves six routes and none of them
classifies; `cascade._proposals` was a frozenset and a two-token window. The epic charter had said
so from the start - `cca/__init__.py` opens "An LLM may *propose* that SEA means eleven countries".
The word list was the placeholder.

**Actual.** Putting a model behind the same interface fixes the half that was broken, does not
damage the half that worked, and **does not move the ship criterion at all**. The last part is the
one worth writing down.

545 labelled questions, the shipped system prompt, the same judge and the same labels:

| | false engage | false miss | in-scope positives |
|---|---|---|---|
| word list | 0.28 pct (1/357) | **78.05 pct** (64/82) | 25 |
| model | 0.28 pct (1/357) | **0.00 pct** (0/82) | 25 |

Restricted to the corpora that may move the flag (tier A external plus the product log, 338 cases):

| | false engage | false miss | in-scope positives | shippable |
|---|---|---|---|---|
| word list | 0.00 pct (0/278) | 100 pct (47/47) | **0** | False |
| model | 0.36 pct (1/278) | 0.00 pct (0/47) | **0** | **False** |

The model caught all 82 named filters. It bought that with exactly one extra false engage, on a
genuinely hard case: *"documents that contain the paragraph text 'Brazil' and 'Ireland'"*, where the
country names are literal strings inside documents rather than a place the business operates in.

## What the word list was missing, in the customer's own words

Sixty-four filters. A representative dozen:

    total outbound sales value in MYR for goods from Malaysian suppliers      geo
    how much of the current order book comes from automotive customers        segment
    lead time from Turkey has been drifting for months                        geo
    OEE for the German Werke this quarter, plant by plant                     geo
    our pharma customers in Ireland, what is OTIF looking like                segment + geo
    how much of our AR is sitting with oil and gas clients right now          segment
    For the retail tenants in our commercial properties, arrears by ...       asset_class + segment
    Revenue from our FMCG customers in Singapore last year                    geo + segment
    EMEA wide, how many CE marked product families are we shipping            geo
    Sub-Saharan Africa arrears converted to USD, top 20 accounts              geo

Every one of these is an ordinary thing a person types. None is exotic phrasing. The word list saw
none of them, and under the old `(no recognised term)` label the trace would have gone green over
each.

## Root-cause CLASS

**Reading comprehension was implemented as string matching, and the two do not degrade alike.**

A term list fails silently and asymmetrically: it is nearly perfect on the questions it was written
against and blind everywhere else, and the blindness reads, from every dashboard, exactly like "this
question carried no filter". Two rounds of tuning cue windows traded one error rate for the other
without closing either, because the missing capability was never a wider list.

## The part that did not change, which is the point

`shippable` is False for the model too, and for the same reason it was False for the word list:
**zero of the independent filter-positives name something the shipped packs claim.** All 47 are geo,
46 of them the United States, Italy, Aruba, Afghanistan or Europe. A recogniser that reads perfectly
still cannot certify a member the pack never listed, and `MIN_IN_SCOPE_FILTER = 8` exists precisely
so that a good recogniser cannot be mistaken for a ready system.

The 25 in-scope positives in the 545-case run are all tier `C_authored`, written in the same session
as the measurement. They are printed and they are barred from moving the flag.

So the honest reading is three separate statements, and quoting any one alone is a misrepresentation:

1. the recogniser was the broken half, and a model fixes it;
2. the certifier was the working half, and it is untouched;
3. the corpus still contains none of the filters this product gates, so nothing is ready to default on.

## Which invariant applies

**Separate verified fact from assumption, explicitly.** The 0 pct miss is measured on 545 labelled
questions. It is *not* measured on the shipped API path: this trial ran the production prompt through
the agent harness, which is the same model family over a different transport with different effort
settings. `scripts/cca_proposer_bench.py --proposer anthropic` is the real measurement and needs
`ANTHROPIC_API_KEY`, which this environment does not have. Treat 0 pct as the ceiling that path
should approach, not as its number.

Also **a skipped test is a failing test**, inverted: rather than leave the claim unverifiable, the
545 model proposals are committed as `proposer_trial_model.jsonl` and replayed by
`tests/test_cca_proposer.py`, so the comparison is re-runnable by anyone for free.

## What would settle the remaining question

The same three things as before, unchanged by any of this: a real customer question log, a public
industry question set, or questions from a domain expert who has never seen this system. A model
brain does not manufacture in-scope filters, and no amount of recogniser quality substitutes for a
corpus that contains the thing being recognised.

## Cost, so the decision has a number attached

Per proposal on Opus 5 with the system prompt cached: roughly 1.6k cached input plus ~40 question
tokens in, ~120 out. That is well under a cent, and `cca_proposer_bench.py` prices any run from real
`usage` and refuses to spend without `--yes`. Latency is the real cost on the ask path, not money.
Sonnet 5 and Haiku 4.5 are benchable with one flag each; on this evidence the question is whether a
cheaper tier holds the 0 pct miss without buying it back in false engages, and that is a measurement,
not a guess.
