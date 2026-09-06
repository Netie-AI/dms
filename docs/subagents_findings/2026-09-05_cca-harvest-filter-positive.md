# CCA harvest: filter-positive public questions

Keywords: cca, engagement, harvest, filter-positive, geo, CRE, agriculture

main_idea: independently sourced filter-positive asks so false-miss is a rate not an anecdote.

Harvested **247** unique questions on 2026-09-05.

## Source mix

- BIRD text-to-SQL benchmark (HuggingFace viewer extracts) -- country/region/segment row filters in NL questions
- CRE/residential lease FAQs: Savills, Knight Frank, ClickBina, My Office Space, CBRE, LeaseLens
- Agriculture/trade: USDA GATS exercises, OpenDOSM, ASEANstats, BMEL, FAO/World Bank microdata, WTO MTN
- Investor/REIT: TIAA Real Estate Account FAQ (SEC), Nuveen global cities, REIT ETF prospectus
- Earnings Q&A: IHH Healthcare transcripts (Malaysia/Singapore/India/Turkey filters)

## Label guesses (not gold)

- kind_guess counts: {'geo': 192, 'sense': 38, 'asset_class': 15, 'segment': 2}
- polarity_guess counts: {'include': 205, 'exclude': 42}

## Output

- JSON: `d:\DMS\.tmp\cca_harvest_filter_positive.json`

## Notes

- Questions transcribed from public pages only; no CCA alias/corpus reads.
- `kind_guess` is **not gold**. BIRD prefix is contaminated: EUR/CZK is currency, SME/LAM/KAM is a tariff segment, not a country. Independent labeler must ignore guesses.
- `group by country` style asks omitted.
- Attribute-lookup asks (e.g. class of each row) omitted where detected.
