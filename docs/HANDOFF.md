# Handoff to Claude Code (slim)

Written 2026-11-29 (SGT). The full version, with a per-PR table and the day plan, is kept off this repo at `<HANDOFF_PATH>`. This page is the part that belongs in the repo. It has no secrets and no hostnames. Fill the placeholders from the table below. Regenerate live state with `python scripts/handoff/update_state.py`; read the pinned issue [#679](https://github.com/Netie-AI/dms/issues/679) and `handoff/CLAUDE_CONTINUE.md` first.

## Fill these in from your private ops notes

These placeholders are not values. Copy them from your private ops notes. Do not commit a filled-in copy.

| Placeholder | Fill with |
|-------------|-----------|
| `<HANDOFF_PATH>` | Full handoff file kept off this repo |
| `<STUDIO_HOST>` | DNS name clients use for Studio |
| `<LOOPBACK>` | This host's loopback address only |
| `<OPS_ROOT>` | Private ops documents, not in the git repo |

## Goal

DMS answers questions on a customer's databases and files, governed and auditable, and creates documents, decks and apps from those answers. Accuracy is judged by generality on **unseen** databases, with **WRONG = 0**. DMS and Create are a thin, pure-AI harness: the provider (the model) is king.

## Rules that bind (long form: `CLAUDE.md`, `handoff/RULES.md`)

- No invented PASS. Report only numbers from a run you did, or "not run".
- No hand-coded word or phrase rules. The AI writes the SQL.
- Flags default off and accept only the digit `1`; flag-off is byte-identical.
- Model routing lives only in OpenVault. No provider or model id in DMS or Cortex. No Gemini API key. No billing.
- Never print a secret. Keys arrive only through the vault or the founder's secret form. The OpenVault Claude cap (500/day) is changed only by the founder.
- No deletes of critical files, no paying, no closing or deleting branches or PRs without an explicit go.
- Auth, grant and key changes get a 1-hour founder-objection window (this covers the governance console, SCIM, HARNESS-SEC, OpenVault seat and guard PRs, and any key mint).
- A prove-host redeploy, a prove-pin move, a Postgres password rotation, outreach, a landing deploy, and remote VM or tunnel changes need the founder's GO.
- No em dashes.

## Review and merge

One writer per branch, a new branch name each time, draft PR first, rebase before review. Writers are `grok-4.7` with `reasoning_effort` high and `fast` false. Critical PRs need **two** reviewers, both AGREE at the exact head SHA: reviewer-1 `bc-f3f18d00-d92e-54c7-8bb6-f2431ff7ef3c` (CI and gates) and reviewer-2 `bc-fce289f4-992b-5efc-9cb9-5e1469c51aa9` (behaviour). Merge only with both AGREEs at that head, green CI on it, and a CLEAN merge state; undraft, then squash with the sha set to the head (`gh pr merge N --squash --match-head-commit <sha>`). A head move voids every verdict. Tier rules and Claude Code's own merge limits are in `CLAUDE.md` and `handoff/RULES.md`.

## Work queue (founder order)

1. Style v2 rebuild after design research, plus customisable document layouts.
2. Create app: a runnable app with its own backend and a DMS dataset, with confirmed and logged actions, and hand-offs to deck creation or a Cursor research job. Create context (#681) and Create docs and slides (#661) are merged.
3. Connectors: Oracle (flagged, exists), S3 / object stores (#630), Azure, Salesforce (#633).
4. Skills library: browse and fetch skills, plus Google AI insights (no Gemini key).
5. Accuracy on held-out databases: land #612 then #668, then measure live through OpenVault.
6. Scale check.
7. Click through every page and button (#680).
8. Governance console (#671), SCIM (#672), HARNESS-SEC (#663): auth class, founder window.

## Launching Cursor cloud agents (v1 API)

Base `https://api.cursor.com`, HTTP Basic with the key as the user and an empty password. The key is an environment variable (`CURSOR_API_KEY`); never print it or put it in a prompt. `GET /v1/models` lists models; for writers use id `grok-4.7` with params `reasoning_effort=high`, `fast=false`. Launch: `POST /v1/agents` with `{"prompt":{"text":...},"model":{"id":...,"params":[{"id":...,"value":...}]},"repos":[{"url":...,"startingRef":"main"}],"autoCreatePR":false}`. Follow up or steer an existing agent: `POST /v1/agents/{id}/runs`. Poll `GET /v1/agents/{id}/runs/{runId}`; artifacts at `/v1/agents/{id}/artifacts`. The Create generator uses the same calls (`packages/core/dms_core/create_generator.py`).

## Infrastructure

The stack (Postgres 17, OpenVault, Cortex, DMS, nginx with basic auth) runs on `<LOOPBACK>` on one Linux host. Clients use `<STUDIO_HOST>`. How that name is fronted stays in private ops notes. The founder decides any cutover. Backups: an age-encrypted daily bundle of Postgres and the OpenVault home to a second server, with a restore check against a scratch database. Operator documents stay off the repo under `<OPS_ROOT>` (the host README, `RESTORE.md`, and the cloud operator guide). Deploying the three services elsewhere is `docs/DEPLOYMENT.md`.

## Day plan

Day 0: health check, land clean PRs (#596), push #663 and #671 through both reviewers and the founder window, show the founder the style research. Day 1: restack SCIM, land #612 then #668, rebase #680 and click through every page. Day 2: live accuracy and Create checks, scale check. Day 3: connectors and the skills ticket. Day 4: fix the deployment guide from a first real install, refresh `handoff/STATE.md`, hand back with a written state.
