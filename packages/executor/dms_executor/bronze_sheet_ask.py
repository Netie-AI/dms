"""Uniquely scoped workbook+sheet asks executed on bronze, not the demo warehouse.

Hostile pack questions name one .xlsx and one sheet. Cortex still answers
from ``transactions`` or abstains. DuckDB stays in this package. Constructor
must not import this.

This is explicit user scope (named file + named sheet), not product intent
inference (F28). Filter values are exact literals: BETA does not become
SKU-BETA.

The lane answers exactly two shapes: ``top N <category> [by <measure>]`` and
``total <measure> for sku|city <value>``. L0 is earned only when the question
is nothing more than that. A clause the lane would silently drop ("excluding
X", "the lowest", "by average", "as a percentage", "for sku X" on a top-N,
"per category", a year) used to be answered anyway as a plain SUM under
``L0_CERTIFIED`` (red team 2026-10-02). ``unhonored_clause`` now names it and
the lane abstains. The shape and the tail after the measure are closed-world;
the text before the shape and any parenthetical gloss are checked against a
cue list, which cannot enumerate every phrasing (KB F-0021): an unlisted
clause in the prefix is still ignored.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import duckdb
from dms_core.ask import MODEL_LANES, NO_MODEL_LANES

from dms_executor.bronze import bronze_table_for_sheet
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
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

_CAT_NOUN = r"(?:categor(?:y|ies)|product\s+famil(?:y|ies)|product\s+line)"
_MEASURE_PAT = r"(?:sales_value_myr|stock_value_myr|myr\s+sales)"
# The whole supported top-N shape: ``top N <category noun>`` or the Malay form, then an
# optional ``by <measure>``. ``tail`` is everything after; it must be only punctuation or a
# harmless parenthetical gloss, anything else is a clause the lane cannot honour.
_TOPN_SHAPE = re.compile(
    rf"(?:\btop\s+\d+\s+{_CAT_NOUN}\b|\b\d+\s+kategori\s+teratas\b)"
    rf"(?:\s+(?:by|mengikut|of)\s+{_MEASURE_PAT}\b)?"
    r"(?P<tail>.*)$",
    re.I | re.S,
)
_TOTAL_SHAPE = re.compile(
    rf"\btotal\s+{_MEASURE_PAT}\s+for\s+(?:sku|city)\s+\S",
    re.I,
)
_GLOSS_TAIL = re.compile(r"\s*(\([^)]*\))?\s*[?.!]*\s*", re.I)
_WORKBOOK_TOKEN = re.compile(r"\S+\.xlsx", re.I)
_IGNORE_OTHER_SHEET = re.compile(r"\bignore\s+\w+_\w+", re.I)
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
# Kinds of clause that change the answer. Used on the text before the shape and inside
# parentheticals; the closed tail check does the rest.
_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "exclusion",
        re.compile(
            r"\b(?:exclud\w*|except\w*|without|other\s+than|apart\s+from|aside\s+from|besides"
            r"|not|never|no|kecuali|tanpa|bukan)\b",
            re.I,
        ),
    ),
    (
        "direction",
        re.compile(
            r"\b(?:lowest|least|bottom|smallest|fewest|worst|cheapest|terendah)\b"
            r"|\bpaling\s+sedikit\b",
            re.I,
        ),
    ),
    (
        "aggregation",
        re.compile(
            r"\b(?:average|avg|mean|median|count\w*|number\s+of|how\s+many|percent\w*|share"
            r"|ratio|proportion|rate|max\w*|min\w*|growth|change|trend|differen\w*|variance"
            r"|purata|bilangan|peratus)\b|%",
            re.I,
        ),
    ),
    (
        "comparison",
        re.compile(r"\b(?:compar\w*|versus|vs|than|above|below|between)\b", re.I),
    ),
    (
        "grain",
        re.compile(r"\b(?:per|each|every|setiap|breakdown|split|group\w*)\b", re.I),
    ),
    (
        "time",
        re.compile(
            r"\b(?:last|previous|current|today|yesterday|ytd|mtd|quarter|q[1-4]|monthly|weekly"
            r"|daily|yearly|annual|20\d\d|bulan|tahun|minggu|hari|"
            + _MONTHS
            + r")\b",
            re.I,
        ),
    ),
)


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


def bronze_lane_table(question: str) -> str | None:
    """Bronze table this question would read, or None if it is not that lane.

    Same scope as ``maybe_bronze_sheet_ask`` before any DuckDB open. The
    grant decision stays with ``table_is_granted`` on the caller's readable
    set. This function does not decide grants.
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
    env = build_answer_envelope(
        answer_id="ans_bronze_grant",
        text=f"ABSTAIN {reason}",
        badge="ABSTAIN",
        abstained=True,
        rows=[],
        values=[],
        sql_used=None,
        assumptions=[reason],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
        question=question,
    )
    env["lane"] = sheet_lane()
    assert_envelope_valid(env)
    return env


