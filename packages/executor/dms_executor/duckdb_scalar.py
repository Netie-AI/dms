"""Narrow helpers for DuckDB fetchone() — typed as tuple | None."""

from __future__ import annotations

from typing import Any


def fetchone_row(row: tuple[Any, ...] | None) -> tuple[Any, ...]:
    if row is None:
        raise RuntimeError("expected DuckDB query to return a row")
    return row


def scalar_int(row: tuple[Any, ...] | None) -> int:
    return int(fetchone_row(row)[0])
