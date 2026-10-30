"""Palantir-lean Objects: DuckDB VIEWs with human column names over landed bronze.

The view is the object. The LLM never sees ``tbl_cust_v2``. Rows stay in bronze;
this layer only names them. Live MySQL VIEWs are not this module (DR-0005).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from dms_executor.demo_warehouse import ensure_demo_warehouse, warehouse_path
from dms_executor.lake_schema import ensure_lake_schemas

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Lake tables may start with a digit (Studio CSV stems like 01_clean_sales).
_LAKE_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_REL = re.compile(r"^(bronze|silver|gold)\.([A-Za-z0-9_]+)$")


class ObjectViewError(ValueError):
    """Bad object name, relation, or column mapping."""


@dataclass(frozen=True)
class ObjectViewSpec:
    """One object: ``objects.<name>`` selects aliased columns from a lake relation."""

    name: str
    relation: str
    columns: Mapping[str, str]  # human_name -> source_column


def _qi(name: str) -> str:
    if not _IDENT.match(name):
        raise ObjectViewError(f"not an identifier: {name!r}")
    return f'"{name}"'


def _qlake(name: str) -> str:
    if not _LAKE_IDENT.match(name):
        raise ObjectViewError(f"not an identifier: {name!r}")
    return f'"{name}"'


def _qrel(relation: str) -> str:
    m = _REL.match(relation)
    if not m:
        raise ObjectViewError(
            f"relation must be bronze|silver|gold.<table>, got {relation!r}"
        )
    return f"{_qi(m.group(1))}.{_qlake(m.group(2))}"


def publish_object_views(
    con: duckdb.DuckDBPyConnection,
    specs: Sequence[ObjectViewSpec],
) -> list[str]:
    """CREATE OR REPLACE VIEW objects.<name>. Returns published names."""
    if not specs:
        return []
    ensure_lake_schemas(con)
    published: list[str] = []
    for spec in specs:
        if not spec.columns:
            raise ObjectViewError(f"{spec.name!r} has no columns")
        parts = [
            f"{_qlake(src)} AS {_qi(human)}" for human, src in spec.columns.items()
        ]
        target = _qi("objects") + "." + _qi(spec.name)
        con.execute(
            f"CREATE OR REPLACE VIEW {target} AS SELECT {', '.join(parts)} "
            f"FROM {_qrel(spec.relation)}"
        )
        published.append(f"objects.{spec.name}")
    return published


def list_object_names(*, path: Path | None = None) -> list[str]:
    """Published ``objects.<name>`` views in the lake."""
    db = ensure_demo_warehouse(path or warehouse_path())
    con = duckdb.connect(str(db))
    try:
        ensure_lake_schemas(con)
        rows = con.execute(
            """
            SELECT table_name FROM information_schema.tables
             WHERE table_schema = 'objects'
             ORDER BY table_name
            """
        ).fetchall()
        out: list[str] = []
        for (name,) in rows:
            n = str(name)
            if n.startswith("_") or not _IDENT.match(n):
                continue
            out.append(f"objects.{n}")
        return out
    finally:
        con.close()


def publish_objects_at_path(
    specs: Sequence[ObjectViewSpec], *, path: Path | None = None
) -> list[str]:
    """Open the warehouse, publish views, close. Used by Studio."""
    db = ensure_demo_warehouse(path or warehouse_path())
    con = duckdb.connect(str(db))
    try:
        return publish_object_views(con, specs)
    finally:
        con.close()


def materialize_silver_from_object(name: str, *, path: Path | None = None) -> str:
    """Copy ``objects.<name>`` into ``silver.<name>``. Gold stays a signed promote."""
    if not _IDENT.match(name):
        raise ObjectViewError(f"not an identifier: {name!r}")
    db = ensure_demo_warehouse(path or warehouse_path())
    con = duckdb.connect(str(db))
    try:
        ensure_lake_schemas(con)
        src = f'"objects".{_qi(name)}'
        dst = f'"silver".{_qi(name)}'
        try:
            con.execute(f"CREATE OR REPLACE TABLE {dst} AS SELECT * FROM {src}")
        except duckdb.Error as exc:
            raise ObjectViewError(f"cannot arrange silver.{name}: {exc}") from exc
        return f"silver.{name}"
    finally:
        con.close()
