---
status: accepted
date: 2026-08-02
decision-makers: founder (delegated to Claude Code, 2026-08-02)
---

# DR-0002 - Space scoping for the six demo warehouse tables

## Context and Problem Statement

`Executor.live_ask` mints its manifest from `demo_acl()`, which allowlists all six demo
tables with predicate `TRUE` regardless of `space_id`
(`packages/executor/dms_executor/__init__.py:172`, `:111-120`). Every Space therefore
reads the whole warehouse and badges the result `L0_CERTIFIED`.

The correct functions - `intersect_space_grants`, `resolve_session_acl` - exist and are
unit-tested with **no production caller**. The boundary cannot simply be switched on:
`acl_grants` has zero rows and three of six tables have no `data_sources` row, so
enforcement today would refuse 100 percent of asks, which is an R-0005 failure.

Wiring it requires knowing which tables are company-wide and which are restricted. That
is a product judgement, not an engineering one, and it was blocking the work.

## Considered Options

**A. Everything Space-scoped.** Maximally strict. Rejected: every Space then needs a full
grant set before it can answer anything, the demo becomes a permissions exercise, and the
first thing a viewer sees is a refusal.

**B. Everything company-scoped except financials.** Simple, but it makes only one table
interesting and the boundary demo hinges on a single case.

**C. Split by commercial sensitivity, with a shared operational core.** Chosen.

## Decision Outcome

**Company-scoped** - readable from any Space:

| Table | Why |
|---|---|
| `locations` | Warehouse layout. Reference data. Knowing bin A-04-12 exists harms nobody. |
| `inventory` | What is on the shelf. Every operational role needs it to do their job. |

**Space-scoped** - readable only where explicitly granted:

| Table | Why |
|---|---|
| `transactions` | Carries `unit_cost_myr`. Unit cost reveals margin; margin is not open-book. |
| `suppliers` | Supplier terms and relationships. Commercially sensitive, procurement-only in most SMEs. |
| `shipments` | Links to counterparties. The closest thing here to customer PII. |
| `alerts` | Derived from the three above, so it inherits their sensitivity. An alert that leaks a cost threshold leaks the cost. |

### The two demo Spaces

| Space | Grants |
|---|---|
| **Warehouse Ops** | `locations`, `inventory`, `shipments` |
| **Finance** | `locations`, `inventory`, `transactions`, `suppliers` |

This split was chosen so the demo shows **both halves of the control**, not just the
refusal:

- *"What was our revenue last month?"* - answers in **Finance**, abstains in
  **Warehouse Ops**.
- *"Where is SKU-00397 stored?"* - answers in **both**, because `locations` and
  `inventory` are shared.
- *"Which supplier has the longest lead time?"* - answers in **Finance** only.

A boundary that only ever refuses looks broken. A boundary that refuses the sensitive
question and answers the operational one in the same session, side by side, is the thing
worth showing.

## Consequences

**Positive.** Unblocks `Netie-AI/dms#2`. The strict-xfail at
`tests/test_space_acl_boundary.py:86` becomes the completion signal. Two Spaces with
overlapping-but-unequal grants is also a more honest picture of a real customer than one
Space per person.

**Negative.** `alerts` being fully Space-scoped may prove too strict - an out-of-stock
alert is arguably operational. If that surfaces in use, split `alerts` by category rather
than weakening the whole table, and supersede this record. Do not add a predicate
exception in code without a new DR.

**Note.** This is a scoping decision for the **demo dataset**. A real customer's scoping
is theirs to set, and the seeding path must not hardcode these names.

## Confirmation

`tests/test_space_acl_boundary.py` - currently `xfail(strict=True)` at `:86`. The
decision is honoured when that flips to passing **without the assertion being weakened**,
and when the full corpus re-runs with nothing valid newly refused (R-0005).

A second test must assert the positive case: the same question answering in Finance and
abstaining in Warehouse Ops, asserted on the DMS envelope from `POST /v1/chat/ask`, not
on Cortex-side state (R-0001).
