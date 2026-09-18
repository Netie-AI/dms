# INSIGHTS-HOST-01

Keywords: INSIGHTS-HOST-01, /v1/insights, OpenVault, founder key, dms-196, cortex-213
Main idea: Hosted DMS consumes Cortex GET|POST /v1/insights via configured OV founder key. Fail closed. No LIVE_KEY invent. No :5000 green. Live walk is Platform. Not COMPLETE.

Gate: Cortex missing or unreachable -> 503 REFUSE values=[]. generate=true and OV down -> 503. Envelope values only from Cortex JSON.
