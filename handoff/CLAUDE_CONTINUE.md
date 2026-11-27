# handoff/CLAUDE_CONTINUE.md

How Claude Code resumes the work when Netie Lead is down, and how Netie Lead takes it back. Read RULES.md first.

## Resume, in order

1. `git fetch origin && git checkout origin/main` in a clean worktree. Read `CLAUDE.md` (law), then `handoff/RULES.md`, `handoff/STATE.md`.
2. `python scripts/handoff/update_state.py` (needs `gh` logged in). It refreshes the machine block: mains, open PRs, heads, CI. Do not commit a refresh on its own.
3. `python scripts/handoff/update_state.py --log-since claude`. Read every Lead entry since your last one. If you have none, read the whole of LOG.md.
4. Check the pinned issue "Handoff: Claude take over" and any issue labeled or commented `@netie-lead continue` or titled `handoff/NEXT`. A founder instruction in an issue beats STATE.
5. Pick the top NEXT item of the first lane in STATE.md that is concrete. If every lane is still a placeholder, stop and append a LOG line saying so. Do not invent a ticket.
6. Check the GitHub issue for that item. Another party's open PR for the same item means you do not start: review it instead.

## Work

- One worktree and one branch per ticket: `git worktree add ../dms-<ticket> -b claude/<ticket-slug> origin/main`. Branch names are never reused.
- Reproduce CI locally before any push, as DMS CLAUDE.md "Claude Code sessions" describes.
- Open a DRAFT PR. Label it `tier:fast` or `tier:full` by the CLAUDE.md rules. When unsure, full. Put AI-vs-rules counts on the 52 in the body, or "not run".
- Update `handoff/STATE.md` (hand part: done, in flight, next) in the same PR, and append to LOG.md:
  `python scripts/handoff/update_state.py --append-log claude "dms#NNN draft at <sha8>: what, CI, next"`.
  The LOG line says author=claude. Never write author=lead.
- Do not edit another party's PR or branch. Do not touch a PR you did not open unless the founder says so.
- Never print, copy or commit a secret value. Never rotate a key or raise a limit.

## What Claude Code may and may not do

| Action | Claude Code |
|--------|-------------|
| Run and test DMS, measure the 52, report counts | yes |
| Review a PR written by a Grok model (`AGREE` or `DISAGREE (Claude family, second-model review) @ <sha8>`) | yes. Never a Claude-written PR |
| Open a draft PR for an ordinary ticket | yes, subject to STATE open question 1 (founder to confirm Claude may write product code) |
| Work touching auth, keys, routing, prove pins, scoring, `/ask`, `contract/` (tier:full) | draft PR only. Leave it for Lead and the founder |
| Merge | only a `tier:fast` PR, with CI green on the exact head and one AGREE from a different model on that head, using `gh pr merge N --squash --match-head-commit <sha>`. Never a `tier:full` PR |
| Close or delete a branch or PR | no, founder only |
| Move a prove pin, redeploy a prove host, change a key or limit | no, founder only |

## Hand back to Netie Lead

When Lead returns it runs `python scripts/handoff/update_state.py --log-since lead`, reads every claude entry, reads the PRs they name, and
then appends a lead line such as "resumed, read claude entries through <timestamp>". Claude's draft PRs stay draft until Lead has reviewed their exact head.
Claude stops starting new tickets as soon as a lead entry newer than the pinned issue appears.

## Triggers (the two directions)

| From | To | Mechanism |
|------|----|-----------|
| Lead | Claude | At about 90 percent weekly usage Lead writes a final STATE and LOG entry, then opens or updates the pinned issue "Handoff: Claude take over" with the date, the top NEXT item and a link to STATE. It may also comment `@claude-continue <item>` there. |
| Claude | Lead | Claude comments `@netie-lead continue` on the pinned issue (or on the PR it wants reviewed) and appends a LOG line. A Lead routine that watches GitHub picks it up. |
| Founder | either | An issue titled `handoff/NEXT` names the next ticket. Either party may read it. Only the founder edits it. |

Routine wording for Netie Lead is in the PR that added this file (design note) and in `handoff/STATE.md` blockers when the founder adopts it.
