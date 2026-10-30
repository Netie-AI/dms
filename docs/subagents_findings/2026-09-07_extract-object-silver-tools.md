---
keywords: [goal, extract, object-views, silver, metric-tools, isolation, human-columns, extracted_at, DR-0005]
main_idea: Extract to bronze, publish objects.* with human column names, arrange silver (not gold), register count tool, tools-only abstain in that Space. UTF-16 StrReplace trap remains.
---

# Extract -> object -> silver -> certified tool (2026-09-07)

PREFLIGHT: HIT lean-ontology + F87. COPY-none of DB-GPT UI/AWEL. Gold stays signed promote.

## What landed

- Studio maps source columns to human names (not identity OLTP).
- POST /v1/studio/objects publishes objects.<name> and materializes silver.<name>. Does not create gold.
- Space with a registered tool: unmatched asks abstain; Cortex is not called. Company-default without extras still can.
- Studio shows extracted_at on the extract receipt (re-extract watermark, not CDC/ATTACH).

## Tests

77 passed: 	est_extract_object_ask, 	est_object_views, 	est_metric_tools, extract-only, sql source, db_connector, ask_mapping, library_promote.

## Trap

Cursor StrReplace wrote UTF-16 on object_views.py and the two new/edited tests. Decode utf-16, write_text(..., encoding="utf-8", newline="\n").

## Not done (goal stays open)

- Live Chat #116: founder unseals OpenVault. Agent cannot.
- CDC/ATTACH of customer engines: refused (DR-0005). Streaming = re-extract + watermark.
- DB-GPT product/UI/AWEL, DeepSeek Harness, Semantica RDF.
