# A2-06 scoped ontology refusals

Keywords: A2-06, load_verified_ontology, failed subject, fk_intact, business_key, dms-262

Main idea: `load_verified_ontology` kept returning None on any verify violation, so one failed claim unverified every object, link and measure. A2-06 keeps a caller-declared ontology and refuses the failed subject: a hop through a failed `fk_intact` link is a use (plan path or SQL that reads both relations); a failed object key cites grain, hop, destination, or SQL that reads its relation. Failed-link cardinality is never re-blessed. Compile uses a shallow copy so the caller ontology is never written. Missing `_violations` (set by `Ontology.verify` on `__dict__`) fail-closes the ask. Default demo ontology (onto is None) still returns None so BIRD/live SQL is unchanged.

Does not prove: live coverage, WRONG=0, EPIC-A2 COMPLETE.
