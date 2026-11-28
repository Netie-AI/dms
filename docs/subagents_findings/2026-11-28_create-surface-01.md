# DMS-CREATE-01

**Status:** Not PASS. Unit file `tests/test_create_surface_01.py` plus `tests/invariants/test_boundaries.py` passed (26). Armed grade52 not run. Tokens and cost not measured. Not a score.

PREFLIGHT: PARTIAL. ADJUST-SQL-VQ-01 already stores steward SQL that lookup does not replay. STREAM-STAGES-01 already streams ladder stages behind a flag. CHART-SPEC-01 already asks OpenVault with a role string.

## What

`DMS_CREATE` defaults off. On, Studio can create an app for a Space. The caller sends a DMS API key and an OpenVault key. DMS sends `create_plan`, then `create_generate` for one component at a time. It does not send a model, a provider, or `strict`.

One cheap plan returns intent, an outline, and an estimate. The user confirms once, then every component is built. A failed component is retried once. OpenVault 402 stops the run. The runaway guard is 60 steps or `DMS_CREATE_MAX_USD` (default 10) and it says why. Twice the estimate warns and the build continues. The model gets the schema, SQL aggregates, and a sample of at most 20 rows. Doc and slides raise `kind_required`.

## Review

A missing usage field is null, not 0. The plan estimate is stored beside the log and is not written as measured cost. Stub orders lake: seconds=0.353159, tokens=null, cost=null. No live OpenVault key. The old 8-step, 8000-token, and $1 stops are not applied.

## Not a word rule

No phrase list. Ungranted and destructive SQL use the existing abstain. An uncited number is withheld. `create_accept` is stored and is not replayed.

## Not PASS

grade52 was not re-run. No live OpenVault key, so tokens and cost were not measured. The Studio click path was not exercised in a browser.
