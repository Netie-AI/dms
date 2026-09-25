# SCORE-BIRD-01 -- measured live score on BIRD Space

**Ticket:** #184 under EPIC-020b #173. **Does not close** #184 or #173.
**Depends on:** SQLSRC-PG-01 #172 @ `d6b73122`; GEN-01 #179 @ `a9578348` (product ask path).
**Does not replace:** `score_answers.py` (hostile Excel) or `score_curated.py` (Genie pack / GEN-02 climb).

## Honesty (read this before quoting a number)

Platform SCORE-BIRD GO 2026-09-13: one postgres source on Space `f0da7dd3-58b3-4d15-84a8-a18f2853ed87`. First batch was `gender` `max_rows=50`. **Bronze grows in batches.** The pack snapshot is not a ceiling.

| Fact | Value |
|------|--------|
| Space | `f0da7dd3-58b3-4d15-84a8-a18f2853ed87` |
| source_count | 1 (one postgres source; table count grows) |
| data_source | `12b6f170` |
| kind / host | postgresql `bird_minidev` @ `127.0.0.1:5432` |
| baseline | `gender` (first GO batch; still required) |
| leftover target | **75** Mini-Dev tables. `--live` prints `measured=N leftover=75-N` from Studio bronze. |

A coverage claim over Mini-Dev / BIRD as a whole is leftover until measured bronze count reaches 75. Growing bronze is not COMPLETE. Do not invent **99.95%**. Do not invent EPIC-020b or EPIC-020 #108 COMPLETE. Do not clone DB-GPT. Keys stay in OpenVault.

WRONG=0 is the law. ABSTAIN on a baseline ask is a coverage cost. 0 answered prints precision `n/a`, not 100.00.

Leftover traps that name a Mini-Dev table **SKIP** once that table is in bronze (no invented oracle). `trap_75_tables` and demo-pack bleed stay refuse.

## Who certifies what

| Seat | What it may measure | What it must not claim |
|------|---------------------|------------------------|
| Platform on prove / Studio origin | `--live` against `DMS_API_BASE` + BIRD Space | EPIC-020b COMPLETE; 75-table coverage from a partial batch |
| Any seat | `--self-check` (CI) | live OK/LAYER/ABSTAIN/WRONG |
| Cursor cloud | self-check + offline `--ab` | prove `127.0.0.1:5432` / VPC Studio PASS |

`--ab` without `--live` scores **exact-match only** (demo pack must miss BIRD) and marks generative **BLOCKED**. That is not a live measurement.

## Env (fail closed -- no laptop default)

```
DMS_API_BASE    required for --live. API prefix (prove: http://127.0.0.1:8090
                or Studio same-origin https://<host>/api). No default.
BIRD_SCORE_URL  alias of DMS_API_BASE
BIRD_SPACE_ID   optional override; pack pins f0da7dd3-...
DMS_SCORE_DIR   optional artifact dir (default .tmp/score_bird.json)
```

Unset `DMS_API_BASE` on `--live` -> exit 2 CONFIG, not PASS.
`--live` 404 / connect fail -> exit 3 BLOCKED (`error.type=space_not_found` or `transport`). Do not tally those as WRONG.

`0.0.0.0` refused. Do not put passwords in the command or in chat.

## Run

```powershell
# Fail-closed self-check (no network, CI-safe):
python D:\DMS\scripts\score_bird.py --self-check

# Exact-match A/B lane only (generative BLOCKED -- not a live score):
python D:\DMS\scripts\score_bird.py --ab

# Agreed Platform command -- exact-match vs GEN-01 product path on Studio:
$env:DMS_API_BASE = "http://127.0.0.1:8090"   # prove loopback API
# or $env:DMS_API_BASE = "$env:STUDIO_ORIGIN/api"
python D:\DMS\scripts\score_bird.py --live
```

`--live` always A/B's:

1. **bronze list** -- `GET /v1/studio/bronze?space_id=` (fail -> pack snapshot). Prints measured/leftover. Does not invent 75.
2. **exact_match** -- demo pack / VQ-04 with empty grants (BIRD is not Finance). Must miss. Green on a leftover trap is WRONG. Landed leftover traps SKIP.
3. **generative_live** -- `POST /v1/chat/ask` on the BIRD Space. GEN-01 (#179) already sits on that path after pack miss. Report real OK/LAYER/ABSTAIN/WRONG.

GEN-02 #180 (curated coverage climb vs baseline @ `91c5cc99`) is a different pack: `python scripts/score_curated.py --ab` / `--live`. Do not quote that baseline as a BIRD number.

## Recorded walk

No live counts from this cloud seat. Prove postgres `bird_minidev` and Studio are Platform. Paste the `--live` table here when Platform runs it. Until then quote only `--self-check` and leftover target=75.

Must not: invent PASS, open public `:8090`, put OV keys in chat, green planted leftover traps, weaken `score_answers --oracle-only`, claim COMPLETE because a later batch attached more than gender.

## A1-02 Mini-Dev (#264)

Grades the real 500-question BIRD Mini-Dev JSON (11 databases) against gold SQL
executed read-only. The corpus is loaded at run time (path or URL) and is
never committed. `--self-check` uses `tests/fixtures/bird_minidev/synthetic.json`.

```
python scripts/score_bird.py --self-check
python scripts/score_bird.py --minidev mini_dev_postgresql.json --live
python scripts/score_bird.py --compare a.json b.json
```

Live prove:

- `DMS_API_BASE` -- POST `/v1/chat/ask` on the BIRD Space
- `BIRD_PG_DSN` (or `BIRD_PG_HOST`) -- gold SQL, read-only
- `CORTEX_FREEROUTE_LEARN=0` -- required until Cortex ROUTER-1
- `CORTEX_ROUTE_STORE` -- empty file or missing path (fresh). Snapshots
  (learn flag, path, fresh, row count / hash before and after) go in the
  artifact. Unset/1 or a dirty store is CONFIG: no scored result.
- `--offline` skips freeze (no Cortex)
- `--with-evidence` appends BIRD evidence (reported separately)
- `--limit N` is smoke and is printed; refusing a shrunk 500 without `--limit`

Numeric match is not 4 dp absolute: integers exact; other numbers use
`|a-b| <= max(1e-9, 1e-6 * max(|a|,|b|))`. 29+ digit numeric strings must not
crash. GOLD_ERROR excludes the question from n.

Each case copies Cortex ROUTER-1 (#269) fields `served_provider`,
`served_model`, and `served_local` when present. Absent fields are
`unknown` (boolean local is never guessed false). Today's Cortex main
does not return them yet. Run-level `served_mix` includes
`provider/model/local=...` plus `served_local` true/false/unknown counts.
`setup_fingerprint` covers learn flag, store state, and that mix.
`--compare` refuses different fingerprints unless `--force-cross-setup`,
which labels the result `cross-setup`.

First live run is a Platform baseline, not PASS. No target. Do not quote a
Mini-Dev percent from CI or this tree.
