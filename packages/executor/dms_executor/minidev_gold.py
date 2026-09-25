"""Mini-Dev gold SQL for the A1-02 harness (DuckDB, self-check only).

Swap scenario: this module exists so ``scripts/`` never calls duckdb.execute
(hard rule 7). CI uses an in-memory DuckDB; live prove executes gold SQL on
PostgreSQL via psycopg in the scorer. Drop this module if CI gains a throwaway
Postgres Mini-Dev.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def duck_gold_rows(sql: str, *, setup: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Run optional DDL then one SELECT. Raises on SQL error."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        for stmt in setup:
            con.execute(stmt)
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description] if rel.description else []
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()
