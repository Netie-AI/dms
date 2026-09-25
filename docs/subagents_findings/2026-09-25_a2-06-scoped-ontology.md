# A2-06 scoped ontology refusals

Keywords: A2-06, load_verified_ontology, failed subject, fk_intact, business_key, dms-262

Main idea: `load_verified_ontology` kept returning None on any verify violation, so one failed claim unverified every object, link and measure. A2-06 keeps a caller-declared ontology and refuses only the failed subject: a failed link when the measure/SQL fact is the child; a failed object key or business key when the measure is grained on that object or a path goes through it. Default demo ontology (onto is None) still returns None so BIRD/live SQL is unchanged.

Does not prove: live coverage, WRONG=0, EPIC-A2 COMPLETE.
