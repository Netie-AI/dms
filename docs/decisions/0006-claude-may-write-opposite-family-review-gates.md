---
status: proposed
date: 2026-10-15
decision-makers: founder, delegated to the Claude Code lane on 2026-10-15 ("U decide E") - handover question E of 2026-10-09
outcome: Claude may write DMS and Cortex code. Every head is cleared only by an AGREE from the model family opposite its writer. An idle writer's PR is superseded on a new branch, never pushed over.
---

# DR-0006 - Claude may write; the opposite family clears every head

## Context and Problem Statement

The founder rules of 2026-10-09 say "Grok writers only, high effort, never fast. The
reviewer is a different model." The same handover asked, as open question E, whether the
twelve Claude-written PRs should be reseated with Grok or waived. On 2026-10-15 the
founder delegated the answer to this lane.

Two facts decide it.

1. **The writer seat is not where the defects were caught.** On 2026-10-08/09 six heads
   cleared by a Grok-family AGREE (#404, #405, #409, #419, #433, #435) each carried a
   blocker or major that a separate Claude run reproduced, among them destructive
   multi-statement SQL reaching submit with a green badge (#433), a compliance-gate
   bypass (#419), a green badge on an empty filter (#435) and raw cell values in the
   customer envelope (#405). Every one of those AGREEs was Grok reviewing Grok, or Grok
   reviewing an unnamed Cursor writer. n=6; this is evidence, not a rate.
2. **The lane stalled.** From 2026-10-09 to 2026-10-15 none of the founder's DMS merge
   order (#432 to #435) received a new head except #432. Only #420 merged. A rule that
   keeps the only available writer off the keyboard while the queue sits still is a
   control that blocks legitimate work (R-0005).

The property that protects WRONG=0 is that the writer and the reviewer come from
different model families. The merge-tier text approved on 2026-10-06 (#375) already says
"ONE second-model AGREE from the opposite model family". Practice drifted to "a different
Grok model", and the six misses came from that drift.

## Considered Options

- **Reseat every Claude-written PR with a Grok writer.** Costs the Grok lane's time, and
  re-review of the same diffs, for no safety gain: the reseated PR still needs an
  opposite-family review, which would then be Claude.
- **Blanket waiver: anyone writes, any different model reviews.** Keeps the drift that
  produced the six misses.
- **Claude may write, and the opposite family clears every head.** Chosen.

## Decision Outcome

1. **Claude may write** DMS and Cortex code. A Claude-written PR says so in its body:
   `Writer: Claude family (Claude Code)`. Every PR declares its writer on a `Writer:` or
   `Writer-Model:` line; trailers count only when there is no such line.
2. **The opposite family clears.** A head may be squashed only on an AGREE that names the
   exact head and comes from the family opposite its writer: Grok clears Claude, Claude
   clears Grok. Same-family AGREEs, AGREEs on an older head, AGREEs that name no family,
   and AGREEs followed by a DISAGREE or VOID on the same head do not clear. A PR whose
   writer family cannot be established cannot be cleared.
3. **One writer per branch, absolutely.** When a writer has been idle for 48 hours or more
   and its PR is blocked, Claude does not push to that branch. It opens a new
   `claude/<ticket>` branch from the idle head (keeping the original commits), fixes it
   there, and opens a PR that says which PR it supersedes. The founder or Lead closes the
   superseded one.
4. Nothing else in the merge tiers changes: CI on the exact head, DMS Check CLEAR, the
   serial order, the revert-on-fail rule, and "nothing declared PASS" all stand.

## Consequences

Positive: the stalled DMS order can move again; the twelve Claude-written PRs need a Grok
AGREE, not a rewrite; the review that caught the defects becomes the required one.

Negative: every Grok-written head now needs a Claude review before squash, so the Claude
lane becomes a standing dependency of the Grok lane, and the reverse. PRs that do not
declare their writer (#409 and #419 today) must add the line before they can clear.

## Confirmation

Enforcer: `scripts/check_writer_family.py` (`--pr N` reads the PR, its commits, comments
and reviews; exit 0 clearable, 1 BLOCK with reasons, 2 unreadable). Its test,
`tests/test_writer_family.py`, was seen failing: with the same-family check disabled,
`test_same_family_agree_is_refused_both_ways` goes red. The merger runs the script
before every squash and quotes its output in the merge record.

Not yet enforced: the script is not a required CI check, because a check that stays red
until a reviewer arrives would turn every new PR red (R-0005). Making it a check on the
merge commit, not on PR heads, is the follow-up.

Feedback ledger (PRD-001 section 7, to append when the Netie repo is writable): founder
2026-10-15, "U decide E", routed to merge tiers / DR-0006.
