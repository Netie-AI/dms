"""Contract inference — propose a contract from observed bronze; never auto-apply."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
from dms_core.pipelines import ContractProposal

from dms_executor.demo_warehouse import ensure_demo_warehouse, warehouse_path
from dms_executor.duckdb_scalar import fetchone_row, scalar_int
from dms_executor.pipeline_loader import PipelineLoadError


def infer_contract(
    source: str,
    *,
    path: Path | None = None,
    sample_limit: int = 5000,
) -> ContractProposal:
    """Observe types, null rates, and candidate keys. Propose only."""
    if "." not in source:
        raise PipelineLoadError(f"source must be schema.table, got {source!r}")
    schema, table = source.split(".", 1)
    db = ensure_demo_warehouse(path or warehouse_path())
    con = duckdb.connect(str(db), read_only=True)
    try:
        exists = con.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            """,
            [schema, table],
        ).fetchone()
        if not exists or int(exists[0]) < 1:
            raise PipelineLoadError(f"source table missing: {source}")

        cols = con.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            ORDER BY ordinal_position
            """,
            [schema, table],
        ).fetchall()
        _provenance = ("_src", "_ingest_id", "_src_row", "_src_ref_id")
        biz = [(c, t) for c, t in cols if c not in _provenance]
        n = scalar_int(con.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone())
        null_rates: dict[str, float] = {}
        columns: dict[str, dict[str, Any]] = {}
        for col, dtype in biz:
            nn = scalar_int(
                con.execute(
                    f'SELECT COUNT("{col}") FROM "{schema}"."{table}"'
                ).fetchone()
            )
            rate = 1.0 - (nn / n) if n else 0.0
            null_rates[col] = round(rate, 4)
            proposed_type = _map_type(str(dtype))
            columns[col] = {
                "type": proposed_type,
                "required": rate < 0.01,
                "observed_duckdb_type": str(dtype),
                "null_rate": round(rate, 4),
            }

        candidate_keys = _candidate_keys(con, schema, table, [c for c, _ in biz], n)
        return ContractProposal(
            source=source,
            columns=columns,
            candidate_keys=candidate_keys,
            null_rates=null_rates,
            row_count=n,
        )
    finally:
        con.close()


def _map_type(dtype: str) -> str:
    d = dtype.upper()
    if "DATE" in d and "TIME" not in d:
        return "date"
    if "TIMESTAMP" in d:
        return "timestamp"
    if "INT" in d:
        return "integer"
    if "DECIMAL" in d or "NUMERIC" in d or "DOUBLE" in d or "FLOAT" in d or "HUGEINT" in d:
        return "decimal(18,2)"
    return "text"


def _candidate_keys(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    cols: list[str],
    n: int,
) -> list[list[str]]:
    if n == 0:
        return []
    singles: list[list[str]] = []
    for col in cols:
        distinct = scalar_int(
            con.execute(
                f'SELECT COUNT(DISTINCT "{col}") FROM "{schema}"."{table}"'
            ).fetchone()
        )
        nulls = scalar_int(
            con.execute(
                f'SELECT COUNT(*) FROM "{schema}"."{table}" WHERE "{col}" IS NULL'
            ).fetchone()
        )
        if distinct == n and nulls == 0:
            singles.append([col])
    if singles:
        return singles[:5]
    # Try common pairs (first few columns)
    pairs: list[list[str]] = []
    for i, a in enumerate(cols[:6]):
        for b in cols[i + 1 : 6]:
            distinct = scalar_int(
                con.execute(
                    f'SELECT COUNT(DISTINCT ("{a}", "{b}")) FROM "{schema}"."{table}"'
                ).fetchone()
            )
            if distinct == n:
                pairs.append([a, b])
            if len(pairs) >= 5:
                return pairs
    return pairs
