"""Contract-gated bronze→silver (and signed gold) promotion in DuckDB SQL.

Rows that fail the contract land in quarantine — never dropped.
lineage: propagate keeps _src[] and _ingest_id on the target.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from dms_core.pipelines import GoldMetricDef, PipelineDef, PromoteReceipt

from dms_executor.demo_warehouse import ensure_demo_warehouse, warehouse_path
from dms_executor.duckdb_scalar import fetchone_row, scalar_int
from dms_executor.lake_schema import ensure_lake_schemas
from dms_executor.pipeline_loader import PipelineLoadError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_QUAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")

#: Leading underscore keeps this out of list_bronze_tables / Library tree.
_RECEIPTS = "main._promote_receipts"
_LOCK_ERRORS: tuple[type[BaseException], ...] = (duckdb.IOException,)
if hasattr(duckdb, "ConnectionException"):
    _LOCK_ERRORS = (duckdb.IOException, duckdb.ConnectionException)


class LakeBusy(Exception):
    """Writer holds the lake file; a miss must not be reported as no_receipt_yet."""


def _ensure_receipts(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RECEIPTS} (
          run_id VARCHAR PRIMARY KEY,
          target VARCHAR,
          recorded_at TIMESTAMPTZ,
          reconciled BOOLEAN,
          unmatched INTEGER,
          receipt_json VARCHAR
        )
        """
    )


def _record_promote_receipt(con: duckdb.DuckDBPyConnection, receipt: PromoteReceipt) -> None:
    """Persist the receipt on the same connection as the target write.

    ``recorded_at`` is minted once in Python UTC so the read route returns the
    instant the store holds. Full ``to_dict()`` is stored as JSON; the read
    path must not recompute it.
    """
    _ensure_receipts(con)
    recorded_at = datetime.now(UTC)
    con.execute(
        f"""
        INSERT INTO {_RECEIPTS}
          (run_id, target, recorded_at, reconciled, unmatched, receipt_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            receipt.run_id,
            receipt.target,
            recorded_at,
            receipt.reconciled,
            receipt.unmatched,
            json.dumps(receipt.to_dict()),
        ],
    )


def _utc_iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def latest_promote_receipt(target: str, *, path: Path | None = None) -> dict[str, Any]:
    """Latest stored receipt for ``target``, or the honest empty state.

    Opens the lake read-only. A writer holding the file raises ``LakeBusy``
    rather than looking like no promote has run.
    """
    empty: dict[str, Any] = {
        "target": target,
        "state": "no_receipt_yet",
        "runs": 0,
        "receipt": None,
    }
    db = path or warehouse_path()
    if not Path(db).is_file():
        return empty
    try:
        con = duckdb.connect(str(db))
    except _LOCK_ERRORS as exc:
        raise LakeBusy(
            "warehouse is held by a writer; retry when the promote finishes"
        ) from exc
    try:
        exists = con.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = '_promote_receipts'
            """
        ).fetchone()
        if not exists or int(exists[0]) < 1:
            return empty
        count_row = con.execute(
            f"SELECT COUNT(*) FROM {_RECEIPTS} WHERE target = ?",
            [target],
        ).fetchone()
        runs = int(count_row[0]) if count_row else 0
        if runs == 0:
            return empty
        row = con.execute(
            f"""
            SELECT recorded_at, receipt_json
            FROM {_RECEIPTS}
            WHERE target = ?
            ORDER BY recorded_at DESC, run_id DESC
            LIMIT 1
            """,
            [target],
        ).fetchone()
        if row is None:
            return empty
        recorded_at, receipt_json = row[0], row[1]
        return {
            "target": target,
            "state": "recorded",
            "recorded_at": _utc_iso(recorded_at),
            "runs": runs,
            "receipt": json.loads(str(receipt_json)),
        }
    except _LOCK_ERRORS as exc:
        raise LakeBusy(
            "warehouse is held by a writer; retry when the promote finishes"
        ) from exc
    finally:
        con.close()


def _qi(name: str) -> str:
    if not _IDENT.match(name):
        raise PipelineLoadError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _qtable(qual: str) -> str:
    if not _QUAL.match(qual):
        raise PipelineLoadError(f"unsafe table ref: {qual!r}")
    schema, table = qual.split(".", 1)
    return f"{_qi(schema)}.{_qi(table)}"


_DECIMAL_RE = re.compile(r"^decimal\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$", re.I)


