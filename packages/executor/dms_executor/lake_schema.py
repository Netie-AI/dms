"""Medallion schemas in the serving DuckDB — bronze / silver / gold / quarantine."""

from __future__ import annotations

import threading

import duckdb

# Serializes the first CREATE so two cursors do not write-write conflict.
# After the schema exists, IF NOT EXISTS is cheap and safe to repeat.
catalog_lock = threading.Lock()


def ensure_lake_schemas(con: duckdb.DuckDBPyConnection) -> None:
    with catalog_lock:
        for schema in ("bronze", "silver", "gold", "quarantine", "dim"):
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
