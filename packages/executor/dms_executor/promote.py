"""Contract-gated bronze→silver (and signed gold) promotion in DuckDB SQL.

Rows that fail the contract land in quarantine — never dropped.
lineage: propagate keeps _src[] and _ingest_id on the target.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dms_core.pipelines import GoldMetricDef, PipelineDef, PromoteReceipt
from dms_executor.demo_warehouse import ensure_demo_warehouse, warehouse_path
from dms_executor.lake_schema import ensure_lake_schemas
from dms_executor.pipeline_loader import PipelineLoadError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_QUAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


def _qi(name: str) -> str:
    if not _IDENT.match(name):
        raise PipelineLoadError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _qtable(qual: str) -> str:
    if not _QUAL.match(qual):
        raise PipelineLoadError(f"unsafe table ref: {qual!r}")
    schema, table = qual.split(".", 1)
    return f"{_qi(schema)}.{_qi(table)}"


def _type_check_sql(col: str, typ: str) -> str:
    """Return SQL boolean expr that is true when value matches contract type."""
    c = _qi(col)
    t = typ.lower().strip()
    if t.startswith("decimal") or t in {"numeric", "number", "float", "double"}:
        return f"TRY_CAST({c} AS DOUBLE) IS NOT NULL OR {c} IS NULL"
    if t in {"integer", "int", "bigint"}:
        return f"TRY_CAST({c} AS BIGINT) IS NOT NULL OR {c} IS NULL"
    if t in {"date", "timestamp", "timestamptz"}:
        return f"TRY_CAST({c} AS DATE) IS NOT NULL OR {c} IS NULL"
    if t in {"text", "varchar", "string"}:
        return "TRUE"
    # unknown types: accept
    return "TRUE"


def _cast_sql(col: str, typ: str) -> str:
    c = _qi(col)
    t = typ.lower().strip()
    if t.startswith("decimal") or t in {"numeric", "number", "float", "double"}:
        return f"TRY_CAST({c} AS DECIMAL(18,2))"
    if t in {"integer", "int", "bigint"}:
        return f"TRY_CAST({c} AS INTEGER)"
    if t in {"date"}:
        return f"TRY_CAST({c} AS DATE)"
    if t in {"timestamp", "timestamptz"}:
        return f"TRY_CAST({c} AS TIMESTAMP)"
    return f"CAST({c} AS VARCHAR)"


def _fail_reason_sql(col: str, typ: str, *, required: bool, min_v: float | None) -> str:
    """Build CASE expression yielding failure reason for one column (or NULL if ok)."""
    c = _qi(col)
    parts: list[str] = []
    if required:
        parts.append(f"WHEN {c} IS NULL THEN 'required_null'")
    parts.append(f"WHEN NOT ({_type_check_sql(col, typ)}) THEN 'type_mismatch'")
    if min_v is not None:
        parts.append(
            f"WHEN TRY_CAST({c} AS DOUBLE) IS NOT NULL "
            f"AND TRY_CAST({c} AS DOUBLE) < {float(min_v)} THEN 'below_min'"
        )
    if not parts:
        return "NULL"
    return "CASE " + " ".join(parts) + " ELSE NULL END"


def _build_source_relation(con: duckdb.DuckDBPyConnection, pipe: PipelineDef) -> str:
    """Return SQL for a relation named _src_rel with business cols + _src + _ingest_id."""
    sources = pipe.sources
    for s in sources:
        q = _qtable(s)
        exists = con.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            """,
            s.split(".", 1),
        ).fetchone()
        if not exists or int(exists[0]) < 1:
            raise PipelineLoadError(f"source table missing: {s}")

    if len(sources) == 1:
        q = _qtable(sources[0])
        # Ensure provenance columns exist
        cols = {
            r[0]
            for r in con.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ? AND table_name = ?
                """,
                sources[0].split(".", 1),
            ).fetchall()
        }
        if pipe.lineage == "propagate":
            if "_src" not in cols or "_ingest_id" not in cols:
                raise PipelineLoadError(
                    f"source {sources[0]} missing _src/_ingest_id required by lineage:propagate"
                )
        return f"(SELECT * FROM {q})"

    # Multi-source: equi-join on join_on, concatenate _src arrays
    if not pipe.join_on:
        raise PipelineLoadError("multi-source pipeline requires join_on")
    a, b = sources[0], sources[1]
    if len(sources) > 2:
        raise PipelineLoadError("only two-source joins supported in T12")
    qa, qb = _qtable(a), _qtable(b)
    on_parts = [f"a.{_qi(k)} = b.{_qi(k)}" for k in pipe.join_on]
    on_sql = " AND ".join(on_parts)
    # Prefer left table business columns; keep join keys once
    a_cols = [
        r[0]
        for r in con.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            ORDER BY ordinal_position
            """,
            a.split(".", 1),
        ).fetchall()
    ]
    b_cols = [
        r[0]
        for r in con.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            ORDER BY ordinal_position
            """,
            b.split(".", 1),
        ).fetchall()
    ]
    skip = {"_src", "_ingest_id"} | set(pipe.join_on)
    select_parts = [f"a.{_qi(c)} AS {_qi(c)}" for c in a_cols if c not in skip]
    for c in b_cols:
        if c in skip or c in a_cols:
            continue
        select_parts.append(f"b.{_qi(c)} AS {_qi(c)}")
    for k in pipe.join_on:
        select_parts.append(f"a.{_qi(k)} AS {_qi(k)}")
    select_parts.append("list_concat(a._src, b._src) AS _src")
    select_parts.append("a._ingest_id AS _ingest_id")
    return (
        f"(SELECT {', '.join(select_parts)} FROM {qa} a "
        f"INNER JOIN {qb} b ON {on_sql})"
    )


def run_promote(
    pipe: PipelineDef,
    *,
    path: Path | None = None,
    gold_metric: GoldMetricDef | None = None,
) -> PromoteReceipt:
    """Execute one pipeline run. Idempotent on dedup_key for silver targets."""
    run_id = str(uuid.uuid4())
    db = ensure_demo_warehouse(path or warehouse_path())
    con = duckdb.connect(str(db))
    try:
        ensure_lake_schemas(con)
        if pipe.is_gold:
            return _run_gold(con, pipe, run_id=run_id, gold_metric=gold_metric)
        return _run_silver(con, pipe, run_id=run_id)
    finally:
        con.close()


def _run_gold(
    con: duckdb.DuckDBPyConnection,
    pipe: PipelineDef,
    *,
    run_id: str,
    gold_metric: GoldMetricDef | None,
) -> PromoteReceipt:
    if gold_metric is None or not gold_metric.is_signed:
        raise PipelineLoadError(
            "gold promote requires a steward-signed metric definition "
            "(sign via /v1/pipelines/gold/sign → Cortex ledger)"
        )
    if not gold_metric.ledger_entry_id:
        raise PipelineLoadError("gold metric must have ledger_entry_id from Cortex append")
    src_sql = _build_source_relation(con, pipe)
    target = _qtable(pipe.target)
    # Materialize metric result as gold table (aggregate — document lineage)
    con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM ({gold_metric.sql})")
    n = int(con.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0])
    return PromoteReceipt(
        run_id=run_id,
        target=pipe.target,
        sources=list(pipe.sources),
        passed=n,
        quarantined=0,
        counts_by_reason={},
        dedup_key=[],
        lineage=pipe.lineage,
        table=pipe.target,
        quarantine_table=None,
    )


def _run_silver(
    con: duckdb.DuckDBPyConnection,
    pipe: PipelineDef,
    *,
    run_id: str,
) -> PromoteReceipt:
    if pipe.contract is None:
        raise PipelineLoadError("silver promote requires a contract")
    contract = pipe.contract
    src_rel = _build_source_relation(con, pipe)
    target = _qtable(pipe.target)
    q_table = pipe.quarantine_table
    q_qual = _qtable(q_table)

    # Staging of source rows with synthetic row id for this run
    con.execute("DROP TABLE IF EXISTS _promote_stage")
    con.execute(f"CREATE TEMP TABLE _promote_stage AS SELECT * FROM {src_rel}")

    biz_cols = list(contract.columns.keys())
    # Validate columns exist
    stage_cols = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = '_promote_stage'"
        ).fetchall()
    }
    for c in biz_cols:
        if c not in stage_cols:
            raise PipelineLoadError(f"contract column {c!r} missing from sources")
    for k in contract.dedup_key:
        if k not in stage_cols:
            raise PipelineLoadError(f"dedup_key column {k!r} missing from sources")

    # Build per-column failure reasons; first failing column wins
    reason_exprs: list[str] = []
    fail_col_exprs: list[str] = []
    for col, spec in contract.columns.items():
        r_sql = _fail_reason_sql(col, spec.type, required=spec.required, min_v=spec.min)
        if spec.allowed_from:
            if not _QUAL.match(spec.allowed_from):
                raise PipelineLoadError(f"bad allowed_from: {spec.allowed_from!r}")
            dim_schema, dim_table = spec.allowed_from.split(".", 1)
            exists = con.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = ? AND table_name = ?
                """,
                [dim_schema, dim_table],
            ).fetchone()
            if exists and int(exists[0]) > 0:
                dim_cols = {
                    r[0]
                    for r in con.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = ? AND table_name = ?
                        """,
                        [dim_schema, dim_table],
                    ).fetchall()
                }
                dim_col = col if col in dim_cols else ("code" if "code" in dim_cols else None)
                if dim_col:
                    c = _qi(col)
                    allowed = (
                        f"CASE WHEN {c} IS NOT NULL AND NOT EXISTS ("
                        f"SELECT 1 FROM {_qi(dim_schema)}.{_qi(dim_table)} d "
                        f"WHERE d.{_qi(dim_col)} = CAST({c} AS VARCHAR)"
                        f") THEN 'not_in_allowed_from' ELSE NULL END"
                    )
                    r_sql = f"COALESCE({r_sql}, {allowed})"
        reason_exprs.append(r_sql)
        fail_col_exprs.append(f"WHEN ({r_sql}) IS NOT NULL THEN '{col}'")

    coalesce_reasons = "COALESCE(" + ", ".join(reason_exprs) + ")"
    fail_col_case = "CASE " + " ".join(fail_col_exprs) + " ELSE NULL END"

    con.execute("DROP TABLE IF EXISTS _promote_scored")
    con.execute(
        f"""
        CREATE TEMP TABLE _promote_scored AS
        SELECT
          s.*,
          ({coalesce_reasons}) AS _fail_reason,
          ({fail_col_case}) AS _fail_column
        FROM _promote_stage s
        """
    )

    # Expectations (table-level) — amount_not_null_rate etc. recorded in receipt, not row drop
    expectation_notes: dict[str, int] = {}
    for exp in contract.expectations:
        for key, rule in exp.items():
            if key.endswith("_not_null_rate") and ">=" in rule:
                col = key[: -len("_not_null_rate")]
                threshold = float(rule.split(">=")[1].strip())
                if col in stage_cols:
                    row = con.execute(
                        f"""
                        SELECT
                          COUNT(*)::DOUBLE AS n,
                          COUNT({_qi(col)})::DOUBLE AS nn
                        FROM _promote_stage
                        """
                    ).fetchone()
                    n, nn = float(row[0]), float(row[1])
                    rate = (nn / n) if n else 1.0
                    if rate < threshold:
                        expectation_notes[f"expectation_fail:{key}"] = 1

    # Pass / fail splits
    pass_where = "_fail_reason IS NULL"
    fail_where = "_fail_reason IS NOT NULL"

    cast_list = [f"{_cast_sql(c, contract.columns[c].type)} AS {_qi(c)}" for c in biz_cols]
    lineage_cols = ""
    if pipe.lineage == "propagate":
        cast_list.append("_src")
        cast_list.append("_ingest_id")
        lineage_cols = ", _src, _ingest_id"

    # Create empty target if needed, then upsert by dedup_key
    select_pass = f"SELECT {', '.join(cast_list)} FROM _promote_scored WHERE {pass_where}"

    if not contract.dedup_key:
        con.execute(f"CREATE OR REPLACE TABLE {target} AS {select_pass}")
    else:
        # Ensure target exists with correct schema
        con.execute(f"CREATE TABLE IF NOT EXISTS {target} AS {select_pass} LIMIT 0")
        # Delete existing keys that appear in this pass set, then insert
        key_pred = " AND ".join(
            f"t.{_qi(k)} = p.{_qi(k)}" for k in contract.dedup_key
        )
        con.execute(
            f"""
            DELETE FROM {target} t
            WHERE EXISTS (
              SELECT 1 FROM ({select_pass}) p WHERE {key_pred}
            )
            """
        )
        con.execute(f"INSERT INTO {target} {select_pass}")

    # Quarantine — replace rows for this run's failing keys (append all fails this run)
    q_select = f"""
        SELECT
          {", ".join(_qi(c) for c in biz_cols)}{lineage_cols},
          _fail_column AS failing_column,
          _fail_reason AS reason,
          '{run_id}'::VARCHAR AS run_id
        FROM _promote_scored
        WHERE {fail_where}
    """
    # Recreate quarantine table schema; keep history by appending if exists
    con.execute(f"CREATE TABLE IF NOT EXISTS {q_qual} AS {q_select} LIMIT 0")
    # On re-run, remove prior quarantine rows for same dedup keys then insert
    if contract.dedup_key:
        key_pred_q = " AND ".join(
            f"q.{_qi(k)} = f.{_qi(k)}" for k in contract.dedup_key
        )
        con.execute(
            f"""
            DELETE FROM {q_qual} q
            WHERE EXISTS (
              SELECT 1 FROM ({q_select}) f WHERE {key_pred_q}
            )
            """
        )
    con.execute(f"INSERT INTO {q_qual} {q_select}")

    passed = int(con.execute(f"SELECT COUNT(*) FROM _promote_scored WHERE {pass_where}").fetchone()[0])
    quarantined = int(
        con.execute(f"SELECT COUNT(*) FROM _promote_scored WHERE {fail_where}").fetchone()[0]
    )
    reason_rows = con.execute(
        f"""
        SELECT _fail_reason, COUNT(*) FROM _promote_scored
        WHERE {fail_where}
        GROUP BY 1
        """
    ).fetchall()
    counts = {str(r): int(c) for r, c in reason_rows}
    counts.update(expectation_notes)

    return PromoteReceipt(
        run_id=run_id,
        target=pipe.target,
        sources=list(pipe.sources),
        passed=passed,
        quarantined=quarantined,
        counts_by_reason=counts,
        dedup_key=list(contract.dedup_key),
        lineage=pipe.lineage,
        table=pipe.target,
        quarantine_table=q_table,
    )


def sign_gold_metric(
    metric: GoldMetricDef,
    *,
    cortex_append: Any,
    actor: str | None = None,
) -> GoldMetricDef:
    """Append metric.certify to Cortex ledger; return metric with ledger_entry_id + signature."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "metric_id": metric.metric_id,
        "name": metric.name,
        "sql": metric.sql,
        "steward_id": metric.steward_id,
        "signed_at": now,
    }
    resp = cortex_append(
        event_type="metric.certify",
        payload=payload,
        actor=actor or metric.steward_id,
    )
    entry_id = getattr(resp, "entry_id", None) or (resp.get("entry_id") if isinstance(resp, dict) else None)
    sig = getattr(resp, "entry_hash", None) or entry_id or f"sig_{uuid.uuid4().hex[:16]}"
    return GoldMetricDef(
        metric_id=metric.metric_id,
        name=metric.name,
        sql=metric.sql,
        steward_id=metric.steward_id,
        signed_at=now,
        ledger_entry_id=str(entry_id) if entry_id else None,
        signature=str(sig),
    )
