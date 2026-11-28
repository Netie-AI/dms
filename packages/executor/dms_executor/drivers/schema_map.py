"""Compact schema map. Names and types only. Cell values are dropped."""

from __future__ import annotations

from typing import Any

from dms_executor.drivers.limits import CATALOG_CAP, COLUMN_CAP


def compact_schema(dialect: str, objects: list[dict[str, Any]]) -> dict[str, Any]:
    """One map the model can see. Extra keys on an object or a column are not copied."""
    kept: list[dict[str, Any]] = []
    truncated = False
    ordered = sorted(objects, key=lambda item: str(item.get("name") or ""))
    for item in ordered:
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        if len(kept) >= CATALOG_CAP:
            truncated = True
            break
        columns: list[dict[str, str]] = []
        raw_columns = item.get("columns") or []
        if not isinstance(raw_columns, list):
            raw_columns = []
        for column in raw_columns:
            if not isinstance(column, dict):
                continue
            col_name = column.get("name")
            col_type = column.get("type")
            if not isinstance(col_name, str) or not col_name:
                continue
            if not isinstance(col_type, str) or not col_type:
                col_type = "UNKNOWN"
            if len(columns) >= COLUMN_CAP:
                truncated = True
                break
            columns.append({"name": col_name, "type": col_type[:80]})
        entry: dict[str, Any] = {"name": name, "columns": columns}
        if item.get("over_byte_cap") is True:
            entry["over_byte_cap"] = True
        kept.append(entry)
    return {
        "dialect": dialect,
        "read_only": True,
        "objects": kept,
        "truncated_catalog": truncated,
    }


def prompt_lines(dialect: str, tables: list[str], schema: dict[str, Any] | None) -> str:
    """Table and column names. No cell values, including when ``schema`` is empty."""
    lines = [f"DIALECT: {dialect}", "SCHEMA"]
    by_name: dict[str, list[dict[str, str]]] = {}
    if schema:
        for obj in schema.get("objects") or []:
            if isinstance(obj, dict) and isinstance(obj.get("name"), str):
                by_name[obj["name"]] = list(obj.get("columns") or [])
    if not schema:
        lines.extend(f"- {name}" for name in tables)
        return "\n".join(lines)
    for name in tables:
        lines.append(f"- {name}")
        for column in by_name.get(name, []):
            lines.append(f"  - {column['name']} {column['type']}")
    if schema.get("truncated_catalog") is True:
        lines.append("TRUNCATED_CATALOG: 1")
    return "\n".join(lines)
