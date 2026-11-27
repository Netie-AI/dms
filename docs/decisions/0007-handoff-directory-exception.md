---
status: accepted
date: 2026-11-27
decision-makers: founder (t1846u, 2026-11-27, Asia/Singapore)
outcome: handoff/ is a founder-approved exception to the five-files rule in this repo
---

# DR-0007 - The handoff/ directory is an exception to the five-files rule

Numbering: DR-0008 reserved DR-0007 for a possible renumber if both #440 and #461 claimed
DR-0006. That renumber never happened, and no `0007-*.md` existed on main. The founder
assigned 0007 to this decision on 2026-11-27. If #440 or #461 later needs its own record,
it takes the next unused number, never 0007.

## Context and Problem Statement

DMS CLAUDE.md, under File law, says five files per repo, no sixth, and "No per-agent
files. No per-session files." Netie Lead's weekly usage limit runs out every couple of
days. On 2026-11-27 the founder asked (t1846u) for a durable, repo-tracked handoff so
Claude Code on the laptop worktrees can continue toward the same goal when Lead is down,
and Lead can resume from Claude's progress, with both sides syncing through GitHub or
studio.netie.ai.

A handoff that lives only in chat is lost when the session ends or the limit hits. A
handoff that lives in STATUS.md would break the 60-line cap and mix live product state
with cross-agent protocol.

## Considered Options

- Keep the five-files rule strictly and put the handoff in STATUS.md or in GitHub issue
  comments only. STATUS.md cannot hold mains, lanes, lessons and a log. Issue comments
  are not reviewable with the code.
- A second repo for handoff. Splits the source of truth away from the ship-focus repo.
- A `handoff/` directory in Netie-AI/dms, with one-line pointers from AGENTS.md (and
  later from Cortex and OpenVault). Chosen by the founder in t1846u.

## Decision Outcome

1. `handoff/` in Netie-AI/dms is a founder-approved exception to the five-files rule
   (t1846u, 2026-11-27). It is not a sixth law file and not a per-agent or per-session
   file. It is the shared, append-friendly state between Netie Lead and Claude Code.
2. The directory holds RULES.md, STATE.md, CLAUDE_CONTINUE.md, LOG.md, LESSONS.md, and
   later copies of the rebuild plan. AGENTS.md stays a slim index and points here.
   Generated live facts (mains, open PRs, CI) are written by
   `scripts/handoff/update_state.py` into the machine block of STATE.md.
3. `handoff/` never contains a secret value, a token, a password, a key id prefix, or
   scored-pack question text, gold SQL, or totals beyond the published count lines.
4. Nothing else in File law changes: CLAUDE.md remains the law, STATUS.md stays at most
   60 lines, decisions still go in `docs/decisions/`, and AGENTS.md remains an index.

## Consequences

Positive: Lead and Claude Code can resume from the same goal after a usage cut-off,
without inventing state from chat.

Negative: agents must not treat `handoff/` as a place for product backlogs (GitHub issues
remain the ticket list) or as a way around CLAUDE.md.

## Confirmation

This file, plus the `handoff/` tree and the AGENTS.md pointer, landing on main through
the ordinary PR flow (dms#571).
