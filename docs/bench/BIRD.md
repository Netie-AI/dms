# BIRD Mini-Dev runner

BIRD-HARNESS-CI-01. This is a measurement script. It is not a product change
and it is not a score to quote as PASS.

The 2024-06 release is SQLite, 11 databases, 500 questions. It is downloaded
at run time from the public zip and never committed:

```
https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip
```

Last-Modified on that object is 2024-06-20. Layout after unpack:

```
minidev/MINIDEV/mini_dev_sqlite.json
minidev/MINIDEV/dev_databases/<db_id>/<db_id>.sqlite
```

## Flag

`DMS_BIRD_HARNESS` defaults off. Only the digit `1` turns `fetch`, `ingest`,
`run`, and `nightly` on. Off, those commands send no ask, write no artifact,
and do not change prompt bytes, the envelope, or the served route.
`self-check` and `score` are local and do not need the flag.

This flag does not sit on `POST /v1/chat/ask`. The ask path is unchanged.

## Commands

```
python scripts/bench/bird/__main__.py self-check

DMS_BIRD_HARNESS=1 python scripts/bench/bird/__main__.py fetch --dest /tmp/bird

DMS_SQLITE_SOURCE_ROOT=/tmp/bird/minidev/MINIDEV/dev_databases \
DMS_API_BASE=http://127.0.0.1:8090 \
DMS_BIRD_HARNESS=1 \
python scripts/bench/bird/__main__.py ingest \
    --db-root /tmp/bird/minidev/MINIDEV/dev_databases \
    --out /tmp/bird/spaces.json

DMS_BIRD_HARNESS=1 python scripts/bench/bird/__main__.py run \
    --arm dms \
    --questions /tmp/bird/minidev/MINIDEV/mini_dev_sqlite.json \
    --db-root /tmp/bird/minidev/MINIDEV/dev_databases \
    --spaces /tmp/bird/spaces.json \
    --out /tmp/bird/dms.jsonl \
    --concurrency 4 \
    --evidence off

DMS_BIRD_HARNESS=1 python scripts/bench/bird/__main__.py run \
    --arm baseline \
    --questions /tmp/bird/minidev/MINIDEV/mini_dev_sqlite.json \
    --db-root /tmp/bird/minidev/MINIDEV/dev_databases \
    --out /tmp/bird/baseline.jsonl

python scripts/bench/bird/__main__.py score --jsonl /tmp/bird/dms.jsonl
```

`--limit N` is a smoke run. It is printed as smoke and is not a Mini-Dev score.
A file that is not 500 questions over 11 databases is refused unless `--limit`
is set.

There is no default host. `DMS_API_BASE` is required for `dms`.
`OPENVAULT_URL` and an `ov_` key (`OPENVAULT_API_KEY` or `CORTEX_API_KEY`) are
required for `baseline`. The key is not printed.

## What the arms do

`ingest` creates one Space per database (`POST /v1/spaces`) and attaches the
file with `POST /v1/studio/sources/sql` and `kind=sqlite`. The database field
is the path relative to `DMS_SQLITE_SOURCE_ROOT`. The API process needs that
variable too. A 500 from space create is `space_create_failed`: a new tenant
needs a tenant row first (FDE-TENANT-01). This script does not insert one.

The product extract still has its own row ceiling. This runner does not add
another cap. Gold and predicted SQL run with `PRAGMA query_only` and
`fetchall` on the source file, with no statement timeout. A write is
`destructive_sql` and is not an EX miss.

Chat reads the serving warehouse, which can be a different file from the
ingest warehouse. The API route syncs bronze after a successful extract. Gold
EX does not read that copy. It reads the source SQLite file.

`run --arm dms` posts `{"question", "space_id"}` to `/v1/chat/ask`. The
question string is the dataset question. `--evidence on` appends the dataset
`evidence` field. It does not rewrite the question from a word list.
`--no-yes` leaves an ask-back as ASKBACK. The default sends Yes to
`/v1/chat/ask/confirm` when the reconfirm block has a token and a non-empty
suggested question.

`run --arm baseline` is the raw model control. The first call uses role
string `sql`, schema text plus 3 sample rows. No provider and no model id
are sent. A 402 or 429 sends role `escalate` before one retry of that first
call. The returned SQL is executed on the source file and graded by the same
function as the dms arm.

## Score

Official EX is `set(predicted_rows) == set(gold_rows)`, the comparison in
bird-bench/mini_dev `evaluation_ex.calculate_ex`. Column order is the tuple
order. Duplicate rows collapse. Floats are not rounded.

`ex_norm` is the same set comparison after a canonical cell: blank text is
null, text is stripped and case-folded, and non-integers snap to 6 decimal
places. It is reported beside EX. A CORRECT outcome uses official EX only.

Outcomes: CORRECT, WRONG, ASKBACK, ABSTAIN, ERROR. An ask-back with a
non-empty suggestion is ASKBACK (unless Yes resolved it). An abstain without
that suggestion is ABSTAIN. A destructive statement is ABSTAIN. Anything else
that does not run is ERROR.

AI versus rule uses `path_group` in `scripts/verify/grade52.py`. p50 and p95
are nearest-rank seconds over every attempted id.

EX is correct / n_total. n_total is every attempted id. A 402 or 429 that is
still unresolved after the ladder below is infra_unscored. It stays in
n_total. It is not a CORRECT. ex_scored_only is correct / n_scored and is
printed on its own line labelled secondary. A run with infra_unscored above
0 is INCOMPLETE. That run is not a headline EX. Quote INCOMPLETE, not the
secondary line.

A record whose HTTP status is 402 or 429, or whose error code contains those
tokens, is not answered by a raw model completion. The dms arm retries
`POST /v1/chat/ask` with role `escalate` (no provider and no model id).
OpenVault routes that role. The graded body is the envelope DMS returns, so
it has been through the ask pipeline. A chat-completion body is not executed
and is not a CORRECT. One plain retry of the original POST is the last rung.
The baseline arm sends role `escalate` on its own OpenVault call, then one
retry of the original call. An id that is still 402 or 429 is
infra_unscored. A later run climbs it again. Finished ids are skipped.

The JSONL stores ids, outcomes, and codes. It does not store row values, SQL,
question text, or keys.

`pass` is false. `target` is null. WRONG stays visible. This command does not
turn a run into a pass.

## Nightly

`.github/workflows/bird-nightly.yml` has a cron (`15 18 * * *` UTC) and a
manual dispatch. The job does not run unless the repo variable
`DMS_BIRD_HARNESS` is `1`. With that variable unset, GitHub skips the job.

On this hosted runner, `DMS_API_BASE` and `OPENVAULT_URL` are absent, so
`nightly` exits 2 before any download. That exit is CONFIG, not a score.

When those settings are present, `nightly` downloads the zip into the runner
temp (never the repo) and scores. The dms arm also needs
`DMS_SQLITE_SOURCE_ROOT` to be the unpacked `dev_databases` directory, because
the API reads the files from its own disk. A mismatch exits 2 and writes no
score. The baseline arm needs `OPENVAULT_URL` and an `ov_` key. The printed
line has `pass=false`. The GitHub job timeout is the platform cap. SQL
execution in this runner has no statement timeout.

## 52 pack

This runner does not change the ask path, so the stub 52 is the byte-identical
check. Run it with the flag off and with `DMS_BIRD_HARNESS=1`. Armed grade52
and pack H are for the Lead. A run that adds a rule or grows rule-served
answers is rejected.

```
python scripts/verify/grade52.py --main
DMS_BIRD_HARNESS=1 python scripts/verify/grade52.py --main
```
