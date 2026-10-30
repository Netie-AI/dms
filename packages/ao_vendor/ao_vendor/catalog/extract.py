# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pull schema entities out of a DuckDB connection.

Upstream ``extract_tabular_db_data`` took a SQLDatabase connector and
returned pandas frames. This copy reads ``information_schema`` and
``duckdb_constraints()`` through the connection the caller already holds.
It does not import duckdb and it does not open a connection.
"""

from __future__ import annotations

from typing import Any

from ao_vendor.catalog import table_filter
from ao_vendor.catalog.normalize import normalize_columns, normalize_tables

_SKIP_SCHEMAS = ("information_schema", "pg_catalog")


class IncompleteCatalogExtractionError(RuntimeError):
    """Raised when listed relations do not have a complete column inventory."""


def _query(conn: Any, sql: str) -> list[dict[str, Any]]:
    cursor = conn.execute(sql)
    description = cursor.description or []
    names = [col[0] for col in description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _relation_keys(rows: list[dict[str, Any]] | None) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in rows or []:
        schema = "" if row.get("table_schema") is None else str(row.get("table_schema"))
        table = "" if row.get("table_name") is None else str(row.get("table_name"))
        if table:
            keys.add((schema, table))
    return keys


def _validate_relation_column_coverage(
    tables: list[dict[str, Any]] | None,
    columns: list[dict[str, Any]] | None,
) -> None:
    """Fail closed when a listed relation has no described columns."""
    listed = _relation_keys(tables)
    if not listed:
        return
    described = _relation_keys(columns)
    missing = sorted(listed - described)
    if not missing:
        return
    preview = ", ".join(
        f"{schema + '.' if schema else ''}{table}" for schema, table in missing[:10]
    )
    suffix = "" if len(missing) <= 10 else f" (+{len(missing) - 10} more)"
    raise IncompleteCatalogExtractionError(
        f"Catalog extraction listed {len(listed)} relation(s), but "
        f"{len(missing)} had no described columns: {preview}{suffix}"
    )


def _names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    text = str(value).strip()
    return [text] if text else []


def _is_view(table_type: Any) -> bool:
    return "view" in str(table_type or "").casefold()


def _split_constraints(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pks: list[dict[str, Any]] = []
    fks: list[dict[str, Any]] = []
    for row in rows:
        kind = str(row.get("constraint_type") or "").casefold()
        schema = row.get("schema_name") or row.get("table_schema") or ""
        table = row.get("table_name") or ""
        columns = _names(row.get("constraint_column_names"))
        if kind == "primary key":
            for name in columns:
                pks.append(
                    {
                        "table_schema": str(schema),
                        "table_name": str(table),
                        "column_name": name,
                    }
                )
            continue
        if kind != "foreign key":
            continue
        targets = _names(row.get("referenced_column_names"))
        target_table = str(row.get("referenced_table") or "")
        for index, source in enumerate(columns):
            target_column = targets[index] if index < len(targets) else ""
            fks.append(
                {
                    "table_schema": str(schema),
                    "table_name": str(table),
                    "source_column": source,
                    "target_table": target_table,
                    "target_column": target_column,
                }
            )
    return pks, fks


def extract_duckdb(
    conn: Any,
    *,
    connection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read tables, columns, declared keys, and views from ``conn``.

    ``connection`` may carry ``table_allow_regex`` / ``table_deny_regex``.
    An empty connection filter keeps every relation in ``main``.
    """
    schema_list = ", ".join(repr(name) for name in _SKIP_SCHEMAS)
    tables_sql = (
        "SELECT table_schema, table_name, table_type "
        "FROM information_schema.tables "
        f"WHERE table_schema NOT IN ({schema_list})"
    )
    columns_sql = (
        "SELECT table_schema, table_name, column_name, ordinal_position, "
        "data_type, is_nullable "
        "FROM information_schema.columns "
        f"WHERE table_schema NOT IN ({schema_list})"
    )
    raw_tables = _query(conn, tables_sql)
    raw_columns = _query(conn, columns_sql)
    base = [row for row in raw_tables if not _is_view(row.get("table_type"))]
    views = [row for row in raw_tables if _is_view(row.get("table_type"))]
    filt = table_filter.from_connection(connection)
    base, raw_columns, views = table_filter.apply(base, raw_columns, views, filt)
    _validate_relation_column_coverage(base, raw_columns)
    constraints = _query(
        conn,
        "SELECT schema_name, table_name, constraint_type, "
        "constraint_column_names, referenced_table, referenced_column_names "
        "FROM duckdb_constraints()",
    )
    pks, fks = _split_constraints(constraints)
    database_name = "memory"
    try:
        found = conn.execute("SELECT current_database()").fetchone()
    except Exception:
        found = None
    if found and found[0]:
        database_name = str(found[0])
    return {
        "database_name": database_name,
        "tables": normalize_tables(base),
        "columns": normalize_columns(raw_columns),
        "views": normalize_tables(views),
        "pks": pks,
        "fks": fks,
        "queries": [],
    }
