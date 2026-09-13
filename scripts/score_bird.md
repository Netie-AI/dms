# SCORE-BIRD-01 -- measured live score on BIRD Space

**Ticket:** #184 under EPIC-020b #173. **Does not close** #184 or #173.
**Depends on:** SQLSRC-PG-01 #172 @ `d6b73122`; GEN-01 #179 @ `a9578348` (product ask path).
**Does not replace:** `score_answers.py` (hostile Excel) or `score_curated.py` (Genie pack / GEN-02 climb).

## Honesty (read this before quoting a number)

Platform SCORE-BIRD GO 2026-09-13 attached **one** table:

| Fact | Value |
|------|--------|
| Space | `f0da7dd3-58b3-4d15-84a8-a18f2853ed87` |
| source_count | 1 |
| data_source | `12b6f170` |
| kind / host | postgresql `bird_minidev` @ `127.0.0.1:5432` |
| bounded attach | `tables=[gender]` `max_rows=50` |
| leftover | full **75-table** Mini-Dev extract |

A coverage claim over Mini-Dev / BIRD as a whole is leftover until Platform attaches the rest. Do not invent **99.95%**. Do not invent EPIC-020b or EPIC-020 #108 COMPLETE. Do not clone DB-GPT. Keys stay in OpenVault.

WRONG=0 is the law. ABSTAIN on a gender ask is a coverage cost, not a silent PASS with a made-up percent. 0 answered prints precision `n/a`, not 100.00.

## Who certifies what

| Seat | What it may measure | What it must not claim |
|------|---------------------|------------------------|
| Platform on prove / Studio origin | `--live` against `DMS_API_BASE` + BIRD Space | EPIC-020b COMPLETE; 75-table coverage |
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

1. **exact_match** -- demo pack / VQ-04 with empty grants (BIRD is not Finance). Must miss. Green on a leftover trap is WRONG.
2. **generative_live** -- `POST /v1/chat/ask` on the BIRD Space. GEN-01 (#179) already sits on that path after pack miss. Report real OK/LAYER/ABSTAIN/WRONG.

GEN-02 #180 (curated coverage climb vs baseline @ `91c5cc99`) is a different pack: `python scripts/score_curated.py --ab` / `--live`. Do not quote that baseline as a BIRD number.

## Recorded walk

No live counts from this cloud seat. Prove postgres `bird_minidev` and Studio are Platform. Paste the `--live` table here when Platform runs it. Until then quote only `--self-check` and leftover=75.

Must not: invent PASS, open public `:8090`, put OV keys in chat, green planted leftover traps, weaken `score_answers --oracle-only`.
