---
status: proposed
date: 2026-08-20
decision-makers: founder (pending) - routed via prd-agent before any implementation
---

# DR-0003 - Engine-side accuracy machinery, copied from four vendors

## Context and Problem Statement

The requirement, as stated: accuracy and reliability across every type of question;
terabyte-scale data may compute longer and verify longer; accuracy must be driven by the
API, not by a frontier model reasoning correctly at request time; Cortex drives, DMS
helps.

Everything after "driven by the API" is the constraint that matters. It rules out the
approach every vendor markets and none of them relies on: a longer system prompt. What is
left is deterministic machinery - metadata, compile-time validation, and post-conditions
that execute.

Primary-source research across Databricks (AI/BI Genie, Unity Catalog metric views,
Lakeflow expectations, MLflow judges), Palantir (Foundry Ontology, AIP Logic, AIP Evals),
Microsoft Azure AI Foundry (Evaluation SDK, Content Safety groundedness, Fabric data
agent) and Microsoft SQL Server / Power BI (semantic models, DAX measures, Q&A linguistic
schema, verified answers, indexed views) produced one convergent finding and several
divergent ones.

**The convergent finding.** All four vendors prevent fan-out inflation the same way, and
none of them does it with a model. A metric is anchored to a declared grain, and the
aggregation is not permitted to see a table finer than that grain:

| Vendor | Mechanism | Enforced at |
|---|---|---|
| Databricks | Metric view has one `source` that *is* the grain; the object cannot be joined to directly, it must materialise as a CTE first | definition + query compile |
| Palantir | A derived property crossing a many-cardinality link **must** name an aggregation, or the definition is rejected | authoring time |
| Power BI | Filter-then-aggregate: dimension predicates resolve to key sets pushed into the fact table; a dimension never appears in the aggregating `FROM` | model evaluation |
| Fabric NL2SQL | Generated query is validated against the selected schema before execution | pre-execution |

Every one is deterministic. Not one needs an LLM at request time. This is the single
architectural answer to "accuracy driven by the API".

**The divergent findings, stated honestly.** None of the four publishes a text-to-SQL
accuracy number in primary documentation. Databricks' explicit position is that
benchmarking is customer-owned. Microsoft's Fabric data agent evaluation section says the
team "tested across a range of public and private datasets" with no n, no dataset, no
percentage. Palantir publishes no benchmark at all. Two further points worth knowing:

- Azure AI Foundry's agent evaluators explicitly **do not support** Azure AI Search,
  Fabric Data Agent, or Code Interpreter - i.e. Microsoft's own evaluation suite does not
  cover the data-question-answering path.
- Power BI Copilot's documented behaviour when a question is not answerable from the
  semantic model is to fall back to the LLM's general knowledge. That is the opposite of
  abstention, and it is the failure this repo's E9 exists to prevent.

We are not behind on measurement. We are ahead of the published state of the art on
measurement, and behind on the compile-time machinery.

## Where DMS and Cortex stand today

Verified by reading the code and by execution, not by assertion.

| Mechanism | Have it? | Where |
|---|---|---|
| Answer-level oracle comparing figures to independently recomputed truth | **yes** | `scripts/verify_freeform_demo.py` |
| Conservation identity as a run-time post-condition on the oracle | **yes** | same, `oracle()` |
| Required-abstention cases with an executed unanswerability proof | **yes, new** | same, `prove_unanswerable()` |
| Explicit `abstained` flag plus a provenance badge (not badge-absence) | **yes** | `dms_executor.envelope` |
| No-executed-query means no authority to state a figure (E9) | **yes** | same |
| Value dictionary / filter-literal normalisation against stored encodings | **partial** - a demote after the fact, no dictionary | hard rule 12 |
| Declared grain per metric, enforced at plan time | **no** | - |
| Join-cardinality verified at build time, not declared and trusted | **no** | - |
| Generated SQL validated against an allow-listed schema before execution | **partial** - `reject_hostile_chat_sql` is not on the ask path | `packages/executor` |
| Verified answers: curated trigger phrase to pinned query plan | **no** | - |
| Accuracy gate running in CI | **no** - every oracle gate is manual | `.github/workflows/ci.yml` |
| Model promotion scored on correctness | **no** - promotes on badge plus latency | `scripts/bakeoff_l2_models.py` |

## Considered Options

