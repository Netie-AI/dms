# GEN-02 -- measured live coverage climb harness (WRONG=0)

**Ticket:** [#180](https://github.com/Netie-AI/dms/issues/180) under EPIC-GEN-01 [#178](https://github.com/Netie-AI/dms/issues/178).
**Depends on:** GEN-01 landed @ `a9578348` (retrieve + execute-validate + offline `--ab`).
**Does not close** #180 or #178. **Not COMPLETE.** Not 99.95%.

Climb is measured on the **product ask path** (pack exact-match first, then GEN-01 generative on miss). Do not expand certified packs as the climb. Offline dual-path remains `python scripts/score_curated.py --ab` (no keys).

## Baseline (frozen)

Live `curated_ceo` @ `91c5cc99` (VQ-04 refuse traps):

```
OK 7   LAYER 10   ABSTAIN 9   WRONG 0
answered = OK+LAYER = 17 / 26
```

Re-measure by running `--climb`. Do not edit these counts to invent a rise.

## Who runs what

| Seat | Command | Must not claim |
|------|---------|----------------|
| Platform (can reach Studio API) | `--climb --url https://studio.netie.ai/api` | COMPLETE / 99.95% / greening planted refuses |
| Laptop loopback | `--live` (`DMS_URL` defaults `http://127.0.0.1:8090`) | host-online / Studio climb |
| CI / any seat, no network | `--self-check` and `--ab` | a live score |

Keys/models stay in Cortex + OpenVault on the host. This script never sends API keys.

## Env (fail closed -- no laptop default on --climb)

```
--url            preferred. Example: https://studio.netie.ai/api
DMS_API_BASE     used if --url omitted (alias STUDIO_API_BASE)
DMS_URL          last resort for --climb; --live still defaults to 127.0.0.1:8090
```

Unset `--url` / `DMS_API_BASE` / `DMS_URL` -> exit 2 CONFIG.
Unreachable host -> exit 3 BLOCKED (not a 26-WRONG score, not PASS).
`demo_fallback=true` or `ask_mode=demo` on `/health` -> exit 1 FAIL.
WRONG>0 (green planted refuse, demo fallback on an answer, transport error mid-pack) -> exit 1 FAIL.

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
