"""AskPayloadColumn.masked from DMS mask and PII flags (dms#456).

Cortex contract 1.9.0 (Cortex#386) samples a column's values only when every
listing says ``masked`` is the JSON boolean false. A missing flag counts as
masked. This module sets the flag when DMS builds that payload.

False only when the column is positively cleared: a boolean whose name is not
PII, or a numeric or date column whose name the mask treats as non-personal
and whose read values are not PII, or a text column with an ontology
``non_personal`` tag (source and field) whose read values are not PII.
Masked, PII, unread, or unknown columns are true. The value is always a
bool. Sample cells are not copied onto the payload and are not logged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from dms_core.pii import classify_column

from dms_executor.schema_context import (
    _cleared_value_blocked,
    _column_positively_cleared,
    _ontology_tags,
    _type_family,
)

_WIRE_COLUMN_KEYS = ("name", "type", "description")
_WIRE_TABLE_KEYS = ("name", "description")


def column_masked(
    table: str | None,
    column: str | None,
    *,
    type_name: str | None = None,
    values: Sequence[object] | None = None,
    tagged: bool = False,
) -> bool:
    """Strict bool. True when the column is masked, PII, or not known clear."""
    if not isinstance(table, str) or not table.strip():
        return True
    if not isinstance(column, str) or not column.strip():
        return True
    try:
        family = _type_family(type_name or "")
        sample = list(values) if values is not None else []
        if classify_column(column, sample, table=table) is not None:
            return True
        if family == "boolean":
            return False
        if family == "text":
            if not tagged or values is None:
                return True
            if any(_cleared_value_blocked(str(value)) for value in values):
                return True
            return False
        if not _column_positively_cleared(table, column, tagged=tagged):
            return True
        # Unread numeric or date values are an unknown mask state.
        if values is None:
            return True
        return False
    except Exception:  # noqa: BLE001 - doubt is masked
        return True


def stamp_ask_payload(
    payload: Mapping[str, Any] | None,
    *,
    ontology: Any = None,
    samples: Mapping[tuple[str, str], Sequence[object]] | None = None,
) -> dict[str, Any] | None:
    """Copy an AskPayload and set ``masked`` on every column.

    ``samples`` is ``(table, column) -> values`` used only to decide the
    flag. Those values are not written onto the copy. ``None`` stays None.
    """
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        return None
    tags = _ontology_tags(ontology)
    held = samples or {}
    out: dict[str, Any] = {"tables": _stamp_tables(payload.get("tables"), tags, held)}
    joins = payload.get("joins")
    if joins is not None:
        out["joins"] = joins
    return out


def _stamp_tables(
    tables: Any,
    tags: set[tuple[str, str]],
    samples: Mapping[tuple[str, str], Sequence[object]],
) -> list[dict[str, Any]]:
    if not isinstance(tables, list):
        return []
    stamped: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, Mapping):
            continue
        item = {key: table[key] for key in _WIRE_TABLE_KEYS if key in table}
        if "columns" in table:
            item["columns"] = _stamp_columns(table.get("name"), table.get("columns"), tags, samples)
        stamped.append(item)
    return stamped


def _stamp_columns(
    table: Any,
    columns: Any,
    tags: set[tuple[str, str]],
    samples: Mapping[tuple[str, str], Sequence[object]],
) -> list[dict[str, Any]]:
    if not isinstance(columns, list):
        return []
    table_name = table if isinstance(table, str) else None
    stamped: list[dict[str, Any]] = []
    for column in columns:
        if not isinstance(column, Mapping):
            continue
        column_name = column.get("name") if isinstance(column.get("name"), str) else None
        type_name = column.get("type") if isinstance(column.get("type"), str) else None
        tagged = (
            table_name is not None and column_name is not None and (table_name, column_name) in tags
        )
        # Missing from the samples map means the values were not read.
        values = samples.get((table_name, column_name)) if table_name and column_name else None
        item = {key: column[key] for key in _WIRE_COLUMN_KEYS if key in column}
        item["masked"] = column_masked(
            table_name,
            column_name,
            type_name=type_name,
            values=values,
            tagged=tagged,
        )
        stamped.append(item)
    return stamped


__all__ = ["column_masked", "stamp_ask_payload"]
