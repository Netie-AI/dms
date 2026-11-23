# ONTO-PROFILE-03

PREFLIGHT: PARTIAL. No KB in this container (`D:\Netie-KB` absent). In this repo `2026-11-13_onto-profile-01.md` covers the routes this screen calls; nothing covered a steward screen.

Keywords: ONTO-PROFILE-03, dms#516, OntologyReviewPage, ontologyReview.ts, steward review, approve one item per press, stale answer, key per Space, same-tick double press, error text passthrough, cross-Space table takeover, cortex-contract wheel, gate stand-in

## Main idea

The steward review screen is `apps/ui/src/pages/OntologyReviewPage.tsx` with its calls and sentences in `apps/ui/src/lib/ontologyReview.ts`. A status on screen is only ever the item the API returned for that press.

## Findings (expected vs actual, repro, class, invariant)

1. **A late answer landed on another Space's page.** Expected: a Measure answer for Space A never shows under Space B. Actual (first build): hold `POST /v1/ontology/data/profile` for A, navigate in-app to B, release: B showed A's 8 items with live Approve buttons. Class: async answer applied to state that outlived its request. Fix: one component instance per Space (`key={spaceId}`), so a late answer lands on an instance that is gone. Invariant: fix the root-cause class (one fix covers measure, approve, reject and the 403 path). Found by the checker, not the author (adversary is not verifier).
2. **Two presses in one tick sent two requests.** Expected: one press, one request. Actual: the guard was React state, read from a stale closure; `el.click(); el.click()` sent two revokes. Fix: a ref. Class: guard on render state instead of a ref.
3. **API error text was echoed.** Expected: no data value and never the words "verified" or "certified". Actual: any `detail` string and any unknown status were rendered. Fix: the page shows the HTTP status number and its own words only; an answer that is not the pressed item with a known status changes nothing. Class: passing another system's text through a surface that makes its own claims.
4. **The first spec could not see findings 1 to 3.** Its leak regex skipped a value followed by a full stop, the cream theme assertion was `/.*/`, and the request log only watched `/v1/ontology/data/`. Invariant: prove a gate can fail before trusting it green. Nine mutations now turn the unit or e2e tests red.

## Not this ticket, seen on the way

- **Cross-Space table takeover.** Uploading same-named files into Space B re-lands `bronze.customers` etc. for B, and Space A then answers 409 `no_file_tables` while still holding its 8 stored proposals. Repro: two Spaces, `POST /v1/studio/ingest-batch` the same five CSV files into each, then `POST /v1/ontology/data/profile` for the first. Class: a global table name owned by the last writer. Not routed yet: goes to the prd-agent.
- **The e2e run here was not against the real wheel or real Cortex.** Attaching Netie-AI/Cortex was denied in the session, so `cortex_contract` was a scratchpad import shim (names only, canonicalisation raises) and the F5 gate was a local stand-in that answers "allowed". The DMS API code was the real one. Invariant: separate verified fact from assumption.
- `persisted:true` (Postgres item store) was never exercised: the notice's absent branch is asserted but did not run.
