# sqlglot currency gate (A2-05 #261)

Keywords: sqlglot, currency, unit, A2-05, dms-261, hard rule 6, swap scenario, generative_ask
Main idea: Founder chose option (a) sqlglot in DMS. Parser is isolated in `dms_executor/sql_currency.py` so it can be swapped without touching the rest of the ask path.

## Swap scenario (hard rule 6)

sqlglot is not a sixth port. It is a SQL parser used by one internal module to decide ABSTAIN vs continue on a named-currency question.

- **Why sqlglot:** regex/allowlist failed four adversarial rounds (CTE/alias shadowing, untraced aggregates, derived tables, comments, scalar subqueries, arithmetic, plain columns, quoted-dot identifiers). A parser that shares DuckDB's read of the statement is the class fix.
- **Swap for:** (1) Cortex HTTP compute "verify unit" once that exists, or (2) another dialect parser (DuckDB `extract_statements` + a lineage walker) behind the same module.
- **Isolation:** `packages/executor/dms_executor/sql_currency.py` is the only import of sqlglot. `generative_ask._submit_validated` calls `currency_gate_reason(question, sql, warehouse=...)`. Replacing the module keeps that one call.

No FX conversion. #262 (grain) lands after this.
