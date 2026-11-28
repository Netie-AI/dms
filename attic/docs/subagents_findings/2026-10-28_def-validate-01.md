# DEF-VALIDATE-01

Keywords: DEF-VALIDATE-01, DMS_DEF_VALIDATE, from_store, sqlglot, ao_vendor, reconfirm, provenance, validate_generated_sql, SERVING_DIALECT, 279cbd85, ba9afb3d

Main idea: `DMS_DEF_VALIDATE` defaults to 0. When it is 1, AI-generated SQL is checked with sqlglot against `Ontology.from_store` (tables, columns, join edges, measure expressions). The join read uses the vendored auto-ontology extractor. A pass executes. An undeclared join, measure drift, or a revoked item returns the #464 reconfirm envelope and shows the definition used. A PII-tagged column or an ungranted column abstains. Every answer on this path carries the ontology version id and the item ids. The function is `validate_generated_sql` so the local-gate name scan stays green. The dialect is `SERVING_DIALECT`, not a second literal. Nothing here classifies the question text. Pin `279cbd85` was not edited. Rebased onto main `ba9afb3d` (#475). PR base is `main`.

Clean tree `28935a2`, Python 3.12.3, `DMS_SKIP_CONTROL_PLANE_TESTS=1`, `DMS_DEMO_FALLBACK=0`: `1 failed, 3594 passed, 41 skipped, 33 xfailed`, exit 1, 1493.14s. The failure is `tests/test_name_echo_01.py::test_sixty_ungranted_names_scrub_under_200ms` at 0.241s against a 0.2s bound. That test alone passed in 0.54s. n=348 not run. Not PASS.