**A. Prompt engineering and instructions.** What Genie's "instructions" and Power BI's
"AI instructions" surface actually are. Rejected on the requirement's own terms, and on
Microsoft's own warning: "the LLM only interprets them. There's no guarantee that the LLM
will exactly follow instructions", instruction order changes output, and end users can
neither see nor disable them.

**B. LLM-as-judge at request time.** A second model checks the first. Rejected: it is a
model at request time, it doubles cost, and Databricks' own honest framing is that their
judge agrees with humans at kappa ~0.65 - roughly as consistent as the humans, which is
not an accuracy mechanism, it is a sampling instrument.

**C. Deterministic metric layer plus compile-time validation plus executed
post-conditions.** Chosen. Everything below is code that either passes or raises.

## Decision

Build accuracy as engine machinery in Cortex, in this order. Ordered by accuracy gained
per unit of irreversibility, so the cheap reversible wins land first.

1. **Grain guard (Cortex).** Every metric declares its grain columns. At plan time,
   compare the requested grouping against the declared grain; if the query would group or
   join below it, abstain rather than return a number. This is Power BI's `ISFILTERED`
   test and Databricks' metric-view source rule. It is the one change that stops fan-out
   as a *class* rather than case by case - which is what R-0004 asks for, and what the
   ~15x bug on `fix/oracle-fanout` was a single instance of.

2. **Verify declared cardinality instead of trusting it (Cortex, build time).** For every
   relationship a metric treats as the "one" side, run `SELECT COUNT(*) = COUNT(DISTINCT
   key) FROM dim` at build time and fail the build. This is strictly better than
   Databricks, who document that `rely.at_most_one_match` is "not validated at runtime.
   If the join produces a fan-out, measures return incorrect results." Our own warehouse
   would have failed this check: `inventory` is 7,388 rows for 509 SKUs.

3. **Schema allow-list validated before execution (Cortex).** Parse the generated SQL and
   reject any relation or column outside the grant. Fabric's pipeline is literally
   generate -> validate against selected schema -> execute. Note honestly what it proves:
   that the query touches only permitted objects. Not that the answer is right.

4. **Value dictionary (Cortex).** Materialise curated distinct-value lists per column,
   resolve user terms deterministically (exact, then case/whitespace-folded, then
   rejected) *before* emitting SQL. Today hard rule 12 catches a mismatch after the query
   returns nothing - and only when it returns *no rows*, so `SUM(x)` over an impossible
   filter still certifies a `0`. Build the dictionary with the caller's row filters
   applied, as Databricks does, or it becomes a side channel.

5. **Verified answers (Cortex, curated by a human).** Trigger phrases bound to a pinned
   parameterised query plan, stored on the semantic layer so every surface inherits it.
   The model binds typed arguments into `:param` slots; the query text is executed as
   stored. Label the answer verified only in that case - never for free-generated SQL.

6. **Fix the refusal badge (Cortex + DMS).** See the finding below. This is a defect, not
   a feature, and it is the cheapest item on this list.

7. **Accuracy in CI.** No oracle gate currently runs in CI, so answer accuracy cannot
   regress a build. Wire `verify_freeform_demo.py` against a warehouse fixture.

8. **Score model promotion on correctness.** `bakeoff_l2_models.py` promotes the fastest
   model that returns a confident badge, on n=1 question, with no correctness check. That
   optimises for confident output rather than true output, upstream of every gate below
   it.

### What "100 percent accuracy" can and cannot mean

It cannot mean a measured error rate of zero. R-0010: zero errors in n trials bounds the
true rate at 3/n. At the current n=19 answerable questions, a clean run bounds the error
rate at ~15.8 percent, not at zero. n >= 300 before "<1 percent" is sayable at all.

The achievable formulation, and the one worth committing to:

> **100 percent precision-on-answered, with coverage as the free variable.** Every figure
> the product states under a confident badge is correct. Questions it cannot answer
> correctly, it refuses. Coverage is then an honest number that goes up as certified
> assets are curated - never by loosening the gate.

That is a claim about the *product's behaviour*, which machinery can enforce, rather than
a claim about a *measured rate*, which n=19 cannot support. Both halves need measuring,
which is why refusal-precision is now reported alongside precision-on-answered: a product
that answers everything scores 100 percent precision on what it happens to get right.

### Terabyte scale

"Compute longer, verify longer" buys three things concretely, and all three survive the
move off a 12k-row DuckDB demo because all three are SQL:

