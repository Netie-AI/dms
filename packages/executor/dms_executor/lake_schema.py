"""Medallion schemas in the serving DuckDB — bronze / silver / gold / quarantine."""

from __future__ import annotations

import duckdb


def ensure_lake_schemas(con: duckdb.DuckDBPyConnection) -> None:
    for schema in ("bronze", "silver", "gold", "quarantine", "dim"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
