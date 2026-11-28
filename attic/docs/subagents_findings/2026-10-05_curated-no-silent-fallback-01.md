# CURATED-NO-SILENT-FALLBACK-01

Keywords: exact-match miss, pack-metric miss, grants fail, Cortex SQL fail, ledger fail, grantable_tables, cq_sku_count, dms-356

## Main idea

`cq_sku_count` is on the #355 score-pack allowlist, so that exact phrase is a pack match. A curated l0 ask that is still absent from the exact pack (base metrics plus that allowlist) abstains with `exact-match miss: pack-metric miss`, not a generic GEN-01 sentence.

A phrase that matches and then fails grants, Cortex SQL, or the ledger returns a named ABSTAIN for that step and does not continue into generative or the contract ask.

Offline `run_ab_curated` takes Space grants from `Executor.grantable_tables` on the A/B warehouse. It does not read the `DEMO_SPACE_GRANTS` dict.

Not COMPLETE. Does not close #356 or #257.
