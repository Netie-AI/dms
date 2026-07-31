# DMS — Pricing, Cost Breakdown, and Delivery Timeline

**Status:** commercial reference · v0.1 · 2026-07-30
**Grounds:** LLM API rates verified live (Anthropic, DeepSeek, July 2026) · hardware prices verified live (July 2026) · Malaysia SME grant figures verified live (Budget 2026)
**Depends on:** `DMS_TECHNICAL_ARCHITECTURE.md` · `DMS_ARCH_AMENDMENT_2026-07-30.md`

---

## 1. Timeline to first forward deployment

### 1.1 Where you actually are

Against the D1 queue (`DMS_ARCH_AMENDMENT §Appendix`): T0, T1, R1, X1, C3, C3.1 landed. T2 unblocked. **Roughly 7 of ~19 D1 tasks closed.**

### 1.2 What's left before D1 is demoable

| Remaining | Repo | Rough size |
|---|---|---|
| C4 — submit() seam, pools, telemetry, 3 known bypasses | Cortex | medium |
| C6 — scope-tagged memory | Cortex | medium |
| T3 — Spaces + scoped chat | DMS | medium |
| T4 — amend loop | DMS | large |
| T5 — OIDC + sessions | DMS | small |
| T6 — compose bundle, Caddy, port isolation | DMS | small |
| T7 — provenance spine (`_src`, drill-through rewrite, receipts) | both | large |
| T8 — frontend surfaces (chat, source panel, Spaces, Library, Amend, Audit) | DMS | large |
| T12 — promote pipelines (bronze→silver→gold) | DMS | large |
| T13 — ingest triage + honest receipts | DMS | medium |
| U0 → full UI build-out | DMS | large |

**12 tasks, several large.** C3 alone — contract bump, verification, enforcement, a 90-case corpus, a five-agent adversarial round, three rounds of fixes — is the honest unit of "one large task," and it consumed a full, dense session.

### 1.3 Two honest paces, not one number

The variable that actually decides this is **your available hours**, and you are not full-time on this — Jumpwin, the MR-TAE viva, Huawei/VitroxCOE applications, and everything else in your week are real and concurrent.

| Scenario | Assumption | Time to D1 | Time to first *paying* deployment |
|---|---|---|---|
| **Focused sprint** | ~30 hrs/week on DMS alone, large tasks batched | **8–10 weeks** | **+ 6–8 weeks hardening** → ~4 months |
| **Realistic part-time** *(current pace)* | ~12–15 hrs/week around your job and other commitments | **14–18 weeks** | **+ 8–10 weeks hardening** → **~6–7 months** |

**Plan around the second number.** The first is achievable only if DMS becomes the primary thing for two months, which conflicts with the job applications currently in flight. If one of those lands, replan immediately — better to move the date once, deliberately, than to silently slip against an optimistic one.

### 1.4 D1 ≠ sellable

D1 is one working install with you in the room. A paying customer needs, additionally:

- **C10** — the adversarial harness with the CI ratchet (§20 of the amendment). Without it, you cannot say "0 confidently wrong" to a customer with a straight face; you can only say it to yourself.
- **T14** — signed receipts. This is the feature that makes the product usable in front of *their* board, not just in front of you.
- **T5/T6 hardened** — auth and packaging good enough that IT doesn't reject it on sight.
- One real backup/restore drill, not a theoretical one.

Budget the 6–10 week hardening window above for exactly this. Selling before it exists trades a customer relationship for a demo.

---

## 2. What you provide vs. what the customer provides

State this explicitly in every proposal — it's the difference between a scoped engagement and scope creep by default.

| | You provide | Customer provides |
|---|---|---|
| **Hardware** | spec sheet, sourcing help, optional supply-at-cost | the machine (or lets you supply it), power, network point, physical space |
| **Software** | DMS + Cortex + OpenVault, licensed and configured | none |
| **Data** | ingest pipeline, triage, contract authoring help | the actual files, connector credentials, a nominated data owner |
| **Setup** | install, ACL configuration, first 90 days of sources ingested | 2–3 named staff for onboarding calls, IT contact for network/firewall |
| **Training** | live session + recorded walkthrough + written quick-reference | staff attendance, one steward nominated as internal owner |
| **Ongoing** | updates, monitoring, support per tier, quarterly health check | timely response to ACL/access questions, a steward who actually uses it |

The single most important line item to negotiate up front is the **named steward** — the amend loop and the certified-query library both depend on someone at the customer being the human in the loop. Without that person, adoption stalls regardless of how good the software is.

---

