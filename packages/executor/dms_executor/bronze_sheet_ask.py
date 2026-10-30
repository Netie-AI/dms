"""Uniquely scoped workbook+sheet asks executed on bronze, not the demo warehouse.

Hostile pack questions name one .xlsx and one sheet. Cortex still answers
from ``transactions`` or abstains. DuckDB stays in this package. Constructor
must not import this.

This is explicit user scope (named file + named sheet), not product intent
inference (F28). Exact literals win. A token that matches nothing is then
resolved against DISTINCT landed values of that column only: unique complete
word (BETA -> SKU-BETA) or unique initials (KL -> Kuala Lumpur). Ambiguous or
wide columns stay unmatched so hard rule 12 still demotes empty SQL.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.bronze import bronze_table_for_sheet
from dms_executor.cca.binder import MAX_DISTINCT, norm_value
from dms_executor.envelope import build_answer_envelope
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


def resolve_unique_encoding(token: str, landed: Sequence[str]) -> str | None:
    """Map a filter token onto one landed value, or None when it is not unique.

    Exact and folded-equal first. Then a complete folded word (BETA in
    ``sku beta``) or concatenated first letters of words (KL in
    ``kuala lumpur``). Never substring-inside-a-word (crop vs Crop Insurance).
    Two hits refuse. Coverage grows only when the column can prove one encoding.
    """
    raw = (token or "").strip()
    if not raw:
        return None
    for value in landed:
        if value == raw:
            return value
    folded = norm_value(raw)
    if not folded:
        return None
    full = [value for value in landed if norm_value(value) == folded]
    if len(full) == 1:
        return full[0]
    if len(full) > 1:
        return None
    if " " not in folded:
        word_hits = []
        for value in landed:
            words = norm_value(value).split()
            if folded not in words:
                continue
            coded = bool(re.search(r"[-_]", value))
            non_first = folded in words[1:]
            if coded or non_first:
                word_hits.append(value)
        if len(word_hits) == 1:
            return word_hits[0]
        if len(word_hits) > 1:
            return None
        compact = folded.replace(" ", "")
        if len(compact) >= 2:
            acro = []
            for value in landed:
                parts = norm_value(value).split()
                if len(parts) >= 2 and "".join(p[0] for p in parts if p) == compact:
                    acro.append(value)
            if len(acro) == 1:
                return acro[0]
    return None


def maybe_bronze_sheet_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any] | None:
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
        if n < 1 or n > 50:
            return None
        measure_m = _MEASURE.search(question or "")
        raw_measure = (measure_m.group(1) if measure_m else "sales_value_myr").lower()
        measure = "sales_value_myr" if raw_measure == "myr sales" else raw_measure
        return _grouped_top_n(
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

    filt = _FOR_FILTER.search(question or "")
    measure_m = _MEASURE.search(question or "")
    if filt and measure_m and _TOTAL.search(question or ""):
        raw_measure = measure_m.group(1).lower()
        measure = "sales_value_myr" if raw_measure == "myr sales" else raw_measure
        col = filt.group(1).lower()
        value = filt.group(2).strip().strip("'\"")
        if not value or not _IDENT.match(col):
            return None
        return _eq_filter_total(
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
    return None


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
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = _cols(con, ident)
        if "category" not in cols or measure not in cols:
            return None
        rows = con.execute(
            f"""
            SELECT TRIM(CAST(category AS VARCHAR)) AS category,
                   ROUND(SUM(TRY_CAST("{measure}" AS DOUBLE)), 2) AS "{measure}"
            FROM bronze."{ident}"
            WHERE TRY_CAST("{measure}" AS DOUBLE) IS NOT NULL
              AND category IS NOT NULL
              AND TRIM(CAST(category AS VARCHAR)) <> ''
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT {n}
            """
        ).fetchall()
    finally:
        con.close()

    out_rows = [{"category": str(r[0]).strip(), measure: float(r[1])} for r in rows]
    if not out_rows:
        return None
    text = f"Found {len(out_rows)} row(s).\n" + "\n".join(
        f"  - category={r['category']}, {measure}={r[measure]}" for r in out_rows
    )
    return build_answer_envelope(
        answer_id=f"ans_bronze_{ident}",
        text=text,
        badge="L0_CERTIFIED",
        abstained=False,
        rows=out_rows,
        sql_used=(
            f'SELECT TRIM(CAST(category AS VARCHAR)), '
            f'ROUND(SUM(TRY_CAST("{measure}" AS DOUBLE)), 2) '
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
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = _cols(con, ident)
        if col not in cols or measure not in cols:
            return None
        bound, resolved_from = _bound_filter_value(con, ident, col, value)
        rows = con.execute(
            f"""
            SELECT TRIM(CAST("{col}" AS VARCHAR)) AS "{col}",
                   ROUND(SUM(TRY_CAST("{measure}" AS DOUBLE)), 2) AS "{measure}"
            FROM bronze."{ident}"
            WHERE CAST("{col}" AS VARCHAR) = ?
              AND TRY_CAST("{measure}" AS DOUBLE) IS NOT NULL
            GROUP BY 1
            """,
            [bound],
        ).fetchall()
    finally:
        con.close()

    sql_used = (
        f'SELECT TRIM(CAST("{col}" AS VARCHAR)), '
        f'ROUND(SUM(TRY_CAST("{measure}" AS DOUBLE)), 2) '
        f'FROM bronze."{ident}" WHERE CAST("{col}" AS VARCHAR) = ? GROUP BY 1'
    )
    out_rows = [{col: str(r[0]).strip(), measure: float(r[1])} for r in rows]
    encoding_note = (
        f"unique landed encoding {value!r} -> {bound!r}"
        if resolved_from
        else "exact filter"
    )
    if not out_rows:
        # Hard rule 12: executed filter matched nothing. Envelope demotes.
        return build_answer_envelope(
            answer_id=f"ans_bronze_{ident}",
            text=f"No matching rows for {col}={value!r}.",
            badge="L0_CERTIFIED",
            abstained=False,
            rows=[],
            sql_used=sql_used.replace("?", repr(bound)),
            assumptions=[
                f"bronze sheet {workbook}::{sheet}",
                encoding_note,
            ],
            space_id=space_id,
            session_id=session_id,
            ask_mode="live",
            route="bronze_sheet",
            grounded_tables=[table],
            question=question,
        )
    text = f"Found {len(out_rows)} row(s).\n" + "\n".join(
        f"  - {col}={r[col]}, {measure}={r[measure]}" for r in out_rows
    )
    return build_answer_envelope(
        answer_id=f"ans_bronze_{ident}",
        text=text,
        badge="L0_CERTIFIED",
        abstained=False,
        rows=out_rows,
        sql_used=sql_used.replace("?", repr(bound)),
        assumptions=[
            f"bronze sheet {workbook}::{sheet}",
            encoding_note,
        ],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="bronze_sheet",
        grounded_tables=[table],
        question=question,
        )


def _bound_filter_value(
    con: duckdb.DuckDBPyConnection, ident: str, col: str, value: str
) -> tuple[str, bool]:
    """Return (bound_literal, resolved_from_landed). Wide columns stay exact."""
    try:
        rows = con.execute(
            f"""
            SELECT DISTINCT TRIM(CAST("{col}" AS VARCHAR))
            FROM bronze."{ident}"
            WHERE "{col}" IS NOT NULL
              AND TRIM(CAST("{col}" AS VARCHAR)) <> ''
            ORDER BY 1
            LIMIT {MAX_DISTINCT + 1}
            """
        ).fetchall()
    except duckdb.Error:
        return value, False
    if len(rows) > MAX_DISTINCT:
        return value, False
    landed = [str(r[0]) for r in rows]
    resolved = resolve_unique_encoding(value, landed)
    if resolved is None:
        return value, False
    return resolved, resolved != value


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
                con = duckdb.connect(str(db), read_only=True)
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
