---
keywords: [EPIC-020, R-0001, ontology, from_manifest, studio, sql_source, bridge, unreachable, dms-116, F-0046]
main_idea: EPIC-020's "understand and refuse" half is not on the customer route at all. sql_source_ingest builds the exact manifest from_manifest consumes and returns it as JSON; nothing under apps/ or packages/ imports from_manifest, so verify() never runs on an extract. Fixing F-0046 alone changes nothing a customer sees.
---

# The ontology is built, handed over, and never called

**Date:** 2026-09-06
**Confirmed by:** direct read of HEAD `27bf687b2`, after a PRD Agent routing run
raised it. Reproduced here rather than taken on report (R-0003).

PREFLIGHT: PARTIAL. Extends `2026-09-06_capped-parent-orphans.md` (F-0046) - same
epic, different and larger root-cause class.
Root cause class: **a verification layer that no customer path reaches.**
Invariant: **R-0001** - the gate asserts the artifact the customer receives, at
the layer they receive it from.

## Golden rule

> Building the input a checker consumes is not the same as running the checker.
> `sql_source_ingest` assembles `manifest_entry` - the exact shape
> `from_manifest` was written to eat - puts it in the receipt, and returns. The
> seam is wired at one end and connected to nothing at the other. A layer that
> is only ever driven by a bench script is an intermediate artifact, and a gate
> on it certifies nothing about the product.

## Expected vs actual

**Expected:** EPIC-020 acceptance clause 2 - "read the source's declared primary
and foreign keys from its own catalog, compile them into an ontology, measure
every link's cardinality against the landed rows, and refuse - naming the link -
any link the source declares that the data violates."

**Actual:** the first two verbs happen, the last two never do, on any path a
customer can reach.

```
POST /v1/studio/sources/sql
  -> apps/api/dms_api/routes/studio.py:270-297
       compliance_gate(...) ; enforce(decision)
       return sql_source_ingest(...)          <- and that is the whole body
  -> packages/executor/dms_executor/db_connector.py:592-599
       return SourceExtract(..., manifest_entry=keys.manifest_entry(...), ...)
```

`manifest_entry` is documented at `db_connector.py:145` as "the shape
`scripts/ontology.py:from_manifest` consumes". It is built, returned to the
caller as JSON, and dropped.

Every importer of `from_manifest` in the repo:

```
scripts/insights.py:199
scripts/ontology_bench.py:663
scripts/ontology.py:1124        (its own __main__)
```

Nothing under `apps/` or `packages/`. `verify()` has never run against a
customer extract.

## Why this outranks F-0046

F-0046 says `verify()` lacks a referential-integrity claim. True, and worth
fixing. But adding the claim changes nothing a customer sees, because the
function it would be added to is not called on their path. Fixed in the order
found, the repo would gain a correct refusal that no route emits.

The two are one epic and should be sequenced bridge-first, or at least
bridge-together. STATUS already records the honest version of this shape for the
neighbouring lane - "AW lake ... **Never read by `apps/` or `packages/`**" - and
the same sentence is now true of the semantic layer itself.

The bench numbers are unaffected and stay true of what they measure: 896 cases
over 494 shapes, 811 answerable, 0 disagreements with an independent oracle.
They measure `scripts/ontology.py` driven by `scripts/ontology_bench.py`. They
do not measure anything reachable from `POST /v1/studio/sources/sql` or
`POST /v1/chat/ask`, and no claim resting on them may be phrased as if they do.

## Which invariant should have caught it

R-0001 exactly, and it did not fire because no gate was written at the customer
layer for this epic - `tests/test_db_connector.py` runs entirely on a
`_FakeConnection` and asserts the connector's return value, which is the
intermediate artifact. The envelope invariants (E1-E12) cover
`POST /v1/chat/ask` and nothing covers `POST /v1/studio/sources/sql` beyond its
receipt.

## Do not

- Do not fix F-0046 alone and report EPIC-020 clause 2 satisfied.
- Do not call `validate_lake.fk_intact` from the connector. The claim belongs in
  `verify()`, the layer that decides whether a link is usable; the parquet lane
  reaching it from `load_adventureworks.py:545` is a second caller, not a home.
- Do not read this as "chat returns wrong numbers today from the ontology". It
  cannot - chat does not reach the ontology either. The harm is a promised
  refusal that is underivable and undelivered, not a measured wrong answer.
- Do not claim a rate. R-0010.
