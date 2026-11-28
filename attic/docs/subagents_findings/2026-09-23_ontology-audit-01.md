# ONTOLOGY-AUDIT-01 — include/exclude/unsure receipt (#235)

Date: 2026-09-23
Keywords: ONTOLOGY-AUDIT-01, audit_receipt, E13, include rows, exclude reasons, unsure, invent totals, dms-235
Main idea: Every ask envelope stamps include/exclude/unsure (or N/A with why) from executed rows and SQL filters. Invented totals demote. COMPLETE is illegal. Not #178 COMPLETE.

## What shipped

`build_answer_envelope` always writes `audit_receipt`:
- include = executed result rows (query grain); N/A when abstained or no SQL
- exclude = WHERE/HAVING/FILTER text, or N/A with why; caller COMPLETE rejected
- unsure = abstain | none

A stated figure that is not an include cell, same-row |a-b|, or full-column sum (every row numeric; missing is not zero) demotes. Constructor never pads include rows.

## Not this ticket

- #178 COMPLETE
- FreeRoute client / GEN-03 ask_path
- Live steward inspect of >=3 Studio asks (Platform leftover)