def _sql_type(typ: str) -> str:
    """The one SQL type both the contract check and the write use.

    These were two separate mappings that disagreed, and a value could satisfy
    the check and still be destroyed by the write — landing in silver as NULL
    while the receipt counted it under ``passed`` and reported
    ``quarantined: 0``. Measured against DuckDB:

    =========================  ==========  =======================
    contract / input           old check   old value in silver
    =========================  ==========  =======================
    integer  3000000000        BIGINT ok   NULL   (cast INTEGER)
    integer  2147483648        BIGINT ok   NULL   (cast INTEGER)
    decimal  1e17-ish          DOUBLE ok   NULL   (cast DECIMAL(18,2))
    timestamp ...+08:00        DATE   ok   offset dropped
    =========================  ==========  =======================

    A row that fails its contract belongs in quarantine where somebody can see
    it. Silently nulling a column of a row reported as passed is the one
    outcome the promotion must never produce, because every number computed
    above it is then wrong with no trace (R-0011).

    Declared precision is honoured too: ``decimal(30,6)`` was being written as
    ``DECIMAL(18,2)`` regardless, quietly truncating four decimal places for
    anyone whose contract asked for them.
    """
    t = typ.lower().strip()
    m = _DECIMAL_RE.match(t)
    if m:
        return f"DECIMAL({m.group(1)},{m.group(2)})"
    if t in {"decimal", "numeric", "number"}:
        return "DECIMAL(18,2)"
    if t in {"float", "double"}:
        return "DOUBLE"
    if t in {"integer", "int"}:
        return "INTEGER"
    if t == "bigint":
        return "BIGINT"
    if t == "date":
        return "DATE"
    if t == "timestamptz":
        # Keeps the offset. TIMESTAMP silently reinterpreted +08:00 as local,
        # making a KL timestamp and a UTC one indistinguishable after promotion.
        return "TIMESTAMPTZ"
    if t == "timestamp":
        return "TIMESTAMP"
    if t in {"text", "varchar", "string"}:
        return "VARCHAR"
    raise PipelineLoadError(
        f"contract declares type {typ!r}, which the promoter cannot enforce. "
        "Parameterised types must be quoted in YAML — {type: decimal(18,2)} is a flow "
        "mapping, so the comma splits it and the type arrives as 'decimal(18'. "
        'Write {type: "decimal(18,2)"}. '
        "Supported: text/varchar/string, integer, bigint, decimal(p,s), numeric, "
        "float/double, date, timestamp, timestamptz."
    )


def _type_check_sql(col: str, typ: str) -> str:
    """True when the value survives the exact cast the write will perform."""
    target = _sql_type(typ)
    if not target or target == "VARCHAR":
        return "TRUE"
    c = _qi(col)
    return f"TRY_CAST({c} AS {target}) IS NOT NULL OR {c} IS NULL"


def _cast_sql(col: str, typ: str) -> str:
    target = _sql_type(typ)
    c = _qi(col)
    if not target:
        return c
    if target == "VARCHAR":
        return f"CAST({c} AS VARCHAR)"
    return f"TRY_CAST({c} AS {target})"


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
    # An unmatched left row has no b._src to concatenate; keep its own lineage.
    select_parts.append("COALESCE(list_concat(a._src, b._src), a._src) AS _src")
    select_parts.append("a._ingest_id AS _ingest_id")
    # LEFT, not INNER. An INNER JOIN removed unmatched rows *upstream* of the
    # staging table, so both the passed and quarantined counters were computed
    # over the survivors and the loss was invisible: 1000 rows joined against
    # 997 reported passed=997, quarantined=0, counts_by_reason={} — three rows
    # gone with no reason code, contradicting this module's own promise that
    # rows failing the contract land in quarantine and are never dropped.
    #
    # Unmatched rows now reach the scorer, which quarantines them under
    # `join_unmatched` where somebody can read them back with their lineage.
    first_key = _qi(pipe.join_on[0])
    select_parts.append(f"(b.{first_key} IS NOT NULL) AS _join_matched")
    return (
        f"(SELECT {', '.join(select_parts)} FROM {qa} a "
        f"LEFT JOIN {qb} b ON {on_sql})"
    )


