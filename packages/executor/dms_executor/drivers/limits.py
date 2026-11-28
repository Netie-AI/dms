"""Caps for a governed connector read.

A source object larger than ``BYTE_CAP`` is refused. Aggregating a prefix
would publish a wrong total. A result that hits ``ROW_CAP`` or ``BYTE_CAP``
is returned with ``truncated`` set, so the cut is visible.

ponytail: the caps are constants. Upgrade path is one steward setting on the
source row, once the connect book has a column for it.
"""

from __future__ import annotations

import json
from typing import Any

ROW_CAP = 1_000
BYTE_CAP = 1_000_000
STATEMENT_TIMEOUT_S = 30
CONNECT_TIMEOUT_S = 5
CATALOG_CAP = 200
COLUMN_CAP = 80


def clip_records(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, int]:
    """Keep rows that fit in both caps. The returned flag is the cut."""
    kept: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for row in rows:
        if len(kept) >= ROW_CAP:
            truncated = True
            break
        size = len(json.dumps(row, default=str, separators=(",", ":")).encode("utf-8"))
        if used + size > BYTE_CAP:
            truncated = True
            break
        kept.append(row)
        used += size
    return kept, truncated, used


def result(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The connect query shape. ``truncated`` is true only when a cap cut rows."""
    kept, truncated, _used = clip_records(rows)
    body: dict[str, Any] = {
        "ok": True,
        "error": None,
        "warning": None,
        "estimate": None,
        "columns": list(columns),
        "rows": kept,
        "complete": not truncated,
        "next_offset": None,
        "cancelled": False,
        "truncated": truncated,
        "bounded": truncated,
    }
    if schema is not None:
        body["schema_map"] = schema
    return body
