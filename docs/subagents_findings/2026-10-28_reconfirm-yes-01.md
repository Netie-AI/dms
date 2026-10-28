# RECONFIRM-YES-01

Keywords: RECONFIRM-YES-01, DMS_RECONFIRM_RUN_HELD, def_validate, held SQL, grantable, yes:definition, chat-reconfirm-yes, chat-reconfirm-no, 9cbcb15, 1ad4e2c

Main idea: `DMS_RECONFIRM_RUN_HELD` defaults to 0. A def_validate reconfirm parks the SQL, the grantable set, the definition, and the reason code on the pending entry. Rows and secrets are not stored. With the flag on, Yes re-runs `serve_gap` and `validate_compiled_sql`, then `run_readonly` once. The answer sets `reconfirm_accepted` and provenance `yes:definition:<code>`, then `maybe_save_checked_sql`. Ungranted SQL at Yes time abstains and does not execute. Flag off, and any pending entry without held SQL, still re-asks. No still answers `not found in the database`. Chat renders the card above Details. Studio test ids stay on the result view.

Clean tree `9cbcb15`, branched from main `1ad4e2c`. `DMS_SKIP_CONTROL_PLANE_TESTS=1`, `DMS_DEMO_FALLBACK=0`: `3600 passed, 41 skipped, 33 xfailed`, exit 0, 1291.33s. n=348 not run. Not PASS.

Rebased onto main `9abbe9b6` (#476 AO-KEY-01). No code conflict. Clean tree `9666b0f`: `3603 passed, 41 skipped, 33 xfailed`, exit 0, 1246.22s. Same env. n=348 not run. Not PASS.
