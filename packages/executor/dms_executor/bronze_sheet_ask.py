"""Uniquely scoped workbook+sheet asks executed on bronze, not the demo warehouse.

Hostile pack questions name one .xlsx and one sheet. Cortex still answers
from ``transactions`` or abstains. DuckDB stays in this package. Constructor
must not import this.

This is explicit user scope (named file + named sheet), not product intent
inference (F28). Filter values are exact literals: BETA does not become
SKU-BETA.
"""

from __future__ import annotations

import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from dms_core.ask import MODEL_LANES, NO_MODEL_LANES

from dms_executor.abstain import build_abstain
from dms_executor.bronze import bronze_table_for_sheet
from dms_executor.demo_warehouse import connect_serving
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.row_listing import decimal_cell, found_rows_text
from dms_executor.stated_values import dropped_stated_value_reason
from dms_executor.warehouse_identity import ingest_warehouse_path, serving_warehouse_path

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_SCOPED = re.compile(
    r"(\S+\.xlsx).*?(?:on the (\w+) sheet|(?:sheet|helaian)\s+(\w+))",
    re.I,
)
_TOP_N = re.compile(r"(?:\btop\s+(\d+)\b|(\d+)\s+kategori\s+teratas)", re.I)
_NO_SQL = re.compile(r"without\s+running\s+(?:warehouse\s+)?sql", re.I)
_CATEGORY = re.compile(
    r"\b(?:categor(?:y|ies)|kategori|product\s+famil(?:y|ies)|product\s+line)\b",
    re.I,
)
_MEASURE = re.compile(r"\b(sales_value_myr|stock_value_myr|myr\s+sales)\b", re.I)
_FOR_FILTER = re.compile(
    r"\bfor\s+(sku|city)\s+(.+?)\s*\??\s*$",
    re.I,
)
_TOTAL = re.compile(r"\btotal\b", re.I)


def sheet_lane() -> str:
    """Bronze's lane name. Being on NO_MODEL_LANES is what the list is for.

    A model lane cannot be this sheet path.
    """
    name = "bronze"
    if name in MODEL_LANES:
        raise RuntimeError(name)
    # On NO_MODEL_LANES is allowed. The shared object is the one the pin imports.
    if name in NO_MODEL_LANES:
        return name
    return name