def _cue_kind(text: str) -> str | None:
    """First kind of answer-changing clause named in ``text``, or None."""
    cleaned = _IGNORE_OTHER_SHEET.sub(" ", _WORKBOOK_TOKEN.sub(" ", text))
    for kind, pattern in _CUES:
        if pattern.search(cleaned):
            return kind
    return None


def unhonored_clause(question: str) -> str | None:
    """None when the question is exactly a supported shape, else the clause kind it would drop.

    Only called for questions the lane already claims (``bronze_lane_table``). ``shape`` means
    the claimed question is not the closed form at all (for example ``top 1 SKU per category``,
    or ``by number of SKUs``), so the lane must not guess a measure or grain for it.
    """
    q = question or ""
    if _TOP_N.search(q) and _CATEGORY.search(q):
        m = _TOPN_SHAPE.search(q)
        if m is None:
            return "shape"
        tail = m.group("tail")
        gloss = _GLOSS_TAIL.fullmatch(tail)
        if gloss is None:
            return _cue_kind(tail) or "shape"
        return _cue_kind(q[: m.start()]) or _cue_kind(gloss.group(1) or "")
    if _FOR_FILTER.search(q) and _MEASURE.search(q) and _TOTAL.search(q):
        m = _TOTAL_SHAPE.search(q)
        if m is None:
            return "shape"
        return _cue_kind(q[: m.start()])
    return None


def bronze_unhonored_abstain(
    question: str,
    *,
    kind: str,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Named ABSTAIN for a clause the lane would have ignored. No rows, no SQL, no figure."""
    reason = f"bronze_sheet_unhonored:{kind}"
    env = build_answer_envelope(
        answer_id="ans_bronze_unhonored",
        text=(
            f"ABSTAIN {reason}. This sheet lane answers only 'top N categories by <measure>' "
            "and 'total <measure> for sku or city <value>'. Your question carries another "
            "clause it would ignore, so it gives no figure."
        ),
        badge="ABSTAIN",
        abstained=True,
        rows=[],
        values=[],
        sql_used=None,
        assumptions=[reason],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
        question=question,
    )
    assert_envelope_valid(env)
    return env


def maybe_bronze_sheet_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any] | None:
    if bronze_lane_table(question) is None:
        return None
    unhonored = unhonored_clause(question)
    if unhonored is not None:
        return bronze_unhonored_abstain(
            question, kind=unhonored, space_id=space_id, session_id=session_id
        )
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
        rows = con.execute(
            f"""
            SELECT TRIM(CAST("{col}" AS VARCHAR)) AS "{col}",
                   ROUND(SUM(TRY_CAST("{measure}" AS DOUBLE)), 2) AS "{measure}"
            FROM bronze."{ident}"
            WHERE CAST("{col}" AS VARCHAR) = ?
              AND TRY_CAST("{measure}" AS DOUBLE) IS NOT NULL
            GROUP BY 1
            """,
            [value],
        ).fetchall()
    finally:
        con.close()

    sql_used = (
        f'SELECT TRIM(CAST("{col}" AS VARCHAR)), '
        f'ROUND(SUM(TRY_CAST("{measure}" AS DOUBLE)), 2) '
        f'FROM bronze."{ident}" WHERE CAST("{col}" AS VARCHAR) = ? GROUP BY 1'
    )
    out_rows = [{col: str(r[0]).strip(), measure: float(r[1])} for r in rows]
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
    text = f"Found {len(out_rows)} row(s).\n" + "\n".join(
        f"  - {col}={r[col]}, {measure}={r[measure]}" for r in out_rows
    )
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