- the conservation identity, run as a live post-condition on the customer's actual query
  rather than only on the eval oracle - one extra aggregate;
- a second independent plan for the same question, compared before answering - roughly
  2x cost for a binary agreement signal;
- the cardinality probe from item 2, on a schedule rather than only at build.

What does not survive: anything requiring a full scan per question, and LLM-judging every
answer. Both are per-question costs that grow with the data.

## Confirmation

- `scripts/verify_freeform_demo.py --oracle-only` validates every oracle and every
  unanswerability proof against the live warehouse.
- `scripts/verify_freeform_demo.py --self-check` feeds every case its own oracle (which
  must be accepted) and a perturbed answer (which must be rejected), so a case that cannot
  fail - or one that rejects its own truth - is caught without asking the product
  anything. This is R-0007 applied to the whole set rather than to one gate, and it found
  a real defect within seconds of first running: an engine returning a composite label as
  one already-joined string was rejected, because the gate split the oracle's side of the
  comparison and not the claim's. Review had not found it; two rounds of tests had not
  found it.
- `tests/test_freeform_gate.py` proves the gate can fail (R-0007) on a fixture built in
  the test, so it never skips (R-0002).
- Items 1-8 each land with an assertion on the DMS envelope from `POST /v1/chat/ask`
  (CLAUDE.md 10a), never on generated SQL alone.

### What the adversarial round changed (W-0001, R-0003)

Four agents that did not write the gate were asked to refute every oracle, and a separate
arbiter re-executed their claims through the gate's own `judge()`. It upheld defects in
almost every case, in both directions. Recorded here because the pattern generalises to
any oracle-based gate:

**The instrument rejected correct answers more often than it accepted wrong ones.**
`claimed_ranking` graded the first numeric column, so an engine returning
`{status, total_cost_myr, pct_of_spend}` was graded on the cost. That single defect fired
on five cases. Composite grains were graded against a separator this file invented, so a
correct two-column answer failed. Unordered questions were graded position-wise, marking a
correct alphabetical answer wrong *and* naming the wrong key in the failure line. Each of
those is R-0005 pointed at ourselves, and a gate that cries wolf gets loosened.

**A gate can be wrong in the direction it was built to prevent.** `ff_network_utilization`
accepted 8 of the 20 possible single-warehouse omissions, because 0.5 pct relative
tolerance on a network percentage is wider than the effect of dropping a site.
`ff_spend_share_by_status` declared `SELECT 100.0` as a conservation identity, which the
row-count measure also satisfies - a check that cannot fail, reading like one that can.
`ff_outbound_by_supplier_country` demanded a refusal on an "ambiguity" whose second
reading was an uncorrected fan-out, with a retirement guard that could never fire because
the two readings hardcoded different labels.

**Ambiguity is more common than it looks.** Three cases written as answerable had a second
defensible reading that changed the winner: net stock movement (is a write-off stock that
left?), distinct SKUs (does "has shipped" include PENDING?), and write-off value (which of
the two disagreeing `unit_cost_myr` columns?). Each was either scoped in the question or
split into an answerable case plus an abstain twin, following the `ff_carrier_ontime`
precedent.

**Comments drift from the code they explain.** Four rationales were factually wrong about
this warehouse - an invented capacity spread, a trap that runs in the opposite direction to
the one described, a claim that `COUNT(*)` preserves a ranking it reorders. The oracle is
the thing standing in judgement, so a false comment in it is not cosmetic.

The generalisable lesson for items 1-8 above: **an accuracy mechanism needs an adversary
who did not build it, and the adversary must run the mechanism rather than read it.** Every
finding here was produced by executing `judge()` against candidate answers, not by
inspection. None of it was visible to the author.

## Open finding routed with this record

**A manifest refusal reaches the customer as `L2_VALIDATED`.** Reproduced end to end by
`scripts/repro_refused_badge.py`. `_abstain_refused` emits `badge/route/layer = "refused"`;
`_is_abstain_signal` recognises none of those three, so `_provenance_from_flat` falls
through to `Badge.SESSION`, which DMS maps to `L2_VALIDATED` with `abstained=false`.
E9 does not fire (the refusal text carries no figures) and the hard-rule-12 demote does
not fire (`sql_used` is None). `assert_envelope_valid` passes - the envelope is
internally consistent and wrong. Per CLAUDE.md 10a a green badge on abstention prose is a
P0.
