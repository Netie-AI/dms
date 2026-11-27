# handoff/LESSONS.md

Append-only distilled lessons for Netie Lead and Claude Code. Newest at the bottom.
Never edit or delete an earlier entry. Fix a mistake with a new entry.

Read this file before starting work. After any failure or correction, append one entry
with: the date (SGT), what went wrong, the root cause, and the fix folded in (a prompt,
a test, a CI gate, or a RULES line).

## 2026-11-27 - CloudAgent prompts with newlines broke JSON

- What went wrong: A CloudAgent launch failed because the prompt was inlined in a JSON
  payload and contained raw newlines, which broke the JSON.
- Root cause: The prompt was passed as a string field instead of as a file.
- Fix folded in: prompt / harness: pass CloudAgent prompts via files, never as a JSON
  string that may contain newlines.

## 2026-11-27 - Studio asks abstained because the Insights timeout was too short

- What went wrong: Studio asks abstained even when Cortex later wrote correct SQL.
- Root cause: `DMS_INSIGHTS_TIMEOUT_S` defaulted to 8 seconds. Cortex generate takes
  about 14 seconds, so every real question hit the timeout.
- Fix folded in: raise `DMS_INSIGHTS_TIMEOUT_S` to 60 on the box stack (in flight).
  Re-test Studio with real questions after the change. Do not treat a timeout abstain as
  a model failure until the budget is above the observed generate time.

## 2026-11-27 - Cortex loopback identity got 402 on Claude

- What went wrong: Studio's Cortex called OpenVault under a loopback identity that was
  not allowed to use Claude, so requests fell back to the free NVIDIA model.
- Root cause: Cortex was not using the DMS service token file.
- Fix folded in: prompt: point Cortex at the existing DMS service token file. Never
  print the token. Confirm a test call is answered by the intended provider before
  calling the stack ready.

## 2026-11-27 - Free-tier limiter caused hundreds of 429s before any hop

- What went wrong: Parallel grade runs saw 340 limiter 429s. `hop_attempts` showed none,
  which looked like a provider or hop bug.
- Root cause: The TokenBudgetLimiter rejected the `box:dms-cortex` key on the free tier
  (20 rpm, 40k tpm) in OpenVault `app.py` before any hop. The per-hop 402 fall-through
  (OpenVault #193) does not cover this case.
- Fix folded in: OpenVault #194, merged `1d7595fb`, adds a service tier and persists
  `limited_by`. Retier of the box key is a key and limit change: founder 1-hour objection
  window, never raised by an agent on its own. After a retier, confirm 429s are 0 on a
  live call, not from a writer report.

## 2026-11-27 - Writers claimed extra gates that conflict with the one-reviewer rule

- What went wrong: Writer reports said a PR also needed PR Bot CLEAR, Security YES, or a
  second-family review, on top of the one reviewer the founder named.
- Root cause: Older merge-flow text in repo docs was treated as binding over the founder
  rule.
- Fix folded in: RULES.md item 8. Ignore those extra-gate claims, note them on the PR,
  and require one reviewer who is a different model from the writer.

## 2026-11-27 - vague_question was a phrase regex, a banned word rule

- What went wrong: A reviewer on dms#570 found that a hard-coded phrase regex still
  decided a question was vague, so the ask path abstained without an AI read and without
  asking the user back.
- Root cause: A hand-coded word or phrase rule, which is banned.
- Fix folded in: RULES.md item 2. The writer must rebase #570, remove the phrase gate,
  and replace it with an AI step that reads the question with the schema and ontology,
  and when unclear, asks back with 1 to 3 interpretations instead of abstaining. Do not
  merge a PR that adds or keeps a phrase rule.

## 2026-11-27 - A writer report said OpenVault was deployed when the box was still on the old SHA

- What went wrong: A status report treated a merged or pushed SHA as live on the box.
  The box OpenVault was still on the previous SHA.
- Root cause: Deploy was inferred from git, not from the running process.
- Fix folded in: RULES.md (verify live) and this lesson: before reporting a service as
  deployed, read the live process SHA, a health endpoint, and one test call. A merge
  commit is not a deploy.
