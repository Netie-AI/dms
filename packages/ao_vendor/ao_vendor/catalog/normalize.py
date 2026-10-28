# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Normalise raw catalog rows into the shapes the compiler expects.

Upstream did this with pandas and numpy. The coercion rules are the same.
Rows are dicts so this package does not take a dataframe dependency.
"""

from __future__ import annotations

from typing import Any

from ao_vendor.catalog.constants import TableTypes

_TABLE_KEYS = ("table_schema", "table_name", "table_type", "created", "description")
_COLUMN_KEYS = (
    "table_schema",
    "table_name",
    "column_name",
    "ordinal_position",
    "data_type",
    "is_nullable",
    "description",
)
_NULLABLE_TRUE = {"YES", "TRUE", "T", "Y", "1"}
_NULLABLE_FALSE = {"NO", "FALSE", "F", "N", "0"}


def flat_list_recursive(nested_list: list[Any]) -> list[Any]:
    output: list[Any] = []
    for item in nested_list:
        if isinstance(item, list):
            output.extend(flat_list_recursive(item))
        else:
            output.append(item)
    return output


def chunks(lst: list[Any], n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _blank(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def coerce_nullable(value: object) -> bool | None:
    """Coerce one ``is_nullable`` value to a boolean.

    ``YES``/``NO`` text and real booleans are accepted. Anything else raises.
    A missing value stays None.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in _NULLABLE_TRUE:
        return True
    if text in _NULLABLE_FALSE:
        return False
    if text == "":
        return None
    raise ValueError(
        f"is_nullable must be a boolean (or 'YES'/'NO'); got {value!r}. "
        "Convert it in the connector."
    )


def normalize_tables(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize table rows. Missing ``table_type`` becomes a base table."""
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = {key: raw.get(key) for key in _TABLE_KEYS}
        if _blank(row["table_type"]):
            row["table_type"] = TableTypes.BASE_TABLE
        else:
            row["table_type"] = str(row["table_type"]).strip().casefold()
        for key in ("table_schema", "table_name", "created", "description"):
            if row[key] is not None:
                row[key] = str(row[key])
        out.append(row)
    return out


def normalize_columns(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize column rows. ``is_nullable`` is a bool or None."""
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = {key: raw.get(key) for key in _COLUMN_KEYS}
        position = row["ordinal_position"]
        row["ordinal_position"] = int(position) if position is not None and position != "" else None
        row["is_nullable"] = coerce_nullable(row["is_nullable"])
        for key in ("table_schema", "table_name", "column_name", "data_type", "description"):
            if row[key] is not None:
                row[key] = str(row[key])
        out.append(row)
    return out
