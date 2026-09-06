# CCA engagement -- independent gold 2099 (HOLD)

**Keywords:** cca, engagement, false-engage, false-miss, spider, hostile, filter-positive, ordinary-bi, DMS_CCA_CASCADE

**Main idea:** Full independent harvest merge n=2099: false-engage 33/1723 (1.9%), false-miss 176/211 (83.4%). Flag stays off. Do not add country aliases.

## What this scored

`scripts/cca_engagement.py` `evaluate()` over `tests/fixtures/cca_eval/engagement_labels.json` plus all `.tmp/cca_label_batches/labels_*.json`. Deduped by casefold question. Does not POST `/v1/chat/ask`. Re-run: `python .tmp/split_ord_and_score.py`.

Includes: product 77, Spider validation 1034, filter-positive 247, hostile 250, ordinary leftovers 491 unique.

Still missing: Distil 50 (optional extra ordinary). Not wired into `harvest()`.

## Rates

| Bucket | n | Rate | Ceiling |
|--------|---|------|---------|
| ordinary | 1723 | 33 false-engage = **1.92%** | 5% |
| filter-positive (include-only) | 211 | 176 false-miss = **83.41%** | 5% |
| polarity | 131 | -- | -- |
| uncertain | 34 | -- | -- |
| shippable | -- | **false** | both rates + floors |

## Deltas

- Ordinary 493: FE 2.61% -> 2.11% (almost all true ordinary). 5 new engages (apartment type, "bought", HQ/Austin). 1 miss (Brazil).
- Spider 600-799 (200, ids 566-765): **0 false-engage**, 30 false-miss, 2 polarity, 6 uncertain, 162 ok ordinary. Labelled 34 geo-true. Retry overwrite marked Haiti OR-phone and CA/NY / US-territory as uncertain; those drop out of both rates. Pack still proposes none of the include geos.

## Do not do

- Do not flip `DMS_CCA_CASCADE` (default stays `0`).
- Do not add Haiti/Brazil/Asia/Africa aliases. That is lexicon chase.
- Do not retune `intent.py` for apartment / bought / headquarters.

Some Spider-600 trues are single-country lookups ("Which continent is Anguilla in?"). Independent gold kept them as geo row restrictions. They still miss. Do not "fix" that by expanding the pack.

## Next

Cue-window retune cannot hit both rates. F81 allows a different engagement approach inside EPIC-CCA. Distil 50 is leftover ordinary seed text only, not an ask-path model.
