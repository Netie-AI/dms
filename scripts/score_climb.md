# GEN-02 -- measured live coverage climb harness (WRONG=0)

**Ticket:** [#180](https://github.com/Netie-AI/dms/issues/180) under EPIC-GEN-01 [#178](https://github.com/Netie-AI/dms/issues/178).
**Depends on:** GEN-01 landed @ `a9578348` (retrieve + execute-validate + offline `--ab`).
**Does not close** #180 or #178. **Not COMPLETE.** Not 99.95%.

Climb is measured on the **product ask path** (pack exact-match first, then GEN-01 generative on miss). Do not expand certified packs as the climb. Isolated live A/B uses `ask_path=exact|generative`. Offline dual-path remains `python scripts/score_curated.py --ab` (no keys).

GEN-02 follow-on: isolated `ask_path=generative` Cortex `POST /dms/query` miss binds local `bind_plan` from the retrieved ontology, then the same compile → validate/CRAG → submit. Product path does **not** bind on miss (Cortex certified still runs). Explicit Cortex `unsure` is not overridden. Planted refuses stay ABSTAIN. Ontology measures are warehouse-honest (thin reseed vs Cortex lake), not pack SQL.

## Distill ladder (ideas only -- no vendor paste)

Netie-native mapping. Not DB-GPT / mybot / n8n / OpenWillow / guaca/rakazo code.

1. **Certified-first, then free gen.** `ask_path=product` and `exact` hit VQ/pack/refuse first. `generative` skips pack, tries retrieve→plan→validate, ABSTAIN only after that attempt. WRONG=0.
2. **Ontology as retrieve spine.** `demo_ontology` object/link/measure declarations (verified on the lake), not a new YAML pack format and not certified-query SQL. `from_manifest` remains the extract path.
3. **Hybrid fuse + CRAG-style confidence.** Retrieve tags `hybrid_fuse` when schema+ontology both hit. Harness grades `validated` / `abstain_validate` / `abstain_gate`. Doc RAG CRAG stays parked (P-DMS-19).
4. **Text2SQL as Cortex compute + typed slots.** `POST /dms/query` (OpenVault FreeRoute inside Cortex) then `bind_plan` on miss. No vendor text2sql SDK.

Founder lock: abstain is safety (WRONG=0), not the ceiling. Isolated gen **tries**:

1. Multi-retrieve (schema SQL + ontology spine YAML + encodings + hybrid_fuse).
2. Ontology relations (`demo_ontology` verify + compile joins).
3. Generate SQL from typed slots (`bind_plan` + lake filters + `keep_gt` from the question).
4. Execute + EXPLAIN/grant validate; ABSTAIN only after that attempt fails.
5. Optional ML route/train/apply -- **not this slice**.

No LangChain/LangGraph. No vendor paste. No memorized VQ pack expansion as the climb.

Ontology YAML for retrieve is slot-name pack `packages/executor/dms_executor/ontology_spine.yaml` (loaded as retrieve allowlist, no SQL). Compile stays verified `demo_ontology` Python. Not a new vendor pack format.

Frozen live A/B @ `a9578348`: exact 10/26 (38.46 pct), gen 1/26 (3.85 pct), WRONG=0. Offline `--ab` on this branch is a separate measurement. Do not edit the frozen counts to invent a rise.

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

Re-measure. Do not edit these counts to invent a rise. Climb gen via retrieve+Cortex compute (bind_plan on miss)+validate, not pack expansion.

## Who runs what

| Seat | Command | Must not claim |
|------|---------|----------------|
| Platform (Studio API + IAP) | `--climb --ab --url https://studio.netie.ai/api` | COMPLETE / 99.95% / greening planted refuses |
| Platform product-path | `--climb --url https://studio.netie.ai/api` | host-online COMPLETE |
| CI / any seat, no network | `--self-check` and `--ab` | a live score |

`--climb --ab` POSTs each curated question twice: `ask_path=exact` (VQ/pack/refuse only) then `ask_path=generative` (ontology retrieve + Cortex compute, bind_plan on compute miss, execute-validate; skip pack). Cortex compute / OpenVault FreeRoute stay on the host. This script never sends keys.

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
Cloudflare **CF1010** (browser-signature ban) -> exit 3 BLOCKED, named as CF1010 not IAP.
`demo_fallback=true` or `ask_mode=demo` on `/health` -> exit 1 FAIL.
WRONG>0 (green planted refuse, demo fallback on an answer, transport error mid-pack) -> exit 1 FAIL.

**Transport (SCORE-CLIENT-01).** `--climb` / `--climb --ab` probe `/health` and POST `/v1/chat/ask` via **httpx** (`score_http`), not `urllib.request`. Bare urllib hits Cloudflare 403 CF1010 on `https://studio.netie.ai`. Durable origin is `https://studio.netie.ai/api`. Loopback `127.0.0.1:8090` is a host workaround, not the public measurement. If httpx still CF1010s: **Platform DevOps exception** (Bot Fight / WAF allow httpx/curl-class). This seat does not invent COMPLETE.

Cursor cloud / seats without Access cookies will see IAP 403 on `studio.netie.ai`. That is BLOCKED for this seat. Platform on prove / with IAP runs the score.

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
