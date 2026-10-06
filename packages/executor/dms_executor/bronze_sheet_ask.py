"""Uniquely scoped workbook+sheet asks executed on bronze, not the demo warehouse.

Hostile pack questions name one .xlsx and one sheet. Cortex still answers
from ``transactions`` or abstains. DuckDB stays in this package. Constructor
must not import this.

This is explicit user scope (named file + named sheet), not product intent
inference (F28). Filter values are exact literals: BETA does not become
SKU-BETA.

The lane answers exactly two shapes: ``top N <category> by <measure>`` and
``total <measure> for sku|city <value>``. L0 is earned only when the whole
question parses as ``scope , filler shape tail`` (see ``_SCOPE_CLAUSE`` and
``_REQUEST``) and every value it executes comes from that parse. Any other
span, in any position or language, is a clause the lane would silently drop
("excluding X", "only X", "the lowest", "by average", "this year", "net of
tax"), so ``unhonored_clause`` quotes it back and the lane abstains by name.
This is an allowlist on purpose: a denylist of cue words certifies every
phrasing it forgot (KB F-0021, Refs #372).
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

# Closed grammar. Every piece below is either a clause the lane executes or filler that
# cannot change the answer; nothing else may appear anywhere in the question.
#   question := scope request
#   scope    := greeting? (in|using|from|dalam fail) <wb>.xlsx ,? sheet-clause only?
#               (ignore <other sheet>)? ,?
#   request  := filler shape tail
_CAT_NOUN = r"(?:categor(?:y|ies)|product\s+famil(?:y|ies)|product\s+line)"
_MEASURE_PAT = r"(?:sales_value_myr|stock_value_myr|myr\s+sales)"
_SCOPE_CLAUSE = re.compile(
    r"\s*(?:(?:hi|hello|hey)\b[\s,!]*)?(?:please\b[\s,]*)?"
    r"(?:in|using|from|dalam(?:\s+fail)?)\s+(?P<wb>[^\s,]+\.xlsx)\s*,?\s*"
    r"(?:on\s+the\s+(?P<s1>\w+)\s+sheet|(?:sheet|helaian)\s+(?P<s2>\w+))"
    r"(?:\s+only)?(?:\s*\(\s*ignore\s+(?P<other>\w+)\s*\))?[\s,]*",
    re.I,
)
# Politeness and request verbs only. Past tense ("what were") is left out: it implies a period.
_FILLER = (
    r"(?:please\b[\s,]*)?(?:(?:can|could|would)\s+you\s+(?:please\s+)?)?"
    r"(?:(?:what(?:'s|\s+(?:are|is))|which\s+(?:are|is)|show(?:\s+me)?|list|give\s+me"
    r"|tell\s+me|(?:sila\s+)?(?:tunjukkan|senaraikan)|apakah)\s+)?(?:the\s+)?"
)
# A city literal is one word, or one of these names whole. A second word is never bound
# because a stored value could equal it: "KL only" as a cell would serve L0 for "only KL".
# ponytail: a multi-word city not listed here abstains (safe, not served); the upgrade is a
# steward-granted city lexicon per Space.
_MULTI_WORD_CITIES = (
    "Kuala Lumpur",
    "Johor Bahru",
    "Shah Alam",
    "Petaling Jaya",
    "Subang Jaya",
    "George Town",
    "Kota Kinabalu",
    "Kota Bharu",
    "Kuala Terengganu",
    "Alor Setar",
)
_CITY_NAME = "|".join(r"\s+".join(map(re.escape, c.split())) for c in _MULTI_WORD_CITIES)
_CITY_WORD = r"(?!(?:please|thanks|thank)\b)[^\W\d_]+"
# N is ASCII digits only: "top ３" abstains like the fullwidth "？" does, not read as 3.
_SHAPE = (
    rf"top\s+(?P<n>[0-9]+)\s+{_CAT_NOUN}\s+(?:by|of)\s+(?P<m1>{_MEASURE_PAT})\b"
    rf"|(?P<n2>[0-9]+)\s+kategori\s+teratas\s+mengikut\s+(?P<m2>{_MEASURE_PAT})\b"
    rf"|total\s+(?P<m3>{_MEASURE_PAT})\s+for\s+"
    r"(?:sku\s+(?P<qs>['\"]?)(?P<sku>[A-Za-z0-9][\w-]*)(?P=qs)"
    rf"|city\s+(?P<qc>['\"]?)(?P<city>{_CITY_NAME}|{_CITY_WORD})(?P=qc))(?![\w-])"
)
_SYNONYM = r"(?:cat|categor(?:y|ies)|product\s+famil(?:y|ies)|product\s+lines?|kategori)"
_GLOSS = rf"\(\s*{_SYNONYM}(?:\s*/\s*{_SYNONYM})*\s+synonyms?\s+for\s+category\s*\)"
_TAIL = rf"(?:\s*{_GLOSS})?(?:[\s,?.!]*(?:please|thanks|thank\s+you))?[\s?.!]*"
_REQUEST = re.compile(rf"(?P<filler>{_FILLER})\b(?:{_SHAPE}){_TAIL}", re.I)
_SHAPE_RE = re.compile(rf"\b(?:{_SHAPE})", re.I)
_FILLER_RE = re.compile(_FILLER, re.I)
_FILLER_END = re.compile(rf"(?:^|(?<=\s)){_FILLER}$", re.I)
_SPAN_STRIP = " \t\r\n,;:?.!"


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


def _parse(question: str) -> tuple[re.Match[str] | None, re.Match[str] | None]:
    """``(scope, request)``. ``request`` is None unless the whole question parses."""
    q = question or ""
    scope = _SCOPE_CLAUSE.match(q)
    loose = _SCOPED.search(q)
    if scope is None or loose is None:
        return None, None
    sheet = scope["s1"] or scope["s2"]
    # The grant check read ``_SCOPED``; the parse must name the same table, not a second one.
    if (scope["wb"], sheet) != (loose.group(1), loose.group(2) or loose.group(3)):
        return None, None
    if (scope["other"] or "").lower() == sheet.lower():
        return None, None
    return scope, _REQUEST.fullmatch(q, scope.end())


def unhonored_clause(question: str) -> tuple[str, str] | None:
    """None when the whole question parses into the lane's grammar, else ``(slot, span)``.

    ``slot`` is where the parse stopped: ``scope`` (workbook/sheet clause), ``prefix`` (before
    the shape), ``shape`` (no supported shape) or ``suffix`` (after it). ``span`` is the text
    the lane would otherwise have ignored. Only called for questions the lane already claims.
    """
    q = question or ""
    scope, request = _parse(q)
    if request is not None:
        return None
    if scope is None:
        loose = _SCOPED.search(q)
        return "scope", (q[: loose.end()] if loose else q).strip(_SPAN_STRIP)
    rest = q[scope.end() :]
    shape = _SHAPE_RE.search(rest)
    if shape is None:
        lead = _FILLER_RE.match(rest)
        return "shape", rest[lead.end() if lead else 0 :].strip(_SPAN_STRIP)
    head = rest[: shape.start()]
    if _FILLER_RE.fullmatch(head) is None:
        filler = _FILLER_END.search(head)
        return "prefix", head[: filler.start() if filler else len(head)].strip(_SPAN_STRIP)
    tail = rest[shape.end() :].strip(_SPAN_STRIP)
    return ("suffix", tail) if tail else ("shape", rest.strip(_SPAN_STRIP))


def bronze_unhonored_abstain(
    question: str,
    *,
    slot: str,
    span: str,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Named ABSTAIN for a clause the lane would have ignored. No rows, no SQL, no figure."""
    reason = f"bronze_sheet_unhonored:{slot}"
    span = span[:160]
    env = build_answer_envelope(
        answer_id="ans_bronze_unhonored",
        text=(
            f"ABSTAIN {reason}. This sheet lane answers only 'top N categories by <measure>' "
            "and 'total <measure> for sku or city <value>'. It could not parse "
            f'"{span}", so it would ignore that clause and gives no figure.'
        ),
        badge="ABSTAIN",
        abstained=True,
        rows=[],
        values=[],
        sql_used=None,
        assumptions=[reason, f"bronze_sheet_unparsed:{span}"],
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
        slot, span = unhonored
        return bronze_unhonored_abstain(
            question, slot=slot, span=span, space_id=space_id, session_id=session_id
        )
    scope, request = _parse(question)
    if scope is None or request is None:
        return None
    workbook = scope["wb"]
    sheet = scope["s1"] or scope["s2"]
    table = bronze_table_for_sheet(workbook, sheet)
    ident = table.split(".", 1)[-1]
    if not _IDENT.match(ident):
        return None
    other = scope["other"]
    # "(ignore X)" is honoured only as "not the X sheet"; "(ignore Electronics)" is an exclusion.
    if other and _db_with_table(
        bronze_table_for_sheet(workbook, other).split(".", 1)[-1], warehouse
    ) is None:
        return bronze_unhonored_abstain(
            question,
            slot="scope",
            span=f"(ignore {other})",
            space_id=space_id,
            session_id=session_id,
        )

    if request["n"] or request["n2"]:
        n = int(request["n"] or request["n2"])
        if n < 1 or n > 50:
            return None
        return _grouped_top_n(
            ident,
            measure=_measure(request["m1"] or request["m2"]),
            n=n,
            workbook=workbook,
            sheet=sheet,
            warehouse=warehouse,
            space_id=space_id,
            session_id=session_id,
            question=question,
            table=table,
        )

    return _eq_filter_total(
        ident,
        col="sku" if request["sku"] else "city",
        value=request["sku"] or request["city"],
        measure=_measure(request["m3"]),
        workbook=workbook,
        sheet=sheet,
        warehouse=warehouse,
        space_id=space_id,
        session_id=session_id,
        question=question,
        table=table,
    )


def _measure(raw: str) -> str:
    raw = " ".join(raw.lower().split())
    return "sales_value_myr" if raw == "myr sales" else raw


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
