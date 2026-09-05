# CCA certifies the encoding, not the executed query

**Date:** 2026-09-05
**Keywords:** EPIC-CCA, CCA-05, claim narrowing, R-0003, R-0010, dms#137, dms#148
**Found by:** an independent verification run, not by the implementer

## Expected vs actual

**Expected.** EPIC-CCA's acceptance says the system "SHALL return L0_CERTIFIED with
sources naming encodings+sql OR ABSTAIN naming the missing binding, never a confident
wrong answer". The build was reported against that wording.

**Actual.** The cascade certifies *that an encoding exists and how the column spells it*.
It never constrains the query that runs. `Executor.live_ask` passes the question to
`self._cortex.ask()` unmodified; `BinderResult.binding_text()` is written only into the
constraint trace and the `assumptions` strings, and the `sql` stage is deliberately never
emitted. So a certified cascade and a wrong filter can coexist:

    Q: rental in Malaysia, commercial only
       badge   L0_CERTIFIED   abstained False
       trace   sense CERTIFIED, asset_class CERTIFIED, geo CERTIFIED "(unconstrained)"
       SQL     ... WHERE country = 'Malaysia'
       column  country encoded 'MY'

Zero rows summed, a plausible total, a green badge, and a green trace. That is worse than
the same wrong answer without the control, because the control's certification is attached
to it.

## Repro steps

1. Build a warehouse whose `transactions.country` holds `MY` and `SG`.
2. Point `Executor` at it with a Cortex stub that returns `badge=certified` and
   `sql_used="SELECT ... WHERE country = 'Malaysia'"`.
3. `POST /v1/chat/ask` with `"rental in Malaysia, commercial only"`.
4. Read the envelope: `L0_CERTIFIED`, `abstained=False`, `constraint_trace` all CERTIFIED.

Two separate defects met there and both are fixed (`ca070af`): the geo stage now
recognises a named country and binds it to `'MY'`, and the no-term stage label no longer
claims the ask placed no geo constraint. **The class defect is not fixed and cannot be
fixed inside this epic.**

## Root-cause CLASS

**Certifying an input to a step is not certifying the step.**

The cascade sits before L0 and settles what the filter values should be. The query is
composed downstream, by Cortex, from the question text. Nothing carries the certified
values across that boundary and nothing checks the executed SQL against them. Every
guarantee the cascade offers is therefore about the *encoding*, and none is about the
*answer*.

The test suite could not catch this because it asserts the intermediate artifact:
`test_certifies_every_stage_when_every_encoding_is_landed` asserts `"'MY'" in bindings`,
which proves the cascade computed the right spelling. Hard rule 10 names exactly this
mistake, and the ticket that wrote the test is the ticket that carried the rule.

## Which invariant applies

**Assert the artifact the user actually receives, not an internal one** (hard rule 10),
and **silent fallback is a lie: degradation has to show in the output.** A control whose
certification does not constrain the thing it certifies is a degradation wearing a policy
decision's clothes.

## The claim, narrowed

What EPIC-CCA delivers, and what may be said about it:

* an ask whose filter terms cannot be bound to landed values **abstains before L0**, names
  the missing encoding, and never reaches the engine (verified end to end, 7 asks,
  `cortex_called=False`, no figure in `text`);
* an ask whose terms **do** bind carries a trace naming the table, the column, the landed
  spellings, the members the pack did not match, and the landed values the filter left out.

What it does **not** deliver, and must not be claimed:

* that the executed SQL used the certified spelling;
* therefore, "never a confident wrong filter" in the general case.

## What would settle it

One test that lets a certified cascade through, captures the SQL Cortex actually executed,
and asserts every filter literal in it is a member of `BinderResult.values` for the
matching stage, demoting to ABSTAIN otherwise. If that assertion cannot be written because
DMS never sees or constrains the SQL, then the epic's acceptance wording needs the amend,
not the test. Two routes exist and both are new work outside this epic:

1. pass the certified predicates to Cortex as typed constraints and have the engine apply
   them (a contract change, Cortex-side ticket);
2. verify after the fact - compare `sql_used`'s filter literals against the certified
   values on the DMS side, and demote the badge on a mismatch (DMS-side, no contract
   change, catches the failure rather than preventing it).

Route 2 is cheap and would have caught the reproduction above. It is not in EPIC-CCA and
should be its own ticket rather than a quiet widening of this one.
