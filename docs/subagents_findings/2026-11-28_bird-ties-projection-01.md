# BIRD-TIES-PROJECTION-01

**Status:** Not PASS. Unit file `tests/test_tie_projection_01.py` passed. Stub `grade52 --main` measured at `4c5b50a`, flag off and on: correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20. Armed unmeasured (stub). Not PASS.

## What

`DMS_TIE_PROJECTION` defaults off. Off, the writer prompt, the envelope object, and the route stay as they were.

On, a served `ORDER BY ... LIMIT n` is executed once more without the LIMIT, read-only, through the same grant gate. Rows that share the rank key at the cutoff are all returned, and the answer text says so. A trailing bare column is a tie-break, so `ORDER BY COUNT(*) DESC, name` ties on the count. A tied set longer than one page is paged (`complete` false, `next_offset` set, `truncated` false). The page is not a cap.

The SQL writer also returns `asked_columns`. Served rows keep those columns. Other measures move to `context_columns`, and `sql_used` selects only the asked columns. Two readings that execute to different row sets become a clarify with both counts. The same row set is not a clarify. Destructive SQL and an ungranted table abstain. Any other unread reading reconfirms.

## Not a word rule

The checks use the SQL shape, the writer's column list, and the executed row sets. They do not read the question text.

## Ceiling

ponytail: one rank key is rewritten into SQL. Several rank keys expand the rows and leave `sql_used` as the model wrote it. A sqlglot miss, or a probe that does not execute, leaves the served rows. Upgrade: the model names the rank keys, and the serving engine parses the probe.
