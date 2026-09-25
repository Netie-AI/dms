# PII-MASK-CHECK-01 free-text + NANP/intl (dms#303)

Keywords: pii, mask, free-text, NANP, phone_intl, dob, flagged_columns, dms-303, dms-272
Main idea: Widen dms#272 so email/phone/card/dob shapes inside free text are dropped from retrieve sampling and masked on export, and add NANP plus spaced international phones. Per-column checker uses synthetic values against the counts-only BIRD scan fixture. Does not lift the BIRD exclusion.

## Swap (hard rule 6)

Same as PII-01: isolated in `dms_core/pii.py`. Swap: Cortex HTTP PII-MASK (#268) or a DLP API behind the same functions.

## Ceiling

Names stay column-name only (no NER). Passport / address / location scan cells can FAIL closed. In-text DOB years are 1900-2019 so 2026 as_of stamps in prose stay. Upgrade: Cortex #268 NER.
