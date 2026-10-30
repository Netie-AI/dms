# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regex allow/deny filtering of relations at catalog-extraction time.

Upstream applied this to pandas frames. Rows here are dicts. The match
rules are unchanged: deny wins, ``re.search`` on the unqualified name,
case-sensitive.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

ALLOW_KEY = "table_allow_regex"
DENY_KEY = "table_deny_regex"
FILTER_KEYS = (ALLOW_KEY, DENY_KEY)


class InvalidTableFilterError(ValueError):
    """Raised when an allow/deny pattern is not a valid regular expression."""


def compile_pattern(raw: Any, *, field: str) -> re.Pattern[str] | None:
    """Compile one pattern, or None when it is absent or blank."""
    if raw is None:
        return None
    pattern = str(raw).strip()
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise InvalidTableFilterError(f"{field} is not a valid regex: {exc}") from exc


@dataclass(frozen=True)
class TableFilter:
    """An allow/deny pair applied to unqualified relation names."""

    allow: re.Pattern[str] | None = None
    deny: re.Pattern[str] | None = None

    @property
    def active(self) -> bool:
        return self.allow is not None or self.deny is not None

    def keep(self, table_name: str) -> bool:
        if not table_name:
            return True
        if self.deny is not None and self.deny.search(table_name):
            return False
        if self.allow is not None and not self.allow.search(table_name):
            return False
        return True

    def describe(self) -> str:
        parts = []
        if self.allow is not None:
            parts.append(f"allow={self.allow.pattern!r}")
        if self.deny is not None:
            parts.append(f"deny={self.deny.pattern!r}")
        return ", ".join(parts) or "inactive"


def from_connection(connection: dict[str, Any] | None) -> TableFilter:
    """Build a filter from a connection dict. Inactive when neither key is set."""
    if not connection:
        return TableFilter()
    return TableFilter(
        allow=compile_pattern(connection.get(ALLOW_KEY), field="Table allowlist"),
        deny=compile_pattern(connection.get(DENY_KEY), field="Table denylist"),
    )


def filter_rows(
    rows: list[dict[str, Any]] | None,
    table_filter: TableFilter,
) -> list[dict[str, Any]]:
    """Drop rows whose ``table_name`` the filter excludes."""
    if not rows:
        return []
    if not table_filter.active:
        return list(rows)
    if rows and "table_name" not in rows[0]:
        return list(rows)
    return [
        row
        for row in rows
        if table_filter.keep("" if row.get("table_name") is None else str(row.get("table_name")))
    ]


def apply(
    tables: list[dict[str, Any]] | None,
    columns: list[dict[str, Any]] | None,
    views: list[dict[str, Any]] | None,
    table_filter: TableFilter,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter the three relation lists together."""
    if not table_filter.active:
        return list(tables or []), list(columns or []), list(views or [])
    before = 0 if not tables else len(tables)
    kept_tables = filter_rows(tables, table_filter)
    kept_columns = filter_rows(columns, table_filter)
    kept_views = filter_rows(views, table_filter)
    if before != len(kept_tables):
        logger.info(
            "Table filter (%s) kept %d of %d table(s)",
            table_filter.describe(),
            len(kept_tables),
            before,
        )
    return kept_tables, kept_columns, kept_views
