# handoff/CLAUDE_CONTINUE.md

How Claude Code resumes the work when Netie Lead is down, and how Netie Lead takes it back. Read RULES.md and LESSONS.md first.

## Resume, in order

1. `git fetch origin && git checkout origin/main` in a clean worktree. Read `CLAUDE.md` (law), then `handoff/RULES.md`, `handoff/LESSONS.md`, `handoff/STATE.md`.
2. `python scripts/handoff/update_state.py` (needs `gh` logged in). It refreshes the machine block: mains, open PRs, heads, CI. Do not commit a refresh on its own.
3. `python scripts/handoff/update_state.py --log-since claude`. Read every Lead entry since your last one. If you have none, read the whole of LOG.md.
4. Check the pinned issue "Handoff: Claude take over" and any issue labeled or commented `@netie-lead continue` or titled `handoff/NEXT`. A founder instruction in an issue beats STATE.
5. Pick the top NEXT item of the first lane in STATE.md that is concrete. If every lane is still a placeholder, stop and append a LOG line saying so. Do not invent a ticket.
6. Check the GitHub issue for that item. Another party's open PR for the same item means you do not start: review it instead.

## Unmerged state

A handoff update may sit in an unmerged draft PR. The pinned issue "Handoff: Claude take over" is the real-time channel: its newest comments are authoritative over a stale STATE.md on main.
Also run `git fetch origin 'refs/heads/handoff/*:refs/remotes/origin/handoff/*'` and read STATE.md, LOG.md and LESSONS.md on the newest `origin/handoff/lead-*` branch. LOG.md is the durable mirror: it merges with each PR. The issue comments are the live copy.

## Work

- One worktree and one branch per ticket: `git worktree add ../dms-<ticket> -b claude/<ticket-slug> origin/main`. Branch names are never reused.
- Reproduce CI locally before any push, as DMS CLAUDE.md "Claude Code sessions" describes.
- Open a DRAFT PR. Label it `tier:fast` or `tier:full` by the CLAUDE.md rules. When unsure, full. Put AI-vs-rules counts on the 52 in the body, or "not run". A Claude-written PR starts with `Writer: Claude family (Claude Code)`.
- Update `handoff/STATE.md` (hand part: done, in flight, next) in the same PR, and append to LOG.md:
  `python scripts/handoff/update_state.py --append-log claude "dms#NNN draft at <sha8>: what, CI, next"`.
  The LOG line says author=claude. Never write author=lead.
- After any failure or correction, append an entry to `handoff/LESSONS.md` (date SGT, what went wrong, root cause, fix folded into a prompt, a test, a CI gate or a RULES line). Never edit an earlier lesson.
- Do not edit another party's PR or branch. Do not touch a PR you did not open unless the founder says so.
- Never print, copy or commit a secret value. Never rotate a key or raise a limit.

## What Claude Code may and may not do

Founder t1846u (2026-11-27): when Netie Lead is down or out of quota, Claude Code may write product code and open PRs to keep the goal moving, following RULES.md (no word rules, AI writes SQL, flags off by default, AI-vs-rules counts on the 52, WRONG=0).

| Action | Claude Code |
|--------|-------------|
| Run and test DMS, measure the 52, report counts | yes |
| Review a PR written by a Grok model (`AGREE` or `DISAGREE (Claude family, second-model review) @ <sha8>`) | yes. Never a Claude-written PR |
| Write product code and open a draft PR | yes, when Lead is down or out of quota, following RULES.md |
| Work touching auth, keys, routing, prove pins, scoring, `/ask`, `contract/` (tier:full) | draft PR only. Leave merge for Lead and the different-model reviewer when Lead returns |
| Merge | only a `tier:fast` PR, or a docs-only PR labeled `tier:fast` that touches nothing in the always-full list, with CI green on the exact head and one AGREE from a different model on that head, using `gh pr merge N --squash --match-head-commit <sha>`. Never a `tier:full` PR, including Claude's own |
| Close or delete a branch or PR | no, founder only |
| Move a prove pin, redeploy a prove host, change a key or limit | no, founder only |

## Hand back to Netie Lead

When Lead returns it runs `python scripts/handoff/update_state.py --log-since lead`, reads every claude entry, reads the PRs they name, and
then appends a lead line such as "resumed, read claude entries through <timestamp>". Claude's draft PRs stay draft until Lead has reviewed their exact head, except a `tier:fast` PR Claude already merged under the rule above.
Claude stops starting new tickets as soon as a lead entry newer than the pinned issue appears.

## Triggers (the two directions)

| From | To | Mechanism |
|------|----|-----------|
| Lead | Claude | At about 90 percent weekly usage Lead writes a final STATE and LOG entry, then opens or updates the pinned issue "Handoff: Claude take over" with the date, the top NEXT item and a link to STATE. It may also comment `@claude-continue <item>` there. |
| Claude | Lead | Claude comments `@netie-lead continue` on the pinned issue (or on the PR it wants reviewed) and appends a LOG line. A Lead routine that watches GitHub picks it up. |
| Founder | either | An issue titled `handoff/NEXT` names the next ticket. Either party may read it. Only the founder edits it. |

Routine wording for Netie Lead is in the body of the PR that added this directory (design note). Nothing here creates a label, an issue or a routine by itself: each needs the founder's go.
