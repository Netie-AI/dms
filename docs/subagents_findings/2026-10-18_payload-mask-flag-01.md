# PAYLOAD-MASK-FLAG-01

PREFLIGHT: PARTIAL. Index hits are PII masking of answers and scores, not AskPayloadColumn.masked.

Keywords: PAYLOAD-MASK-FLAG-01, AskPayloadColumn.masked, dms-456, Cortex#386, contract 1.9.0, extra ignore

## Main idea

DMS sets `masked` on each ask-payload column from its own mask and PII flags. The wire value is a bool. True for a masked, PII, unread, or unknown column. False only when the column is positively cleared. Cortex main (contract 1.7.0) `AskPayloadColumn` has no such field and no `extra=forbid`, so a bool is ignored and does not 422. `/health` and `/health/features` do not advertise the contract version, so DMS sends the field. Sample cells are not copied onto the payload and are not logged. Not COMPLETE. Pairs with Cortex#386. Nothing PASS.
