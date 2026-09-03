"""Allowlisted warehouse table browse for Library (DbGate-inspired, read-only)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dms_executor.bronze import list_bronze_tables
from dms_executor.demo_warehouse import (
    DEMO_TABLES,
    connect_readonly,
    ensure_demo_warehouse,
    warehouse_path,
)
from dms_executor.duckdb_scalar import scalar_int

_ALLOWED = frozenset(DEMO_TABLES)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def list_warehouse_tables(
    *,
    path: Path | None = None,
    space_id: str | None = None,
) -> list[dict[str, Any]]:
    """List demo warehouse tables under ``space_id``, or the company default scope.

    ``space_id=None`` is the company default ACL, not the absence of a check. It used
    to allowlist every table in ``DEMO_TABLES``, which included ``alerts`` - a table no
    Space grants and every named Space refuses. See ``company_default_tables``.
    """
    ensure_demo_warehouse(path)
    from dms_executor.demo_grants import (
        COMPANY_SCOPED,
        DEMO_SPACE_GRANTS,
        canonical_space_id,
        company_default_tables,
    )

    if space_id:
        entry = DEMO_SPACE_GRANTS.get(canonical_space_id(space_id))
        if entry:
            allowed = frozenset(entry[1])
        else:
            # Unknown Space: company-scoped reference tables only (no other Space's facts).
            allowed = frozenset(COMPANY_SCOPED)
    else:
        allowed = frozenset(company_default_tables())
    con = connect_readonly(path)
    out: list[dict[str, Any]] = []
    try:
        for table in DEMO_TABLES:
            if table not in allowed:
                continue
            try:
                n = scalar_int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone())
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
        total = scalar_int(con.execute(f"SELECT COUNT(*) FROM {name}").fetchone())
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
    if not _IDENT.match(schema) or not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"Unknown or disallowed bronze table: {table!r}")
    return schema, name


def _resolve_bronze_label(table: str, known: set[str]) -> str:
    """Map any tree / UI label to the canonical name from list_bronze_tables."""
    label = (table or "").strip()
    if label in known:
        return label
    candidates: list[str] = [label]
    if label.startswith("bronze:"):
        candidates.append(label.removeprefix("bronze:"))
    if label.startswith("bronze."):
        candidates.append(label.removeprefix("bronze."))
    candidates.append(f"bronze.{label}")
    if "." in label:
        candidates.append(label.split(".", 1)[-1])
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        if cand in known:
            return cand
    raise ValueError(f"Unknown or disallowed bronze table: {table!r}")


def preview_bronze_table(
    table: str,
    *,
    limit: int = 100,
    offset: int = 0,
    path: Path | None = None,
) -> dict[str, Any]:
    """Read-only bronze preview — allowlist from list_bronze_tables only."""
    known = {t["table"] for t in list_bronze_tables(path=path)}
    label = _resolve_bronze_label(table, known)
    schema, name = _parse_bronze_ref(label)
    lim = max(1, min(int(limit), 500))
    off = max(0, min(int(offset), 100_000))
    db = ensure_demo_warehouse(path or warehouse_path())
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        qual = f'"{schema}"."{name}"'
        total = scalar_int(con.execute(f"SELECT COUNT(*) FROM {qual}").fetchone())
        rel = con.execute(f"SELECT * FROM {qual} LIMIT {lim} OFFSET {off}")
        cols = [d[0] for d in rel.description]
        rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        source = None
        extracted_at = None
        try:
            # filename is the file path for a CSV ingest, and the credential-free
            # source string for a SQL pull (DR-0005 part 4). created_at is extracted_at.
            reg = con.execute(
                "SELECT filename, created_at FROM bronze._ingest_registry WHERE table_name = ?",
                [name],
            ).fetchone()
            if reg is not None:
                source = None if reg[0] is None else str(reg[0])
                extracted_at = None if reg[1] is None else str(reg[1])
        except Exception:  # noqa: BLE001 - registry may not exist yet
            source = None
            extracted_at = None
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
        "source": source,
        "extracted_at": extracted_at,
        "note": "Read-only bronze lake preview — provenance columns attached at ingest.",
    }
