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
    ],
)
def test_hostile_sql_fails_at_executor(sql: str) -> None:
    with pytest.raises(SecurityEvent) as ei:
        reject_hostile_chat_sql(sql)
    assert ei.value.code in {"path_not_allowed", "sql_not_analyzable"}


def test_benign_select_passes_dms_gate() -> None:
    reject_hostile_chat_sql("SELECT id FROM orders WHERE tenant_id = 'acme'")
