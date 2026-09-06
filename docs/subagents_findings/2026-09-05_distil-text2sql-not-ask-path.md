# Distil Labs Text2SQL: harvest questions, do not wire the model

**Date:** 2026-09-05
**Keywords:** distil-labs, text2sql, CCA harvest, F28, DMS_CCA_CASCADE, dms#132
**Found by:** founder asked whether https://github.com/distil-labs/distil-text2sql and https://github.com/distil-labs/distil-example-text2sql-with-claude can be used

## Verdict

Usable as ~50 ordinary BI asks (plus a few named-country filters). Not usable as the DMS ask engine. Not a 1000-question independent log.

## What they are

Both repos are the same Distil Labs CSV->SQLite demo. Public git holds ~50 seed schema/question/SQL pairs (`finetuning/data/{train,test}.jsonl`). The Claude example wraps the same 50 with chatty prefixes. The claimed ~10k synthetic expansion is not in git (`distil-labs/text2sql-synthetic` on HuggingFace was gated here).

## Allowed

- Cite and harvest the NL `Question:` strings for CCA engagement ordinary-slice stress.
- Example geo-positive: "Show all customers from Canada."
- Cities, warehouses, departments stay ordinary under the CCA definition.

## Forbidden

- Do not put the Qwen3 Text2SQL model on `live_ask` (F28; Cortex is the only engine; 80 pct LLM-as-judge is 1-in-5 wrong SQL).
- Do not Distil-synthesize 10k variants of the 50 seeds and call that an independent customer log.
- Do not vendor Distil CLI or weights into this repo. Model card says Apache 2.0; the HuggingFace LICENSE file is a Distil R&D/commercial contract.

## Ceiling unchanged

Both rates <= 5 pct, n>=40 ordinary, n>=8 filter-positive. Distil does not get miss n to 8.