## 3. Compute economics — what LLM calls actually cost

Live rates, July 2026:

| Model | Input / MTok | Output / MTok | Role in DMS |
|---|---|---|---|
| DeepSeek V4 Flash | $0.14 (cache miss) / $0.0028 (hit) | $0.28 | routing, vocabulary, wording, diff explanation |
| Claude Haiku 4.5 | $1.00 | $5.00 | same tier, higher reliability |
| **Claude Sonnet 5** | $2.00 *(intro, to Aug 31 '26)* → $3.00 | $10.00 → $15.00 | **SQL generation over wide schema — the job that matters** |
| DeepSeek V4 Pro | $0.435 (promo) / $1.74 (standard) | $0.87 / $3.48 | budget alternative for SQL generation |
| Claude Opus 5 | $5.00 | $25.00 | escalation only — ambiguous/high-stakes queries |

**Route by job, not by vendor loyalty.** The architecture doc already specifies this (§6.4): small model for routing and wording, large model for SQL generation, because sqlglot validates the output either way. DeepSeek's rates make it viable for the small-model tier and as a first-pass on SQL generation, with Claude reserved for retries after two failures or for anything the plausibility check flags. This blend, not "pick one vendor," is what makes the unit economics work.

### Worked cost per question

Assume 3,000 input tokens (schema slice + question + context) and 400 output tokens (SQL + explanation) per answer, Sonnet 5 at intro rate:

```
input:  3,000 / 1,000,000 × $2.00  = $0.006
output:   400 / 1,000,000 × $10.00 = $0.004
                                    ─────────
per answer, uncached                $0.010
```

Prompt caching on the schema slice (stable across a session) cuts the input leg by up to 90%, so a session of 20 questions after the first is closer to **$0.003–0.005 per answer**, not $0.01. Budget on the uncached figure; the caching is upside.

A department asking 50 questions a day, 22 working days: **~50,000 tokens/day → ~$11–15/month in raw API cost, at Sonnet rates, unoptimized.** This is the number that makes the "10%" instinct make sense on paper — and exactly why it's the wrong basis for a price, covered next.

---

## 4. Why 10% markup is the wrong number — and what to charge instead

Your instinct — pass through usage, add a management fee — is structurally correct. The percentage isn't.

**What 10% actually has to cover**, on a bill that might be $15–40/month per customer:

- Payment processing and currency conversion (USD API bill → MYR customer invoice)
- Monitoring the pipeline so a runaway agent loop doesn't hand you a $400 surprise bill you must eat or awkwardly pass on
- Support time when a customer asks "why did this cost more this month"
- The float risk of paying Anthropic/DeepSeek before the customer's invoice clears

At $20/month in usage, 10% is $2. That doesn't cover fifteen minutes of anyone's time, and it means your margin structure depends entirely on the license fee, not the usage line — which is fine, but then say so rather than pricing the usage line at a number that looks like it's supposed to matter.

**Recommended: 30–40% on pass-through usage**, framed as a **managed usage fee**, not hidden. This is a normal SaaS-reseller margin, not a markup that will surprise anyone who's bought infrastructure before. At $20/month usage, 35% is $7 — still a trivially small number for the customer, and now correctly sized for the AWS-reseller-style economics you're actually running.

**Better: cap it.** Bill actual usage + 35%, with a **usage cap per tier** the customer picks up front, so their monthly number never surprises them. If routing keeps most traffic on the cheap tier as designed, most customers land near the low end of their tier and you look generous without giving anything away.

| Tier | Monthly usage cap | Typical customer | Price (usage + 35%, capped) |
|---|---|---|---|
| Light | up to 2M tokens/mo | 1–3 users, occasional questions | **RM 150/month** |
| Standard | up to 15M tokens/mo | one department, daily use | **RM 600/month** |
| Heavy | up to 60M tokens/mo | company-wide, multiple Spaces | **RM 1,800/month** |
| Overage | — | — | actual cost + 35%, itemized on the invoice |

These are cloud/hybrid-mode customers only — see §5.

---

## 5. Local vs. hybrid — the two commercial modes

This maps directly to the architecture's deployment modes (§2 of the base doc).

### Mode A — Local (their box, local model)

No usage line. The model runs on their RTX 4070-class hardware, so there's no per-token cost to pass through. You charge:

- **One-off setup fee** — hardware sourcing (if applicable), install, ingest of their first data set, training
- **Annual engine license** — updates, security patches, the F1 ledger and F5 gate staying current, support

This is the customer who wants zero cloud dependency and has said so, or whose data sensitivity makes it non-negotiable.

### Mode B — Hybrid *(expected default)*

Local box for storage, DuckDB serving, and the small routing model. Cloud API (wrapped, metered) for SQL generation on complex queries. Lower setup cost, monthly usage-based fee from §4.

### Mode C — No local hardware

Everything runs against cloud APIs behind OpenVault's FreeRoute, including what would otherwise be local routing. Highest monthly bill, lowest setup cost, weakest story on data residency — position this as a starting point with a defined upgrade path to Mode B, not a permanent home. It's the easiest sell and the one you want customers migrating off within a year, once they trust the product enough to buy hardware.

---

## 6. Hardware — appliance tier ladder (sell up, don't overspec)

**Default for the first three customers: T1.** One mid card, 128GB RAM, 4TB NVMe. Architecture minimizes model calls (L0/L1 = zero tokens); buying 3×5090 for a shrinking L2 workload is backwards. Quote T3 only when a customer funds air-gap wide-schema generation.

Verified street prices, July 2026 (≈RM 4.70/USD). GPU crypto / “PII card” is a category error — PDPA leave-machine scan lives in **OpenVault**, not silicon.

| Tier | Hardware | Local capability | SQL generation | Box cost (MYR) |
|---|---|---|---|---|
| **T0** | no GPU, 128GB RAM | none | L0/L1 only, abstain otherwise | **RM 18–22k** |
| **T1** *(default)* | 1× 12–16GB | routing, wording, diffs, PII detect | cloud, or abstain | **RM 20–25k** |
| **T2** | 1× 5090 32GB | + 30B-class | local on narrow schemas, cloud on wide | **RM 35–42k** |
| **T3** | 2× 5090 | + 70B dense | fully local (priced option) | **RM 70–85k** |

MoE does **not** stretch VRAM the way people hope — all experts must stay resident. 2×5090 ≈ 64GB Q4 fits 70B dense comfortably; Qwen3-235B-A22B-class does not. Break-even vs Sonnet API needs ~RM 2,000+/month token spend (~15–20k questions/day) — no SME does that. Electricity on a 3-card box alone can exceed the API bill it replaces.

**You don't need to own this inventory.** Three ways to handle it, cleanest first:

1. **Customer procures against your spec sheet.** Zero capital risk to you, no markup revenue, fastest to execute. Default recommendation.
2. **You source and supply at cost + 15–20%** for customers who'd rather not deal with a vendor themselves. Reasonable margin for the sourcing and pre-configuration work; you're not in the hardware resale business, don't price like you are.
3. **You lease/finance it** into the monthly fee. More complexity than a first-stage company should take on — skip this until you have working capital and a reason.

Legacy one-liners (still useful for RFQs):

| Spec shorthand | Rough MYR |
|---|---|
| Ryzen 7 + RTX 4070 12GB + 32GB + 2TB | RM 7–9.5k (below T0/T1 target RAM) |
| Ryzen 9 / i9 + 12–16GB GPU + 128GB + 4TB | **T1 band** RM 16.5–25k |
| Server-class 256GB ECC + multi-GPU | T3 / growth quote |

---

## 7. Setup, training, and support — pricing

### One-off implementation fee

Scoped by data complexity, not by company headcount — a 20-person company with 400 messy spreadsheets is a bigger job than a 200-person company with one clean SQL database.

| Tier | Scope | Fee (RM) |
|---|---|---|
| **Starter** | ≤ 5 sources, one department, ≤ 5 users, standard connectors | **RM 8,000–15,000** |
| **Standard** | ≤ 20 sources, multiple departments, custom triage/contracts | **RM 20,000–40,000** |
| **Complex** | 20+ sources, Salesforce/ERP integration, multi-site, custom pipelines | **RM 45,000–90,000+** |

Includes: discovery call, install, first-batch ingest and triage, initial contracts and metric certification with the nominated steward, and the training below. Excludes: hardware (§6), ongoing license/subscription (§4/§8), scope added after sign-off — quote change orders separately, in writing, every time. Not doing this is the single most common way a services business quietly works for free.

### Training

- **Included in every implementation fee:** one live session (half-day, on-site or remote) for up to 10 staff, a recorded walkthrough, and a written quick-reference card.
- **Additional sessions:** RM 1,500–2,500 per half-day, on-site preferred.
- **Steward deep-dive** (metric certification, amend workflow, audit review) — 2 hours, included for the one nominated steward, RM 800/session for additional stewards.

### Support tiers, monthly

| Tier | Response | Included | Price |
|---|---|---|---|
| **Standard** | next business day | patches, updates, email support | **included in engine license** |
| **Priority** | 4-hour response | + phone/video support, quarterly health check | **RM 800–1,500/month** |
| **Dedicated** | 1-hour response | + named contact, monthly usage review, on-call | **RM 3,000–6,000/month** |

---

## 8. Annual engine license (Mode A and Mode B both carry this)

This is the fee that exists regardless of usage mode — it's what funds continued engine development, security patching, and the guarantee that F5/F1 stay current. Rough sizing against team size and source count, since that's what drives support load, not revenue:

| Tier | Profile | Annual (RM) |
|---|---|---|
| Starter | ≤ 10 users, ≤ 5 sources | **RM 6,000–10,000/yr** |
| Standard | ≤ 50 users, ≤ 20 sources | **RM 18,000–30,000/yr** |
| Enterprise | 50+ users, 20+ sources, multi-site | **custom, from RM 40,000/yr** |

Renewal, not resale — position it exactly like an anti-virus or firewall license, because functionally it is one: it's what keeps the appliance current and supported, not new capability sold each year.

---

## 9. Worked example — a real quote

**Customer:** 15-person finance and ops team, 40 Excel workbooks, one Salesforce instance, wants hybrid mode, standard support.

| Item | One-off | Recurring |
|---|---|---|
| Implementation (Standard tier) | RM 28,000 | — |
| Hardware (target tier, customer-procured against spec) | RM ~19,000 *(customer pays supplier directly)* | — |
| Engine license (Standard) | — | RM 24,000/yr |
| Usage (Standard tier, capped) | — | RM 600/mo → RM 7,200/yr |
| Priority support | — | RM 1,200/mo → RM 14,400/yr |
| **Year 1 total to you** | **RM 28,000** | **RM 45,600** |
| **Year 1 all-in, customer's view** | **RM 47,000** *(incl. hardware)* | **RM 45,600** |

Year 2 onward, no implementation fee: **RM 45,600/yr recurring**, a number you can say out loud with a straight face against Snowflake's usage-scaling bill or a RM 300,000+ Databricks/ERP integration project.

**MDEC angle, verified live:** Budget 2026's Geran Digital PMKS MADANI matches 50% up to RM 5,000, and the wider SME Digitalisation Grant runs RM 5,000–500,000 depending on programme tier, with the Malaysia Digital Acceleration Grant (RM53M allocated in Budget 2026) specifically covering AI adoption for Malaysia Digital–status companies. Worth pursuing MDEC Technology Service Provider status early — it's a genuine deal-closer at the SME tier, effectively halving the implementation fee for an eligible customer, and it's a credibility signal independent of the money.

---

## 10. Positioning against the incumbents — the one-line version, backed by numbers

Snowflake and Databricks bill by consumption with no ceiling; a growing SME's bill grows with them, invisibly, until someone in finance asks why. Your customer's bill this quarter is the same as last quarter unless they added sources — because you capped it, on purpose, and told them the cap up front. That's not a smaller version of the same pitch. It's the opposite pitch: **predictable cost was rejected as a feature by companies whose margin depends on you not having it.**

---

## 11. Summary price sheet

| Line | Range (RM) |
|---|---|
| Implementation, one-off | 8,000 – 90,000+ |
| Hardware, one-off *(if you supply)* | 8,000 – 66,000 + 15–20% |
| Engine license, annual | 6,000 – 40,000+ |
| Usage, monthly (hybrid/cloud only, capped) | 150 – 1,800 |
| Support, monthly | included – 6,000 |
| Additional training, per session | 800 – 2,500 |

Every number above is a starting anchor for negotiation, not a rate card to publish verbatim on a website in year one — hold pricing loosely until three real deployments confirm what implementation actually costs *you* in hours, then tighten the ranges.

---

## Appendix — sources checked live for this document

- Anthropic API pricing (Claude Sonnet 5, Haiku 4.5, Opus 5) — verified July 2026, Anthropic first-party pricing docs
- DeepSeek API pricing (V4 Flash, V4 Pro) — verified July 2026, official DeepSeek pricing page
- AI workstation / server hardware pricing — verified July 2026, multiple builder quotes (VRLA Tech, MyAIHardware, Petronella)
- Malaysia SME digitalisation grants (Geran Digital PMKS MADANI, SME Digitalisation Grant, Malaysia Digital Acceleration Grant) — verified July 2026, Budget 2026 sources

Re-check the LLM pricing lines before finalizing any customer contract — Sonnet 5's introductory rate ($2/$10) expires **August 31, 2026** and reverts to $3/$15, which changes the §3 and §4 math directly.
