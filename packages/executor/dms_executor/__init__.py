"""Query execution — the only package allowed to call duckdb.execute."""

from __future__ import annotations

from typing import Any

from dms_core.ports import ServingEnginePort


class Executor:
    """Skeleton serving engine. Real DuckDB wiring lands in a later slice."""

    def execute(self, sql: str) -> Any:
        raise NotImplementedError("T0 skeleton — no DuckDB yet")


def get_serving_engine() -> ServingEnginePort:
    return Executor()
