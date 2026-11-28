# BIRD-JOIN-GATE-01 / BIRD-FK-ORPHANS-01

PREFLIGHT: PARTIAL. Join-hole, compile-check, and capped-parent-orphan findings were on file. None covered a declared FK on a table with no primary key, or an orphan ratio the steward sets.

`DMS_JOIN_DECLARED_FK` defaults off. Only the digit 1 turns it on. Off, derive still drops a foreign key whose child has no primary key, and a join-rule refusal still abstains.

On, derive keeps that child as an object with an empty key when the parent is already an object. There is no row-identity claim. Verification is the existing measured parent-uniqueness and fk check, not the primary key. No table-name or column-name match.

A refusal whose code is parent_rows_repeat, fan_trap, join_kind, or subquery_shape stops the extract loop and asks OpenVault once. The body sends role `sql` and the named refusal. It does not send a provider or a model id. The repaired SQL goes back through `validated_sql_reason`, including the join rule. A second refusal is a typed reconfirm that names the two tables from the SQL tree. The suggested question is non-empty.

`DMS_FK_ORPHAN_DISCLOSE` defaults off. Off, any orphan still fails `fk_intact`, and a stored ratio is not read. On, the steward ratio for the Space starts at 0, so a dirty link still refuses until someone sets a ratio. The PUT records the server actor, not a request field. Below that ratio, and only when the parent column is unique, the link is `verified_with_orphans` and usable. The envelope `audit_receipt.exclude` names the orphan count. At or above the ratio, the ask reconfirms with the measured counts (`fk_orphan_hold`). Nothing is approved automatically. This change sets no default above 0.

Unit file `tests/test_bird_join_gate_01.py`: 12 passed. Word-rule ceiling and boundary invariants stayed green. Synthetic schemas only. The BIRD corpus was not read.

The spaces route records the ratio through `dms_core`. It does not import the executor.

Stub 52, `grade52 --main`, rebased onto `05bc1e7`. Flag off: correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20 stamp_contradiction=0. Both flags on: the same counts. Rule-served answers did not grow. Armed grade52 was not run. Not PASS.
