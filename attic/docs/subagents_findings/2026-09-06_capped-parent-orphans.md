---
keywords: [F-0046, SQLSRC-07, dms-157, verify, max_rows, orphan, fk_intact, truncated, EPIC-020]
main_idea: verify() had no referential-integrity claim, so a max_rows cap on a parent invented orphans that LEFT JOIN misattributed while the grand total still reconciled. The claim is now fk_intact; truncated is threaded through the manifest, not the bronze registry.
---

# Capped parent invents orphans verify() must refuse

**Date:** 2026-09-06
**Branch:** `cursor/sqlsrc-08-then-07-2eff`
**Tickets:** Netie-AI/dms#157 (this), #156 (the path), #116 (certifying run)

PREFLIGHT: HIT on `2026-09-06_sqlsrc-08-bridge-landed.md`. The path exists;
this finding is the missing claim.

## The shape

Named regions understated by half, grand total exact, compiler note
"verified many-to-one, so no fact row is duplicated". LEFT JOIN is not the
bug - it converts a visible shortfall into invisible misattribution only
because nothing upstream said the extract was partial.

`validate_lake.fk_intact` already counted orphans on the parquet lake path.
The sql_source lane never called it. Lifted into `Ontology.verify()`, not
called from the connector.

## What landed

- `fk_intact` in `dms_executor.ontology.Ontology.verify`.
- `truncated` on `manifest_entry` tables, copied by `from_manifest`. No bronze
  registry read (ticket split condition not fired).
- Receipt via existing `verify_source_links` (#156). Connector still has no
  DuckDB handle.

## Do not

- Do not report EPIC-020 COMPLETE. #116 is the live certifying run (R-0003).
- Do not expand to SQLSRC-09 #158.
- Do not revert LEFT JOIN to INNER.
- Do not claim `POST /v1/chat/ask` consults the ontology.
