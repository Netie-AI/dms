"""Allowlisted warehouse table browse for Library (DbGate-inspired, read-only)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dms_executor.bronze import list_bronze_tables
from dms_executor.demo_warehouse import DEMO_TABLES, connect_readonly, ensure_demo_warehouse, warehouse_path

_ALLOWED = frozenset(DEMO_TABLES)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def list_warehouse_tables(*, path: Path | None = None) -> list[dict[str, Any]]:
    ensure_demo_warehouse(path)
    con = connect_readonly(path)
    out: list[dict[str, Any]] = []
    try:
        for table in DEMO_TABLES:
            try:
                n = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except Exception:  # noqa: BLE001
                n = 0
            out.append({"table": table, "row_count": n, "kind": "demo_warehouse"})
    finally:
        con.close()
    return out


def preview_warehouse_table(
    table: str,
    *,
    limit: int = 100,
    offset: int = 0,
    path: Path | None = None,
) -> dict[str, Any]:
    name = (table or "").strip().lower()
    if name not in _ALLOWED:
        raise ValueError(f"Unknown or disallowed table: {table!r}")
    lim = max(1, min(int(limit), 500))
    off = max(0, min(int(offset), 100_000))
    ensure_demo_warehouse(path)
    con = connect_readonly(path)
    try:
        total = int(con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        rel = con.execute(f"SELECT * FROM {name} LIMIT {lim} OFFSET {off}")
        cols = [d[0] for d in rel.description]
        rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()
    return {
        "table": name,
        "columns": cols,
        "rows": rows,
        "row_count": total,
        "limit": lim,
        "offset": off,
        "note": "Read-only demo warehouse preview — Excel remains source-only.",
    }


def _parse_bronze_ref(table: str) -> tuple[str, str]:
    raw = (table or "").strip()
    if "." in raw:
        schema, name = raw.split(".", 1)
    elif raw.startswith("bronze_"):
        schema, name = "main", raw
    else:
        schema, name = "bronze", raw
    if not _IDENT.match(schema) or not _IDENT.match(name):
        raise ValueError(f"Unknown or disallowed bronze table: {table!r}")
    return schema, name


def preview_bronze_table(
    table: str,
    *,
    limit: int = 100,
    offset: int = 0,
    path: Path | None = None,
) -> dict[str, Any]:
    """Read-only bronze preview — allowlist from list_bronze_tables only."""
    known = {t["table"] for t in list_bronze_tables(path=path)}
    label = (table or "").strip()
    if label not in known:
        candidates = [label, f"bronze.{label}"]
        if "." in label:
            candidates.append(label.split(".", 1)[-1])
        match = next((c for c in candidates if c in known), None)
        if match is None:
            raise ValueError(f"Unknown or disallowed bronze table: {table!r}")
        label = match
    schema, name = _parse_bronze_ref(label)
    lim = max(1, min(int(limit), 500))
    off = max(0, min(int(offset), 100_000))
    db = ensure_demo_warehouse(path or warehouse_path())
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        qual = f'"{schema}"."{name}"'
        total = int(con.execute(f"SELECT COUNT(*) FROM {qual}").fetchone()[0])
        rel = con.execute(f"SELECT * FROM {qual} LIMIT {lim} OFFSET {off}")
        cols = [d[0] for d in rel.description]
        rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()
    return {
        "table": label if "." in label else f"{schema}.{name}",
        "columns": cols,
        "rows": rows,
        "row_count": total,
        "limit": lim,
        "offset": off,
        "kind": "bronze",
        "note": "Read-only bronze lake preview — provenance columns attached at ingest.",
    }
