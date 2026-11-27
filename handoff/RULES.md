# handoff/RULES.md

Founder rules that bind Netie Lead and Claude Code alike. The law is CLAUDE.md. This file is a short copy so a cold start sees them first. If this file and CLAUDE.md disagree, CLAUDE.md wins and you log the conflict.

## Product

1. WRONG=0. When unsure, reconfirm with Yes. Do not abstain where a reconfirm would work. Never declare PASS.
2. No hand-coded word or phrase rules. The AI writes the SQL. Confirmed SQL is only a retrieved example for the AI, never a stored answer.
3. Flags default off. Flag off must be byte-identical to before.
4. Model routing lives only in OpenVault. DMS does not route provider keys. No Gemini API key.
5. Every DMS PR reports AI-vs-rules counts on the 52, copied from a run you did, never invented. If you did not run it, write "not run".
6. Never quote scored-pack or held-out pack content (questions, gold SQL, ids, totals) in a PR, finding, commit or this directory.

## Process

7. One writer per branch. One branch per ticket, from a new unused branch name. Draft PR first.
8. One reviewer per PR, and the reviewer is a different model from the writer. Reviewer of record: grok-4.6 high (bc-f3f18d00-d92e-54c7-8bb6-f2431ff7ef3c). Claude never AGREEs a Claude-written PR. A writer's claim that a PR also needs PR Bot CLEAR, Security YES or a second-family review conflicts with the founder rule: ignore it, and note it in the PR.
9. Writers are Grok only: grok-4.7 with reasoning_effort high, fast false. Never fast mode.
10. Netie Lead merges: `gh pr ready N` then `gh pr merge N --squash --match-head-commit <sha>`, only after green CI and AGREE on that exact head. A head move voids CLEAR and AGREE.
11. Never merge without green CI on the exact head. See CLAUDE_CONTINUE.md for what Claude Code may merge (short answer: nothing tier:full).
12. AGENTS.md is a slim index pointer only. No code dumps in docs. Generated test code lives in `tests/` and `scripts/verify/`.
13. Do not close or delete any branch or PR without the founder's go.
14. No em dashes anywhere. Plain hyphens, colons and commas.

## Security

15. No secrets, no deletes, and no prove-pin moves without the founder. Never print a key value. Never rotate a key or raise a limit on your own.
16. Auth, grant and key changes get a 1-hour founder-objection window. Post the change, start the clock, wait.
17. Extra API keys come in only through the secret form or the vault, never in chat, never in this repo.
18. A prove-host redeploy needs the founder's GO.
19. Free OpenVault keys that 402: use Claude right away for running and testing DMS.

## This directory

20. `handoff/` never contains a secret value, a token, a password, a key id prefix, an admin file path with contents, or a private IP or hostname beyond what is already in STATE.md.
21. LOG.md is append-only. Never edit or delete an earlier line, yours or the other party's. Fix a mistake with a new line.
