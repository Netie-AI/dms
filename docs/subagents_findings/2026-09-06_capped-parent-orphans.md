---
keywords: [F-0046, ontology, verify, max_rows, orphan, referential-integrity, EPIC-020, dms-116, R-0001, R-0004]
main_idea: A max_rows cap on a parent table invents orphans the source never had. verify() has no referential-integrity check at all, so the ontology verifies clean, LEFT JOIN buckets every orphan under NULL, and each named group is understated while the grand total reconciles. Repro exits 1.
---

# A max_rows cap invents orphans, and verify() has no check that would see them

**Date:** 2026-09-06
**Repro:** `python scripts/repro_capped_parent_orphans.py` (exit 1 = reproduced)

PREFLIGHT: PARTIAL. `2026-09-03_epic020-f0019-worktree.md` covers the EPIC-020
lane; three INDEX rows cover ontology generally. Nothing covered this class.
Root cause class: **a completeness assumption that no check states.**
Invariants: **R-0004** (fix the class, not the symptom), **R-0001** (assert the
artifact the customer receives), CLAUDE.md hard rule 12.

## Golden rule

> A semantic layer that measures only key uniqueness and parent-side cardinality
> is complete **only if the extract is complete**. Nothing in DMS says so. The
> moment an extractor caps tables independently, "this key identifies one row"
> stays true while "every child has its parent" quietly stops being true, and no
> declared check is looking at the second one. Row-count caps and referential
> integrity are the same subject; a layer that knows about one and not the other
> will certify the join it should refuse.

## Expected vs actual

**Expected:** an extract that cut 100 of 1100 parent rows either refuses the
join, or answers with a stated caveat naming what it could not prove.

**Actual:** `verify()` returns `[]`. `compile()` succeeds and attaches the note
"joined customer through order_customer (verified many-to-one, so no fact row is
duplicated)" - true, and about a different hazard than the one present. The
answer:

```
region        answered    source truth
East            200.00          400.00  <-- WRONG
North           200.00          400.00  <-- WRONG
South           200.00          400.00  <-- WRONG
West            200.00          400.00  <-- WRONG
NULL            800.00               -   <-- orphans, in no region

total answered 1,600.00 == source total 1,600.00: True
```

Every named region is understated by half, and the grand total reconciles
exactly. Total-equals-source is the check a reviewer reaches for first, so the
one number that is right is the one that hides the four that are wrong. There is
no abstention and no badge demotion anywhere in this path.

## Root cause, at the binding

`Ontology.verify()` (`scripts/ontology.py:252`) states its own scope in its
docstring: `key_unique`, `key_not_null`, `link_cardinality`. The link loop reads
the child side exactly once, and only to prove the declared columns exist:

```
child_cols = ", ".join(_ident(c) for c in link.from_columns)
con.execute(f"SELECT {child_cols} FROM {child.relation} LIMIT 0")   # ~:311
```

Referential integrity is never measured. There is no `Violation` class for it.
Uniqueness is measured over the parent only (`:307-359`).

The compiler emits `LEFT JOIN` deliberately (`:636`) - "an inner join silently
drops fact rows whose key is absent from the dimension, shrinking the measure
without anything looking wrong". That reasoning is right, and it converts a
visible shortfall into an invisible misattribution: the orphans survive into a
NULL group instead of vanishing.

The cap that creates the orphans is
`packages/executor/dms_executor/db_connector.py:436`
(`truncated = len(fetched) > max_rows`). `truncated` reaches the receipt, the
preview and the Library tree. It does not reach the ontology, so the layer that
would have to act on it never learns the extract was partial.

## Live evidence, independent of this repro

Netie-AI/dms#116 (SQLSRC-06, verify run 2026-09-05, R-0003 different run):
SQL Server `sales`, 1100 customers and 160 orders with 80 on C1021-C1100,
pulled at a cap landing 1000 customers. `verify()` said nothing about the 80
orphans. The ticket's acceptance text anticipated the outcome in advance -
"if it says nothing, that is a finding, not a pass".

## Which invariant should have caught it

None existed. `tests/test_db_connector.py` runs entirely on a `_FakeConnection`
(`:82-113`), so no test in the repo has ever pulled a table that was capped
while its child was not. The ontology suite declares its fixtures whole. The
gap is structural: both halves are tested, the seam between them is not - the
same shape as the STATUS "Engine bench" row, where every declared key is correct
by construction and so the four failure classes that decide customer viability
cannot occur in the corpus.

## Do not

- Do not fix this from inside a verify run. #116 is a verify ticket and its own
  text forbids it; routed to the PRD Agent instead (CLAUDE.md routing rule).
- Do not "fix" it by switching `LEFT JOIN` to `INNER JOIN`. That trades a wrong
  part for a wrong total and loses the protection at `:636` was written for.
- Do not treat the NULL bucket as the disclosure. A group the customer did not
  ask about, carrying no explanation, is not a refusal.
- Do not claim a rate from this. n=1 shape, R-0010.
