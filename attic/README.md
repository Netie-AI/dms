# attic

Moved with `git mv`. Nothing here is imported by the product. Do not hard-delete.

Source commit at the move: `fa0eeef7026e5c09c2ebffd7b573087bb41fc700`
(origin/main, "READY-04: guide a new company from connect to a first count").

## scripts

| From | Why it moved |
|------|----------------|
| `scripts/repro_capped_parent_orphans.py` | One-off F-0046 repro. The product check lives in tests. |
| `scripts/repro_refused_badge.py` | F40 repro. The customer path is `map_ask_response_to_envelope`. The comment in `packages/executor/dms_executor/__init__.py` points here. |
| `scripts/stress_dms_http.py` | In-process HTTP stress. It sets `DMS_DEMO_FALLBACK=1`. No test imports it. |
| `scripts/try_changes.py` | Narration harness for an old recovery wave. pytest already covers the claims. Live cites are in `docs/archive` only. |

Historical sentences in `CHANGELOG.md`, `docs/archive/`, and `docs/decisions/` still say `scripts/<name>.py`. Those files are append-only or archived. They are not callers.

## docs/subagents_findings

121 files dated before 2026-11-01. November rows stay in `docs/subagents_findings/` because open writers still append there. `docs/subagents_findings/INDEX.md` still names the moved files, with this attic prefix.

18 of the 121 had no index row. This directory is the full set:

- `2026-09-05_cca-benchmarks-contain-none-of-our-domain.md`
- `2026-09-05_cca-engagement-labelled-product-log.md`
- `2026-09-05_cca-model-recogniser-beats-the-word-list.md`
- `2026-09-26_ingest-types-277.md`
- `2026-09-26_onto-derive-01.md`
- `2026-10-01_grant-read-02.md`
- `2026-10-01_score-bird-rows-01.md`
- `2026-10-05_bank-02-ask-audit-export.md`
- `2026-10-05_bank-04-tls-appliance.md`
- `2026-10-05_heldout-pack-d.md`
- `2026-10-05_key-01.md`
- `2026-10-05_redteam-gate-01.md`
- `2026-10-06_chat-01-studio-turn.md`
- `2026-10-06_ov-mint-bearer-01.md`
- `2026-10-06_serving-precheck-01.md`
- `2026-10-06_studio-result-01.md`
- `2026-10-09_same-family-review-misses.md`
- `2026-10-16_ingest-colname-01.md`

The other 103 are the index rows whose path now starts with `attic/docs/subagents_findings/`.
