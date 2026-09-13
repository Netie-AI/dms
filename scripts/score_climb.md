# GEN-02 -- measured live coverage climb harness (WRONG=0)

**Ticket:** [#180](https://github.com/Netie-AI/dms/issues/180) under EPIC-GEN-01 [#178](https://github.com/Netie-AI/dms/issues/178).
**Depends on:** GEN-01 landed @ `a9578348` (retrieve + execute-validate + offline `--ab`).
**Does not close** #180 or #178. **Not COMPLETE.** Not 99.95%.

Climb is measured on the **product ask path** (pack exact-match first, then GEN-01 generative on miss). Do not expand certified packs as the climb. Offline dual-path remains `python scripts/score_curated.py --ab` (no keys).

## Baseline (frozen)

Product-path live `curated_ceo` @ `91c5cc99` (VQ-04 refuse traps):

```
OK 7   LAYER 10   ABSTAIN 9   WRONG 0
answered = OK+LAYER = 17 / 26
```

Isolated A/B @ `a9578348` (GEN-01 offline, Platform-reported):

```
exact-match answered 10 / 26  (38.5 pct)
generative answered  1 / 26  (3.8 pct)
WRONG 0 both
```

Re-measure. Do not edit these counts to invent a rise. Climb gen via retrieve+Cortex compute+validate, not pack expansion.

## Who runs what

| Seat | Command | Must not claim |
|------|---------|----------------|
| Platform (Studio API + IAP) | `--climb --ab --url https://studio.netie.ai/api` | COMPLETE / 99.95% / greening planted refuses |
| Platform product-path | `--climb --url https://studio.netie.ai/api` | host-online COMPLETE |
| CI / any seat, no network | `--self-check` and `--ab` | a live score |

`--climb --ab` POSTs each curated question twice: `ask_path=exact` (VQ/pack/refuse only) then `ask_path=generative` (ontology retrieve + execute-validate; skip pack). Cortex compute / OpenVault FreeRoute stay on the host. This script never sends keys.

CRAG-style gates (validate-or-abstain, ideas only, not a vendor clone): each gen envelope is graded `validated` / `abstain_validate` / `abstain_gate` / `skipped`. Document RAG CRAG stays parked (P-DMS-19) until a doc index exists.

Keys/models stay in Cortex + OpenVault on the host. This script never sends API keys.

## Env (fail closed -- no laptop default on --climb)

```
--url            preferred. Example: https://studio.netie.ai/api
DMS_API_BASE     used if --url omitted (alias STUDIO_API_BASE)
DMS_URL          last resort for --climb; --live still defaults to 127.0.0.1:8090
```

Unset `--url` / `DMS_API_BASE` / `DMS_URL` -> exit 2 CONFIG.
Unreachable host or IAP **401/403** -> exit 3 BLOCKED (not a score, not PASS).
`demo_fallback=true` or `ask_mode=demo` on `/health` -> exit 1 FAIL.
WRONG>0 (green planted refuse, demo fallback on an answer, transport error mid-pack) -> exit 1 FAIL.

Cursor cloud / seats without Access cookies will see 403 on `studio.netie.ai`. That is BLOCKED for this seat. Platform on prove / with IAP runs the score.

Studio SPA `/health` is HTML. Use the **API** prefix (`/api/health`).

## Run

```powershell
# Fail-closed self-check (no network, CI-safe):
python scripts/score_curated.py --self-check

# Offline A/B: exact-match pack vs retrieve+bind generative (GEN-01). No keys.
python scripts/score_curated.py --ab

# Platform live climb against Studio API (Cortex+OpenVault already on the host):
$env:DMS_API_BASE = "https://studio.netie.ai/api"
python scripts/score_curated.py --climb
# or:
python scripts/score_curated.py --climb --url https://studio.netie.ai/api
```

Artifacts (under `DMS_SCORE_DIR` or `.tmp/`): `score_climb.json` (counts + delta), `score_climb_cases.json` (per qid).

Prints real OK / LAYER / ABSTAIN / WRONG, answered delta vs `91c5cc99`, and `answered_by_path` (`exact_match` from `route=governed_metric|verified_query`, `generative` from `route=generated`). LAYER on expect:l0 is an honest generative/L1/L2 answer, not L0.

## Honesty

- WRONG=0 is the law. Coverage may rise, stay flat, or fall -- the harness reports the delta. It does not invent a climb.
- Planted refuse/abstain traps staying green is WRONG, not coverage.
- This is **not** a GitHub CI job. Do not add `--climb` to `.github/workflows/ci.yml`.
