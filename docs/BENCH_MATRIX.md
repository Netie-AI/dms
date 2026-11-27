# BENCH-MATRIX-01

One command runs a named dataset slice through `POST /v1/chat/ask` and writes one artifact per dataset. The first live run is a baseline. There is no accuracy target. Not PASS.

## Flag

`DMS_BENCH_MATRIX` defaults off. Only the digit `1` turns a live slice on. Off, the command sends no ask, writes no artifact, and does not change prompt bytes, the envelope, or the served route.

`--self-check` does not need the flag. It builds a five-question synthetic SQLite file in a temp dir, grades a stub ask, re-ingests Chinese and Malay headers, and checks the armed-run guard. It does not call the live route. CI runs it.

## Command

```
python scripts/bench_matrix.py --self-check

DMS_BENCH_MATRIX=1 python scripts/bench_matrix.py \
    --dataset bird_minidev_sqlite \
    --source /data/mini_dev_sqlite.json \
    --db-dir /data/bird_sqlite \
    --space <space-id> \
    --out .tmp/bench
```

`--url` or `DMS_API_BASE` is required for a live ask. There is no default host.

`--limit N` writes a smoke artifact. It is not a dataset score.

A manifest runs several slices and writes one artifact each:

```
{"slices": [
  {"dataset": "bird_minidev_sqlite", "source": "/data/mini_dev_sqlite.json",
   "db_dir": "/data/bird_sqlite", "space": "<space-id>"},
  {"dataset": "spider2_lite_sqlite", "source": "/data/spider2-lite",
   "space": "<space-id>"},
  {"dataset": "adventureworks_pg", "source": "/data/aw_questions.json",
   "pg_dsn_env": "AW_PG_DSN", "space": "<space-id>"}
]}
```

## Datasets

Nothing in this list is committed. The operator points `--source` at a file or directory on the machine.

| Dataset | What `--source` is | Gold |
|---|---|---|
| `bird_minidev_sqlite` | Mini-Dev JSON, HF `birdsql/bird_mini_dev` config `mini_dev_sqlite` | SQL on `--db-dir/<db_id>.sqlite` |
| `bird_minidev_mysql` | config `mini_dev_mysql` | SQL via `--mysql-dsn-env` (URL `mysql://...`, password only in that env) |
| `bird_minidev_pg` | config `mini_dev_pg` | SQL via `--pg-dsn-env` |
| `spider2_lite_sqlite` | dir with `questions.json` | `gold_csv` next to the result, or SQL on a sqlite file |
| `adventureworks_pg` | questions JSON the operator supplies | SQL via `--pg-dsn-env` |

A full BIRD slice must be 500 questions, 11 databases, and question ids 0 through 499, the same ids on each split. A smaller file needs `--limit` and is marked smoke.

Spider gold is a per-instance result CSV. The envelope rows are compared to that CSV. This is the evaluation idea in `refs/ReFoRCE/spider2-snow/evaluation_suite/evaluate.py`. The code is not copied.

## Header language

```
DMS_BENCH_MATRIX=1 python scripts/bench_matrix.py \
    --dataset bird_minidev_sqlite --source <json> \
    --csv-dir <csv-pack> --header-map <map.json> \
    --header-lang zh --space <space-id> --out .tmp/bench
```

`--header-lang` is `zh` or `ms`. The mapping file names tables and columns:

```
{"tables": {"widgets": {"zh": "部件", "ms": "widgetms"}},
 "columns": {"widgets.price": {"zh": "价格", "ms": "harga"}}}
```

The runner re-ingests the CSVs into a sqlite under `--out`. Cell values are copied. Question text is not edited. Gold SQL identifiers that the map names are renamed with sqlglot, then executed on that sqlite. The artifact field `gold_db` is the file name. Attach that file to the Space before reading the live numbers, so the model and the gold see the same headers.

## Armed runs

The command refuses to start when this matches:

```
pgrep -f "[a]b_run.sh|[g]rade52.py --armed"
```

The brackets keep pgrep from matching itself. A missing pgrep is also a refusal. Process output is not printed.

## Artifact

`<out>/<dataset>.json`, or `<dataset>.<lang>.json` when a header language is set.

Counts: `served_correct`, `wrong`, `reconfirm`, `abstain`, `gold_error`, `error`, and `n`. `pass` is false. `target` is null.

`wrong_served_upper_95` is the one-sided 95 percent Clopper-Pearson upper bound on WRONG among served answers (served correct + WRONG), in percent. Reconfirm and abstain are not served. Zero wrong uses the same bound as `scripts/score_heldout.py` (about 300/n). Nothing served stores null, and the printed line still carries `answered=` and the rule-of-three bound.

`seconds_p50` and `seconds_p95` are nearest-rank seconds over asks that were sent. Gold errors are not asks.

`ov_usage_usd` is the sum of `cost_usd`, `usd`, or `usage_usd` on `GET {OPENVAULT_URL}/api/usage` for events whose `run_label` or `label` equals this run. Other runs are not added. A ledger with no matching label stores null and `no_run_label_match`. Key fields are not copied. One page, limit 1000.

Cases store `id`, `verdict`, `seconds`, and `reason_code` only. A reason that is not a short code is stored as `unstructured`. Row values, question text, gold SQL, and secrets are not written.

## Not a word rule

The ask body is `{"question": <dataset question>, "space_id": <space>}`. The question string is the `question` or `instruction` field, copied. Headers change only when the mapping file names that table and column. Gold must be one read-only statement. DMS does not pick a provider or a model. OpenVault still routes.

## How the Lead measures it

Same SHA. Flag off, then flag on. Do not call either a pass. This PR does not change the ask path, so the 52 and pack H are the byte-identical check. The matrix slices are the new baseline.

```
python scripts/verify/grade52.py --armed --layout copy-only
DMS_BENCH_MATRIX=1 python scripts/verify/grade52.py --armed --layout copy-only

python scripts/verify/grade52.py --armed --layout copy-only --pack <pack-H-dir>
DMS_BENCH_MATRIX=1 python scripts/verify/grade52.py --armed --layout copy-only --pack <pack-H-dir>

DMS_BENCH_MATRIX=0 python scripts/bench_matrix.py --dataset bird_minidev_sqlite \
    --source <mini_dev_sqlite.json> --db-dir <dir> --space <space> --out .tmp/bench-off
DMS_BENCH_MATRIX=1 python scripts/bench_matrix.py --dataset bird_minidev_sqlite \
    --source <mini_dev_sqlite.json> --db-dir <dir> --space <space> --out .tmp/bench-on
```

Repeat the matrix pair for `bird_minidev_mysql` (`--mysql-dsn-env`), `bird_minidev_pg` (`--pg-dsn-env`), `spider2_lite_sqlite`, and `adventureworks_pg` (`--pg-dsn-env`). Then the same SHA with `--header-lang zh` and `--header-lang ms` on a CSV pack whose Space holds the re-ingested file.

`grade52 --armed` and pack H are not run by the writer.
