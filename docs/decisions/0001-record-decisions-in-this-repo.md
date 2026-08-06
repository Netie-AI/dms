---
status: accepted
date: 2026-08-02
decision-makers: founder
---

# DR-0001 - Record decisions in this repo

## Context and Problem Statement

Decisions were being re-litigated every session because the reasoning behind them lived
in chat logs rather than in the repo.

## Considered Options

- Keep decisions in chat and hope they are remembered
- A wiki alongside the repo
- Decision records in the repo, changed by pull request

## Decision Outcome

Decision records in the repo, at `docs/decisions/DR-NNNN-kebab-title.md`.

Opened with `status: proposed` **as a pull request** - the PR is the RFC and the thread
is the review. On merge, status flips to `accepted`. Superseded records are kept and
marked, never deleted or edited.

## Consequences

Positive: the argument lives in the same history as the code, and an agent can read why
before proposing the same thing again.

Negative: one more file per decision, and a threshold judgement about what deserves one.
Write a DR only when it crosses a repo boundary, touches the contract / ledger / manifest
enforcer / a protected path, is expensive to reverse, or an agent would otherwise
re-litigate it. Everything else is a commit message.

## Confirmation

This file existing is the confirmation for DR-0001. Every later DR must name a test file
that exists - a decision naming a non-existent enforcer is a wish, not a control.
