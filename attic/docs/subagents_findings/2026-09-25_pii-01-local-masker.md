# PII-01 local masker (dms#272)

Keywords: pii, mask, nric, email, encodings, xlsx_export, DMSMASK, cortex-268, dms-272
Main idea: Drop or mask personal-data column samples before Cortex generate context, and mask answer/export cells with stable non-PII-shaped `DMSMASK_*` tokens. Fail closed. GEN-RESTORE path: retrieve ctx is compute_insights ontology then Insights POST body["ontology"]. Live both-maskers run with Cortex #268 is leftover.

## Swap (hard rule 6)

Not a sixth port. Isolated in `dms_core/pii.py`. Swap: Cortex HTTP PII-MASK (#268) or a DLP API behind the same functions.

## Ceiling

Names are column-name only (no NER). A free-text notes column of person names can still leak until Cortex #268 NER sits on FreeRoute.
