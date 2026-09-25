# Climb metric ceiling (curated_ceo ontology_plan count vs unseen-schema accuracy)

Keywords: GEN-PATH-CLIMB, ontology_plan, curated_ceo, certified leftover, BIRD, Mini-Dev, unseen schema, DB-GPT, 100 percent, rule of three, EPIC-INSIGHTS-UX, dms-178
Main idea: Founder asked for "DB-GPT level, 100% accuracy". The only live number (ontology_plan=39/49 WRONG=0, #228 RISE_PASS; 36/46 at #226) grows 3 per tick by unioning paraphrases of SQL Cortex already certifies, on one ~91-row warehouse. CLIMB-12 notes certified primaries+synonyms are already POSTed, so restatement sources are nearly used up. The count cannot measure generalisation. Routed as a PRD amendment brief (BIRD-class gates G1-G5), not built.

Expected: headline accuracy metric moves when DMS answers questions it was not written to match.
Actual: metric moves only when certified SQL is restated as new pack questions; BIRD Mini-Dev harness (#184) has no recorded live counts.
Repro: `git log --oneline -8` on main shows CLIMB-05..12 each adding 3 `*_RISE_L0` rows to `scripts/score_curated.py`; `scripts/score_climb.md` CLIMB-12 section says primaries+synonyms are "already POSTed".
Root-cause class: gate asserts an intermediate artifact (pack count on a self-authored pack) instead of the outcome (accuracy on an unseen schema).
Invariant: "Assert the artifact the user/customer actually receives" + "Don't claim 0 wrong below n=300".
Verified: climb pattern, n values, already-POSTed note (read from repo + issues; independently re-checked by a separate PRD Agent run). Assumption: ontology_plan answers are overlay-compiled certified SQL, not model-generated (CLIMB-12 note says unarmed FreeRoute cannot emit generate SQL).
Ceiling: this seat did not build CLIMB-13 (Studio unreachable; #231 seated elsewhere, PR #240). PRD Agent routed the ask as a PRD-001 amendment (draft EPIC-A1..A3), NEEDS-YOU. GEN-03 #194 closed via #246.
