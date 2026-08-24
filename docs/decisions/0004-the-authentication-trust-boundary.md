---
status: proposed
date: 2026-08-24
decision-makers: founder (pending) - routed via prd-agent, PRD-001 feedback ledger F49
---

# DR-0004 - The authentication trust boundary, and what the first contract may promise

## Context and Problem Statement

**There is no authentication anywhere in DMS.** This is not a gap discovered by inference;
the code says it in its own first line.

`apps/api/dms_api/middleware_actor.py:1`

```
"""T5 lite - trust headers until OIDC. Not a security boundary for production."""
```

Lines 15-23 of that file read `x-dms-tenant-id`, `x-dms-actor-id` and `x-dms-role` from
client-supplied headers, defaulting to process constants from `settings.py`.
`apps/api/dms_api/app.py:119-132` registers `DevActorMiddleware` and then includes
thirteen routers, none of which declares an auth dependency: `deps.py:47-51` exposes
exactly five dependencies - settings, Cortex client, space store, store binding, ask
service - and there is no principal among them.

Two consequences follow, and they are not equally urgent.

**One is live.** `apps/api/dms_api/routes/pipelines.py:32-37` declares
`GoldSignBody.steward_id: str` as a plain request-body field; `:77-81` calls
`compliance_gate` passing no actor; `:84-90` writes `steward_id=body.steward_id` into the
tamper-evident ledger. The caller names the actor. That is KB attack `A-0005` with
identity in place of the trust flag, and it is reachable by anyone who can reach the API.

**One is inert, and must stay inert.** `grep -rn "request.state" apps/ packages/
--include=*.py` returns only the middleware that writes it. Nothing reads it. So the
header path is currently a latent defect rather than a live one - and that is precisely
why **wiring `DevActorMiddleware` through as an interim step is forbidden**: making routes
honour `x-dms-role` converts an inert escalation into a live one. Credential verification
must land in the same change as any wiring, or the wiring must not happen.

The reason this is a decision record rather than a ticket is that **the two honest answers
imply different contracts**, not different implementations. PRD-001 section 3 already
narrows deployment to "self-host and single-tenant pilot only", which is a real constraint
that a real buyer may accept in writing - or may not. Nothing an agent can verify decides
that. It crosses the ledger, it changes what a signed statement means, and it will be
re-litigated in every session until it is written down, which is all four thresholds in
`DOCUMENT_SYSTEM.md` section Tier 5.

## Considered Options

### Option A - Name the trust boundary and constrain the first sale

The first install is single-tenant, on the customer's own network, reachable only over VPN
or air-gapped. There is no identity provider. Actors come from server-side configuration,
never from a request. The limitation is written into the SOW as a named condition of sale:
*the API trusts its network; anyone who can reach the host can act as the configured
steward.*

Work implied: delete every path by which a caller can name an identity (the `steward_id`
body field first), delete or neuter `DevActorMiddleware` so no header is ever consulted,
pass the server-side actor into `compliance_gate`, and state the boundary in the runbook
and the SOW.

### Option B - OIDC before any install

No install ships until a request carries a verified credential resolving to a principal,
and every route reads that principal instead of a process constant.

Work implied: an auth dependency over `dms.api_keys` or an OIDC verifier producing a
`PrincipalDep` in `deps.py`; routes read it; the executor and the signed manifest receive
it; `dms.sessions` and `dms.api_keys` gain their first readers (both tables are migrated
today and read by nothing).

### Option C - Wire the existing middleware through as an interim

Rejected, and recorded so it is not proposed again. `x-dms-role` is caller-supplied and
unverified. Making routes honour it converts `A-0005` from latent to live privilege
escalation, and it would arrive wearing the appearance of progress.

## Decision Outcome

**PENDING - founder.** No option is chosen and this record authorises nothing.

What is true under either A or B, and therefore does not wait for this decision:

1. `steward_id` is removed from `GoldSignBody` and the ledger actor is derived
   server-side. Under A the source is configuration; under B it is the principal. Under
   neither is it a request field. This closes the live `A-0005` instance today.
2. `compliance_gate` receives an `actor` on every gated mutation.
3. `A-0007` on the read path is closed regardless (PRD-001 F51, routed to EPIC-003):
   the Space scope check at `apps/api/dms_api/routes/library.py:199` and `:219` is nested
   inside `if space_id:` and is skipped when the parameter is omitted.

What genuinely differs: whether the product may be sold to a buyer who will not accept a
network as its security boundary, and whether "who approved it" in the ledger means a
person or a deployment.

## Consequences

**If A.** The first sale can close sooner and the engineering is small, but the promise in
PRD-001 section 2 - "the confirmation lands in the ledger with what changed and who
approved it" - is only true at the granularity of an install, not a person. Every
object-level permission item is decoration under A, because a row predicate keyed to an
identity nobody verified enforces nothing (PRD-001 F55). Any buyer with an internal audit
function will ask, and the honest answer must be in the SOW before they ask, not after.
The risk is not technical: it is that the limitation goes unsaid and is discovered by a
customer.

**If B.** The governance story is true when told, and object-level permissions become
worth building. The cost is that the first install waits, and identity-provider
integration is scope PRD-001 does not currently carry - so choosing B also amends the PRD.

**Under both.** `DevActorMiddleware` must not survive as it is. A middleware that reads
identity from a header and a docstring that says it is not a security boundary will
eventually be honoured by a route written by someone who read only the first of those two.

## Confirmation

**Stated honestly: the enforcer for this decision does not exist yet.** This section names
what must exist before the decision may be called honoured, and a record that named a
non-existent test as if it ran would be a wish (`DOCUMENT_SYSTEM.md` Tier 5).

Exists today, and is the file to extend:

- `tests/invariants/test_boundaries.py` - real, and currently blind to this class:
  `:39` sets `MUTATING_METHODS = {"post", "put", "patch", "delete"}`, so it inspects no
  GET at all. Extending it to classify a route by what it **reaches** rather than by its
  HTTP verb touches a protected path and needs an `INVARIANT-CHANGE:` trailer, and the
  extended gate must be shown to fail before it is trusted green (R-0007).

Must be created before this record moves to `accepted`:

- an assertion that **no request field and no request header can determine a ledger
  actor** - the direct regression test for `A-0005`, asserted on the response of
  `POST /v1/pipelines/gold/sign` and on `GET /v1/audit/ledger`, which are the artifacts a
  customer receives (R-0001, CLAUDE.md hard rule 10a). Asserting on the Cortex append
  payload is necessary and insufficient.
- under B only: an assertion that a request with no credential is refused on every route
  that reaches customer rows.

Until both exist, this record stays `proposed` and no claim about authentication may
appear in any external asset (PRD-001 section 8).
