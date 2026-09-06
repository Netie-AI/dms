"""Medallion schemas in the serving DuckDB — bronze / silver / gold / quarantine."""

from __future__ import annotations

import threading

import duckdb

LAKE_SCHEMAS = ("bronze", "silver", "gold", "quarantine", "dim")

#: Catalog writes against one DuckDB file are not concurrency-safe, and
#: ``IF NOT EXISTS`` does not make them so: it only skips a schema that is
#: already *committed*, so two connections that start the same CREATE together
#: both write the catalog entry and DuckDB aborts the loser with
#: ``TransactionContext Error: Catalog write-write conflict on create with
#: "bronze"``. Library fires /tree twice, so the first load of a fresh warehouse
#: raced itself and one of the two requests 500ed. Same failure class the
#: ``_REGISTRY_LOCK`` in ``bronze.py`` already covers for the ingest registry —
#: this is the other catalog writer on that same hot path.
_SCHEMA_LOCK = threading.Lock()


def _schema_exists(con: duckdb.DuckDBPyConnection, schema: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = ?",
        [schema],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def ensure_lake_schemas(con: duckdb.DuckDBPyConnection) -> None:
    """Postcondition: every lake schema exists. Safe to call concurrently.

    Losing the race is not an error. The contract is that the schemas are there
    when this returns, not that *this* connection is the one that created them,
    so a conflict is swallowed only when the schema is in fact present
    afterwards — never blindly.
    """
    with _SCHEMA_LOCK:
        for schema in LAKE_SCHEMAS:
            try:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            except duckdb.Error:
                # The lock only covers this process; another process holding the
                # same file (the Cortex sidecar, a second worker) can still win.
                try:
                    settled = _schema_exists(con, schema)
                except duckdb.Error:
                    settled = False
                if not settled:
                    raise
