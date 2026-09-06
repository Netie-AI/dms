# CCA harvest -- ordinary BI asks (false-engage stress)

**Keywords:** cca, engagement, harvest, ordinary, spider, tpc, bird, wikisql, powerbi, northwind

**Main idea:** Independently sourced ordinary BI asks for false-engage measurement. 9,435 unique after lowercase+strip from public corpora; top 500 ranked exported for the stress set.

## Sources (all public, cited per row)

| Source | URL | Raw kept | Notes |
|--------|-----|----------|-------|
| Spider train+dev | https://huggingface.co/datasets/xlangai/spider | 7,883 | Academic text-to-SQL; cross-domain |
| BIRD dev | https://huggingface.co/datasets/birdsql/bird_sql_dev_20251106 | 1,489 | Realistic BI questions |
| TPC-H business questions | https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-h_v2.17.1.pdf | 12 | Rephrased from official spec |
| Power BI Q&A / Copilot | https://github.com/MicrosoftDocs/powerbi-docs/.../q-and-a-intro.md | 8 | Manager-style NL |
| Northwind / LearnSQL | https://learnsql.com/blog/sql-exercises-northwind/ | 10 | Exercise titles -> NL |
| SageNLP Northwind demo | https://sagenlp.com/nlpDb.htm | 4 | Product/stock asks |
| WMS / logistics FAQ | metaoption, modernmaterialshandling, grexpro, traceconsultants | 15 | KPI-style manager asks |
| Northwind AI assistant | https://github.com/cesar39299/Northwind_AI_Web_Assistant | 3 | Example chat asks |

WikiSQL dev.jsonl CDN returned 404 on 2026-09-05; skipped. Train split not attempted.

## Exclusion rules applied (no lexicon read)

Dropped when question text matches:

- Country/region row filters (`in Germany`, `sales in the USA`, TPC nation placeholders)
- Country-as-subject totals (`GDP of Malaysia`)
- Property tenure/class (lease, rent, commercial vs residential)
- Agriculture/plantation/livestock sector filters

**Kept:** city, SKU, warehouse code, supplier id, product category, date range, customer name; `group by country` / `rank by country`.

## Output artifacts

| File | Count |
|------|-------|
| `D:\DMS\.tmp\cca_ordinary_harvest.json` | 500 (ranked best) |
| `D:\DMS\.tmp\cca_ordinary_harvest_meta.json` | harvest stats |
| `D:\DMS\.tmp\harvest_cca_ordinary_questions.py` | one-shot harvester (not product code; do not move into `scripts/`) |

Domain mix in top 500: retail 322, finance 98, hr 69, logistics 11, other 0.

## Spot-check

Automated post-pass on exported 500: 0 questions matched obvious country-filter or tenure/sector leak patterns.

## Re-run

```powershell
Set-Location D:\DMS
python .tmp\harvest_cca_ordinary_questions.py
```

## Caveats

- Spider/BIRD questions are dataset-native; some are academic (concerts, pets). Ranking is a harvester prefilter, not gold.
- Ranking favours retail/finance/hr keywords; logistics under-represented in top 500 despite curated WMS block.
- The exclusion regex missed at least `ord-099` ("Tokyo or Taiwan"): Taiwan is a named country but `\bin Taiwan\b` did not fire. Do not treat the ranked 500 as `carries_filter=false`.
- Dedupe vs independently labelled Spider validation (834 unique): 7 overlap, 493 leftovers in `.tmp/cca_label_batches/ord_new.json` (434 Spider train, 20 BIRD, 39 other). Independent labels are required before `harvest()` loads this sidecar.
