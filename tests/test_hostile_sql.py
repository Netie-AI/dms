"""Hostile SQL from the DMS chat path — executor rejects before Cortex/LLM/UI."""

from __future__ import annotations

import pytest
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_parquet('lake/secrets/*.parquet')",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM parquet_scan('s3://bucket/x')",
        "ATTACH 'secret.db' AS s",
        "SELECT * FROM UNNEST([{x:1}]) AS orders(x) JOIN secrets s ON s.id = orders.x",
        # unknown FROM-position file function (C3 red team)
        "SELECT * FROM read_json_auto('lake/hidden/**')",
        "COPY orders TO 'out.csv'",
        "INSTALL httpfs; LOAD httpfs",
        "PRAGMA show_tables",
        "SELECT * FROM read_parquet('\\\\evil\\share\\x.parquet')",
        "SELECT * FROM read_csv_auto('file:///C:/Windows/win.ini')",
        "CREATE TABLE pwned AS SELECT 1",
        "DROP TABLE transactions",
        "DELETE FROM transactions WHERE TRUE",
        "UPDATE transactions SET qty = 0",
        "INSERT INTO transactions VALUES (1)",
        "SELECT * FROM read_parquet(chr(47)||'etc/passwd')",
    ],
)
def test_hostile_sql_fails_at_executor(sql: str) -> None:
    with pytest.raises(SecurityEvent) as ei:
        reject_hostile_chat_sql(sql)
    assert ei.value.code in {
        "path_not_allowed",
        "sql_not_analyzable",
        "statement_not_allowed",
    }


def test_benign_select_passes_dms_gate() -> None:
    reject_hostile_chat_sql("SELECT id FROM orders WHERE tenant_id = 'acme'")


def test_benign_aggregate_passes() -> None:
    reject_hostile_chat_sql(
        "SELECT sku, SUM(revenue_myr) AS rev FROM transactions "
        "GROUP BY sku ORDER BY rev DESC LIMIT 5"
    )