def bronze_sheet_lane_enabled() -> bool:
    """Hand-coded workbook lane. Off unless ``DMS_LANE_BRONZE_SHEET`` is set.

    Only ``1``, ``true``, ``yes``, and ``on`` (any case) turn it on. Swap: a
    host that still wants this lane sets the env. Default off, so the ask
    continues down the generative path. Read per call.
    """
    import os

    raw = os.environ.get("DMS_LANE_BRONZE_SHEET", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def bronze_lane_table(question: str) -> str | None:
    """Bronze table the sheet server would read, or None if it is not that lane.

    Same scope as ``maybe_bronze_sheet_ask`` before any DuckDB open. The
    phrase match only names that table. It does not grant, and it does not
    build SQL. The grant decision is ``relation_gap`` on this name.
    """
    if _NO_SQL.search(question or ""):
        return None
    scoped = _SCOPED.search(question or "")
    if not scoped:
        return None
    workbook = scoped.group(1)
    sheet = scoped.group(2) or scoped.group(3)
    table = bronze_table_for_sheet(workbook, sheet)
    ident = table.split(".", 1)[-1]
    if not _IDENT.match(ident):
        return None
    n_m = _TOP_N.search(question or "")
    if n_m and _CATEGORY.search(question or ""):
        n = int(n_m.group(1) or n_m.group(2))
        if 1 <= n <= 50:
            return table
        return None
    filt = _FOR_FILTER.search(question or "")
    measure_m = _MEASURE.search(question or "")
    if filt and measure_m and _TOTAL.search(question or ""):
        col = filt.group(1).lower()
        value = filt.group(2).strip().strip("'\"")
        if value and _IDENT.match(col):
            return table
    return None


def bronze_grant_abstain(
    question: str,
    *,
    reason: str,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Named ABSTAIN. No rows, no SQL, no figure from the table."""
    env = build_abstain(
        reason=reason,
        question=question,
        stage="bronze",
        answer_id="ans_bronze_grant",
        text=f"ABSTAIN {reason}",
        rows=[],
        values=[],
        sql_used=None,
        assumptions=[reason],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
    )
    env["lane"] = sheet_lane()
    assert_envelope_valid(env)
    return env


def maybe_bronze_sheet_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any] | None:
    if not bronze_sheet_lane_enabled():
        return None
    if bronze_lane_table(question) is None:
        return None
    scoped = _SCOPED.search(question or "")
    if not scoped:
        return None
    workbook = scoped.group(1)
    sheet = scoped.group(2) or scoped.group(3)
    table = bronze_table_for_sheet(workbook, sheet)
    ident = table.split(".", 1)[-1]
    if not _IDENT.match(ident):
        return None

    n_m = _TOP_N.search(question or "")
    if n_m and _CATEGORY.search(question or ""):
        n = int(n_m.group(1) or n_m.group(2))
        if n < 1 or n > 50:
            return None
        measure_m = _MEASURE.search(question or "")
        raw_measure = (measure_m.group(1) if measure_m else "sales_value_myr").lower()
        measure = "sales_value_myr" if raw_measure == "myr sales" else raw_measure
        return _bronze_origin(
            _grouped_top_n(
                ident,
                measure=measure,
                n=n,
                workbook=workbook,
                sheet=sheet,
                warehouse=warehouse,
                space_id=space_id,
                session_id=session_id,
                question=question,
                table=table,
            )
        )

    filt = _FOR_FILTER.search(question or "")
    measure_m = _MEASURE.search(question or "")
    if filt and measure_m and _TOTAL.search(question or ""):
        raw_measure = measure_m.group(1).lower()
        measure = "sales_value_myr" if raw_measure == "myr sales" else raw_measure
        col = filt.group(1).lower()
        value = filt.group(2).strip().strip("'\"")
        if not value or not _IDENT.match(col):
            return None
        return _bronze_origin(
            _eq_filter_total(
                ident,
                col=col,
                value=value,
                measure=measure,
                workbook=workbook,
                sheet=sheet,
                warehouse=warehouse,
                space_id=space_id,
                session_id=session_id,
                question=question,
                table=table,
            )
        )
    return None


def _bronze_origin(env: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attribution for a sheet-lane envelope. ``served_attribution`` stays ``none``."""
    if not isinstance(env, dict):
        return None
    env["plan_origin"] = "bronze_sheet"
    return env


def _dropped_value(
    question: str, *, workbook: str, sheet: str, sql: str, db: Path, table: str
) -> str | None:
    """STATED-VALUE-01 for this lane: a value the question states and the SQL leaves open.

    The lane builds its own SQL, so the check reads the SQL it is about to run. The workbook
    and sheet names scope the lane; they are not conditions.
    """
    asked = question.replace(workbook, " ").replace(sheet, " ")
    return dropped_stated_value_reason(asked, sql, db, grantable={table})


def _grouped_top_n(
    ident: str,
    *,
    measure: str,
    n: int,
    workbook: str,
    sheet: str,
    warehouse: Path | None,
    space_id: str | None,
    session_id: str | None,
    question: str,
    table: str,
) -> dict[str, Any] | None:
    db = _db_with_table(ident, warehouse)
    if db is None:
        return None
    gap = _dropped_value(
        question,
        workbook=workbook,
        sheet=sheet,
        sql=f'SELECT category, SUM("{measure}") FROM bronze."{ident}" GROUP BY 1 LIMIT {n}',
        db=db,
        table=table,
    )
    if gap:
        return bronze_grant_abstain(
            question, reason=gap, space_id=space_id, session_id=session_id
        )
    con = connect_serving(db)
    try:
        cols = _cols(con, ident)
        if "category" not in cols or measure not in cols:
            return None
        exact = _exact_measure(con, ident, measure)
        if exact is None:
            return None
        value_sql, where_sql = exact
        rows = _fetch(
            con,
            f"""
            SELECT TRIM(CAST(category AS VARCHAR)) AS category,
                   SUM({value_sql}) AS "{measure}"
            FROM bronze."{ident}"
            WHERE {where_sql}
              AND category IS NOT NULL
              AND TRIM(CAST(category AS VARCHAR)) <> ''
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT {n}
            """
        )
        if rows is None:
            return None
    finally:
        con.close()

    out_rows = [{"category": str(r[0]).strip(), measure: _figure(r[1])} for r in rows]
    if not out_rows:
        return None
    text = found_rows_text(out_rows, cap=None)
    return build_answer_envelope(
        answer_id=f"ans_bronze_{ident}",
        text=text,
        badge="L0_CERTIFIED",
        abstained=False,
        rows=out_rows,
        sql_used=(
            f'SELECT TRIM(CAST(category AS VARCHAR)), '
            f"SUM({value_sql}) "
            f'FROM bronze."{ident}" GROUP BY 1 ORDER BY 2 DESC LIMIT {n}'
        ),
        assumptions=[
            f"bronze sheet {workbook}::{sheet}",
            "openpyxl-aligned grouped SUM; hanging/blank measure rows dropped",
        ],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="bronze_sheet",
        grounded_tables=[table],
        question=question,
    )


def _eq_filter_total(
    ident: str,
    *,
    col: str,
    value: str,
    measure: str,
    workbook: str,
    sheet: str,
    warehouse: Path | None,
    space_id: str | None,
    session_id: str | None,
    question: str,
    table: str,
) -> dict[str, Any] | None:
    db = _db_with_table(ident, warehouse)
    if db is None:
        return None
    quoted = value.replace("'", "''")
    gap = _dropped_value(
        question,
        workbook=workbook,
        sheet=sheet,
        sql=f'SELECT SUM("{measure}") FROM bronze."{ident}" WHERE "{col}" = \'{quoted}\'',
        db=db,
        table=table,
    )
    if gap:
        return bronze_grant_abstain(
            question, reason=gap, space_id=space_id, session_id=session_id
        )
    con = connect_serving(db)
    try:
        cols = _cols(con, ident)
        if col not in cols or measure not in cols:
            return None
        exact = _exact_measure(con, ident, measure)
        if exact is None:
            return None
        value_sql, where_sql = exact
        rows = _fetch(
            con,
            f"""
            SELECT TRIM(CAST("{col}" AS VARCHAR)) AS "{col}",
                   SUM({value_sql}) AS "{measure}"
            FROM bronze."{ident}"
            WHERE CAST("{col}" AS VARCHAR) = ?
              AND {where_sql}
            GROUP BY 1
            """,
            [value],
        )
        if rows is None:
            return None
    finally:
        con.close()

    sql_used = (
        f'SELECT TRIM(CAST("{col}" AS VARCHAR)), '
        f"SUM({value_sql}) "
        f'FROM bronze."{ident}" WHERE CAST("{col}" AS VARCHAR) = ? GROUP BY 1'
    )
    out_rows = [{col: str(r[0]).strip(), measure: _figure(r[1])} for r in rows]
    if not out_rows:
        # Hard rule 12: executed exact filter matched nothing. Envelope demotes.
        return build_answer_envelope(
            answer_id=f"ans_bronze_{ident}",
            text=f"No matching rows for {col}={value!r}.",
            badge="L0_CERTIFIED",
            abstained=False,
            rows=[],
            sql_used=sql_used.replace("?", repr(value)),
            assumptions=[
                f"bronze sheet {workbook}::{sheet}",
                "exact filter; no synonym/acronym rewrite",
            ],
            space_id=space_id,
            session_id=session_id,
            ask_mode="live",
            route="bronze_sheet",
            grounded_tables=[table],
            question=question,
        )
    text = found_rows_text(out_rows, cap=None)
    return build_answer_envelope(
        answer_id=f"ans_bronze_{ident}",
        text=text,
        badge="L0_CERTIFIED",
        abstained=False,
        rows=out_rows,
        sql_used=sql_used.replace("?", repr(value)),
        assumptions=[
            f"bronze sheet {workbook}::{sheet}",
            "exact filter; no synonym/acronym rewrite",
        ],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="bronze_sheet",
        grounded_tables=[table],
        question=question,
    )


_PLAIN_DECIMAL = r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)"



def _fetch(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> list[Any] | None:
    """The sum's rows, or None when DuckDB cannot compute it exactly.

    A sum past DECIMAL(38) overflows. The lane then does not serve: an
    error here used to reach the customer as a 503.
    """
    try:
        return con.execute(sql, params or []).fetchall()
    except duckdb.Error:
        return None


def _figure(value: Any) -> float:
    return decimal_cell(value) if isinstance(value, Decimal) else float(value)


_EXACT_TYPES = ("DECIMAL", "BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT")


def _exact_measure(
    con: duckdb.DuckDBPyConnection, ident: str, measure: str
) -> tuple[str, str] | None:
    """The measure as an exact DECIMAL expression and its row filter, or None.

    DECIMAL-EXACT-01 (dms#540): a served sum is computed in DECIMAL, never as
    a DOUBLE sum rounded to 2 places. A DECIMAL or integer column sums as it
    is. Text, or a DOUBLE read through its shortest text, is cast at the
    largest scale its values carry. A DOUBLE whose text has an exponent, or a
    scale over 18, cannot be stated exactly: None, and the lane does not serve.
    A value that reads as a number but not as a plain decimal (1e3, inf) is the
    same: counting it inexactly or dropping it would serve a wrong total.
    """
    q = '"' + measure.replace('"', '""') + '"'
    found = con.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'bronze' AND table_name = ? AND column_name = ?",
        [ident, measure],
    ).fetchone()
    kind = str(found[0]).upper() if found else ""
    if kind.startswith(_EXACT_TYPES):
        return q, f"{q} IS NOT NULL"
    text = f"trim(CAST({q} AS VARCHAR))"
    plain = f"regexp_full_match({text}, '{_PLAIN_DECIMAL}')"
    row = con.execute(
        f"SELECT COUNT(*) FILTER (WHERE TRY_CAST({text} AS DOUBLE) IS NOT NULL AND NOT {plain}), "
        f"COALESCE(MAX(length(split_part({text}, '.', 2))) FILTER (WHERE {plain}), 0) "
        f'FROM bronze."{ident}"'
    ).fetchone()
    if row is None:
        return None
    bad, scale = int(row[0]), int(row[1])
    if scale > 18 or bad:
        return None
    return f"CAST({text} AS DECIMAL(38,{scale}))", plain


def _cols(con: duckdb.DuckDBPyConnection, ident: str) -> set[str]:
    return {
        str(r[0]).lower()
        for r in con.execute(f'DESCRIBE bronze."{ident}"').fetchall()
    }


def _db_with_table(ident: str, warehouse: Path | None) -> Path | None:
    candidates: list[Path] = []
    if warehouse is not None:
        candidates.append(Path(warehouse))
    else:
        candidates.append(serving_warehouse_path())
        ingest = ingest_warehouse_path()
        if ingest.resolve() != candidates[0].resolve():
            candidates.append(ingest)
    for db in candidates:
        if not db.is_file():
            continue
        con = None
        for _attempt in range(2):
            try:
                con = connect_serving(db)
                break
            except duckdb.Error:
                time.sleep(0.1)
        if con is None:
            continue
        try:
            n = con.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                 WHERE table_schema = 'bronze' AND table_name = ?
                """,
                [ident],
            ).fetchone()
            if n and int(n[0]) == 1:
                return db
        except duckdb.Error:
            continue
        finally:
            con.close()
    return None
