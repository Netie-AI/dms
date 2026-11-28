# ONTOLOGY-AUDIT-01-FLOOR — INVARIANT-CHANGE trailer for #235 squash (#252)

Date: 2026-09-23
Keywords: ONTOLOGY-AUDIT-01-FLOOR, INVARIANT-CHANGE, protected-paths, audit_receipt, E13, dms-252, dc752563
Main idea: #235 squash `dc752563` touched tests/invariants/test_envelope.py without INVARIANT-CHANGE in the squash body. Floor cure is a new commit that declares include/exclude/unsure E13 with the trailer. Ticket squash must keep the trailer. Not #178 COMPLETE.

## What shipped

E13 gate now fails if `audit_receipt` is omitted, if include/exclude/unsure is omitted, or if any arm stamps COMPLETE. WRONG=0 unchanged.

## Not this ticket

- #178 COMPLETE / bar (1) PASS
- #238 / #194 / #189 product work
- Re-YES of broken oid `dc752563`
