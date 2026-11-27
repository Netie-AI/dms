# UNICODE-IDENT-01

**Status:** Not PASS. Unit file `tests/test_unicode_ident.py` 6 passed with `--noconftest`. Full suite not run. Armed `grade52 --armed` and pack H not run. AI-vs-rules on the 52 was not measured.

## What

`DMS_UNICODE_IDENT` defaults to off. Only the digit `1` enables it. The flag is read when a name is folded, not at import.

Off, `ident_safe` is the previous ASCII fold (`销售额` and `库存表` both become `___`). CSV headers stay whatever DuckDB `read_csv` kept. Schema prompts for an ASCII catalog do not contain `source_name`. The answer envelope does not relabel row keys.

On, a name that still contains a non-ASCII character lands as `<ascii stem or t>_<8 hex of sha256(the full source string)>`. ASCII names, including `jumlah_jualan` and a dotted `dbo.table`, stay on the old fold. The original is `source_name` on the bronze registry row, the ingest receipt, and `schema_context` (`column: t_<hex>  source_name: <header>  type: ...`). Answer row keys are relabeled to that source name after masking. Two distinct CJK headers cannot share an identifier. Cortex is not imported: the contract test parses the landed SQL with sqlglot, checks each identifier against the shared ASCII pattern, and runs the SELECT in DuckDB.

DMS does not name a provider or a model.

## Ceiling

ponytail: an 8-hex collision inside one table gets a numeric suffix (`_2`, `_3`). The hash is of the full name, so this is a same-table safety valve, not the naming rule. The upgrade, if a live warehouse ever hits it, is a longer digest, not a word list.