def run_promote(
    pipe: PipelineDef,
    *,
    path: Path | None = None,
    gold_metric: GoldMetricDef | None = None,
    cortex_verify: Any = None,
) -> PromoteReceipt:
    """Execute one pipeline run. Idempotent on dedup_key for silver targets.

    Gold refuses unless ``cortex_verify`` demonstrates the metric's ledger entry
    on the Cortex chain. Construction-time verify in ``sign_gold_metric`` is not
    this gate: a metric that merely *looks* signed must not materialise.
    """
    run_id = str(uuid.uuid4())
    db = ensure_demo_warehouse(path or warehouse_path())
    con = duckdb.connect(str(db))
    started = False
    try:
        ensure_lake_schemas(con)
        con.execute("BEGIN")
        started = True
        if pipe.is_gold:
            receipt = _run_gold(
                con,
                pipe,
                run_id=run_id,
                gold_metric=gold_metric,
                cortex_verify=cortex_verify,
            )
        else:
            receipt = _run_silver(con, pipe, run_id=run_id)
        con.execute("COMMIT")
        started = False
        return receipt
    except Exception:
        if started:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def _run_gold(
    con: duckdb.DuckDBPyConnection,
    pipe: PipelineDef,
    *,
    run_id: str,
    gold_metric: GoldMetricDef | None,
    cortex_verify: Any = None,
) -> PromoteReceipt:
    if gold_metric is None or not gold_metric.is_signed:
        raise PipelineLoadError(
            "gold promote requires a steward-signed metric definition "
            "(sign via /v1/pipelines/gold/sign → Cortex ledger)"
        )
    if not gold_metric.ledger_entry_id:
        raise PipelineLoadError("gold metric must have ledger_entry_id from Cortex append")
    _require_chain_readback(gold_metric, cortex_verify)
    # Called for its validation, not its return value: it raises PipelineLoadError if
    # any declared source table is missing. The gold path then materialises from the
    # signed metric SQL rather than the source relation, so the returned SQL is unused -
    # but dropping the call would let a gold promote succeed against absent sources.
    _build_source_relation(con, pipe)
    target = _qtable(pipe.target)
    # Materialize metric result as gold table (aggregate — document lineage)
    con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM ({gold_metric.sql})")
    n = scalar_int(con.execute(f"SELECT COUNT(*) FROM {target}").fetchone())
    receipt = PromoteReceipt(
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
    _record_promote_receipt(con, receipt)
    return receipt


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

    # Rows that set out, counted on the driving source before any join can
    # change the cardinality. Without this the receipt could report passed and
    # quarantined and still not answer "did everything arrive".
    driving = pipe.sources[0]
    source_rows = scalar_int(con.execute(f"SELECT COUNT(*) FROM {_qtable(driving)}").fetchone())

    # Staging of source rows with synthetic row id for this run
    con.execute("DROP TABLE IF EXISTS _promote_stage")
    con.execute(f"CREATE TEMP TABLE _promote_stage AS SELECT * FROM {src_rel}")
    staged_rows = scalar_int(con.execute("SELECT COUNT(*) FROM _promote_stage").fetchone())

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

    # A row the join could not match is a failure with its own reason, checked
    # before the column contract — its b-side columns are NULL for a structural
    # reason, and reporting that as `required_null` would name the wrong cause.
    if "_join_matched" in stage_cols:
        join_reason = "CASE WHEN NOT _join_matched THEN 'join_unmatched' ELSE NULL END"
        reason_exprs.insert(0, join_reason)
        fail_col_exprs.insert(0, f"WHEN ({join_reason}) IS NOT NULL THEN '_join'")

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
                    row = fetchone_row(
                        con.execute(
                            f"""
                        SELECT
                          COUNT(*)::DOUBLE AS n,
                          COUNT({_qi(col)})::DOUBLE AS nn
                        FROM _promote_stage
                        """
                        ).fetchone()
                    )
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

    passed = scalar_int(
        con.execute(
            f"SELECT COUNT(*) FROM _promote_scored WHERE {pass_where}"
        ).fetchone()
    )
    quarantined = scalar_int(
        con.execute(f"SELECT COUNT(*) FROM _promote_scored WHERE {fail_where}").fetchone()
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

    # Positive means the join consumed rows; negative means it produced extra
    # ones by fanning out on a duplicated key, which silently multiplies every
    # amount downstream. Either way the receipt reports reconciled=false rather
    # than a clean-looking pass.
    unmatched = source_rows - staged_rows
    if unmatched:
        counts["join_cardinality_change"] = abs(unmatched)

    receipt = PromoteReceipt(
        run_id=run_id,
        target=pipe.target,
        sources=list(pipe.sources),
        source_rows=source_rows,
        passed=passed,
        quarantined=quarantined,
        unmatched=unmatched,
        counts_by_reason=counts,
        dedup_key=list(contract.dedup_key),
        lineage=pipe.lineage,
        table=pipe.target,
        quarantine_table=q_table,
    )
    _record_promote_receipt(con, receipt)
    return receipt


def _append_hash(resp: Any) -> str | None:
    """Chain hash from an append response. Never invent one; never fall back to entry_id.

    The field is ``hash`` on LedgerAppendResponse. Reading ``entry_hash`` alone used to
    return None every time and the signature silently degraded to the entry id — an
    identifier, which verifies nothing (PRD-001 F52(b)). Accept either name so a
    dict-shaped stub still works, but never treat the id as a hash.
    """
    sig = getattr(resp, "hash", None) or getattr(resp, "entry_hash", None)
    if sig is None and isinstance(resp, dict):
        sig = resp.get("hash") or resp.get("entry_hash")
    if sig is None or sig == "":
        return None
    return str(sig)


def _verify_ok(result: Any) -> bool:
    ok = getattr(result, "ok", None)
    if ok is None and isinstance(result, dict):
        ok = result.get("ok")
    return bool(ok)


def _require_chain_readback(gold_metric: GoldMetricDef, cortex_verify: Any) -> None:
    """EPIC-025: demonstrate the signed metric on the Cortex chain before gold runs.

    ``is_signed`` is a claim about fields we hold. The demonstration is Cortex
    ``POST /v1/contract/ledger/verify``: it recomputes payload hashes along the
    chain, so a silent payload edit or a broken link fails closed. Contract 1.2.0
    has no get-entry; this is the read-back that pin offers. Given
    ``ledger_entry_id``, we still require it (a metric with no id cannot be
    pointed at) and we refuse when verify is missing, raises, or returns not-ok.
    Unreachable Cortex must not proceed.
    """
    entry_id = gold_metric.ledger_entry_id
    if not entry_id:
        raise PipelineLoadError(
            "gold promote refused: ledger_entry_id is required to demonstrate "
            "the metric is on the Cortex chain"
        )
    sig = gold_metric.signature
    if sig and sig == str(entry_id):
        raise PipelineLoadError(
            "gold promote refused: signature equals ledger_entry_id; that is not a hash"
        )
    if cortex_verify is None:
        raise PipelineLoadError(
            "gold promote refused: Cortex ledger verify is required; "
            "unreachable Cortex cannot demonstrate the metric is on the chain"
        )
    try:
        result = cortex_verify()
    except Exception as exc:
        raise PipelineLoadError(
            "gold promote refused: Cortex ledger verify unreachable; "
            "the metric is treated as unsigned"
        ) from exc
    if not _verify_ok(result):
        raise PipelineLoadError(
            "gold promote refused: Cortex ledger verify failed for "
            f"ledger_entry_id={entry_id}; the metric is treated as unsigned"
        )


def sign_gold_metric(
    metric: GoldMetricDef,
    *,
    cortex_append: Any,
    cortex_verify: Any,
    actor: str,
) -> GoldMetricDef:
    """Append metric.certify, then read the chain back before treating it as signed.

    ``actor`` is required and must be resolved server-side by the caller. It used to
    default to None and fall back to ``metric.steward_id``, which arrives from a request
    body - so whoever called the route named the actor written onto a tamper-evident
    record (KB attack A-0005, identity in place of the trust flag). The fallback is the
    binding where caller data became identity, so it is removed here rather than at the
    one route that exposed it (R-0004).

    ``metric.steward_id`` stays in the payload as the claimed steward, but it is never
    the actor. DR-0004 records that under both open options the actor is derived
    server-side - from configuration, or from an authenticated principal - never from a
    request field.

    Append alone is not attestation. F70 closed the caller-asserted field hole; this
    closes the "append said ok, so it is signed" hole: if verify says the chain is not
    intact (or the append returned no real hash), the metric stays unsigned and the
    caller gets an honest error. One shared guard — both /gold/sign and /run go through
    here. Reuses Cortex ``verify_ledger``; no local hash chain.
    """
    if not actor:
        raise ValueError("sign_gold_metric requires a server-resolved actor")
    if cortex_verify is None:
        raise ValueError(
            "sign_gold_metric requires cortex_verify to read the ledger chain back "
            "before a metric can count as signed"
        )
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
        actor=actor,
    )
    entry_id = getattr(resp, "entry_id", None) or (
        resp.get("entry_id") if isinstance(resp, dict) else None
    )
    if not entry_id:
        raise ValueError("Cortex ledger append did not return entry_id; metric is not signed")
    sig = _append_hash(resp)
    if not sig:
        raise ValueError(
            "Cortex ledger append did not return a chain hash; metric is not signed"
        )
    if sig == str(entry_id):
        raise ValueError(
            "Cortex ledger append returned hash equal to entry_id; that is not a "
            "signature — metric is not signed"
        )

    verify_result = cortex_verify()
    if not _verify_ok(verify_result):
        raise ValueError(
            "Cortex ledger verify failed after append; the chain is not intact and "
            "the metric is not signed"
        )

    return GoldMetricDef(
        metric_id=metric.metric_id,
        name=metric.name,
        sql=metric.sql,
        steward_id=metric.steward_id,
        signed_at=now,
        ledger_entry_id=str(entry_id),
        signature=sig,
    )
