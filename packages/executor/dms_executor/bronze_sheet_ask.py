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
# ponytail: spelled rank slot is the cardinals one..twenty. Ordinals (third)
# and twenty-one+ are not a number here, so that ask does not take this lane.
# Upgrade path is SHEET-BOTTOM-N-01, which is the only place ASC may be served.
_SPELLED_RANK_N = frozenset(
    {
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
    }
)
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
_TOKEN = re.compile(r"[A-Za-z0-9_]+")
# live_ask prepends this. It is scope the grant already checked, not the ask.
_SCOPE_PREFIX = re.compile(r"^Using only [^:]+:\s*", re.I)

# Grammar the certified shapes may skip. A content word in neither this list
# nor the matched shape is an ungrounded qualifier. This is not a block list.
# by/per/in are not here: they pass only via _connector_pair. at/from never do.
_CERTIFIED_NO_GROUND = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "on",
        "for",
        "to",
        "with",
        "and",
        "or",
        "what",
        "are",
        "is",
        "show",
        "me",
        "please",
        "our",
        "this",
        "that",
        "using",
        "only",
        "sheet",
        "top",
        "teratas",
    }
)
# Outright abstain if they show up as their own token. Never add these, or
# by/per/in, to _CERTIFIED_NO_GROUND.
_NEVER_FREE = frozenset(
    {
        "not",
        "except",
        "excluding",
        "without",
        "lowest",
        "bottom",
        "least",
        "at",
        "from",
    }
)
_CONNECTORS = frozenset({"by", "per", "in"})
# Longest token run wins. Bare "value" is not a measure. "stock value" is.
_ENTRY_PHRASES: tuple[tuple[str, ...], ...] = (
    ("sales_value_myr",),
    ("stock_value_myr",),
    ("product", "families"),
    ("product", "family"),
    ("product", "line"),
    ("stock", "value"),
    ("sales", "value"),
    ("myr", "sales"),
    ("categories",),
    ("category",),
    ("kategori",),
)
_STOCK_MEASURE = re.compile(r"\bstock(?:_value_myr|\s+value)\b", re.I)
assert _NEVER_FREE.isdisjoint(_CERTIFIED_NO_GROUND)
assert _CONNECTORS.isdisjoint(_CERTIFIED_NO_GROUND)
# ponytail: "fail" is Malay for file on the certified Malay top-n question.
# Grounded only when another Malay frame word is present, so English "fail"
# stays a content word. Upgrade path: a locale tag on the ask.
_MALAY_FUNCTION = frozenset({"dalam", "fail", "helaian", "apakah", "mengikut"})
_MALAY_FRAME = frozenset({"dalam", "helaian", "apakah", "mengikut"})


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN.finditer(text or "")]


def _token_spans(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0).lower()) for m in _TOKEN.finditer(text or "")]


def _grounding_text(question: str) -> str:
    return _SCOPE_PREFIX.sub("", question or "", count=1)


def _phrase_at(toks: list[str], i: int) -> int:
    """Length of the entry phrase starting at i, or 0. Longest run wins."""
    rest = toks[i:]
    best = 0
    for phrase in _ENTRY_PHRASES:
        n = len(phrase)
        if n > best and tuple(rest[:n]) == phrase:
            best = n
    return best


def _is_rank_n(tok: str) -> bool:
    """True for the N in a ranked-N slot: a digit 1..50, or a spelled cardinal."""
    if tok.isdigit():
        return 1 <= int(tok) <= 50
    return tok in _SPELLED_RANK_N


def _grain_len(toks: list[str], i: int) -> int:
    """Length of a category grain at i. Measures (``stock value``) are not grains."""
    n = _phrase_at(toks, i)
    if not n:
        return 0
    phrase = tuple(toks[i : i + n])
    if phrase in {
        ("categories",),
        ("category",),
        ("kategori",),
        ("product", "families"),
        ("product", "family"),
        ("product", "line"),
    }:
        return n
    return 0


def _is_direction(toks: list[str], i: int) -> bool:
    """Content word in the rank slot. Allow-list words are not one.

    ``top`` and ``teratas`` sit on ``_CERTIFIED_NO_GROUND`` and are the only
    directions this lane serves, via ``_TOP_N``. Anything else next to N is
    returned as the abstain word. This is not a block list.
    """
    if i < 0 or i >= len(toks):
        return False
    tok = toks[i]
    if tok in _CERTIFIED_NO_GROUND or tok in _CONNECTORS or tok in _MALAY_FUNCTION:
        return False
    if _is_rank_n(tok) or _phrase_at(toks, i):
        return False
    return True


def _non_serving_rank_word(question: str) -> str | None:
    """Direction word of a ranked-N shape ``_TOP_N`` does not implement.

    Shapes: ``<word> <N> <grain>``, ``<N> <word> <grain>``, ``<N> <grain> <word>``.
    N is a digit 1..50 or a spelled cardinal (``bottom three``). ``_TOP_N``
    (``top N`` / ``N kategori teratas``) owns the question when it matches.
    No ASC SQL. SHEET-BOTTOM-N-01 is the serving ticket.
    """
    raw = question or ""
    text = _grounding_text(raw)
    if _TOP_N.search(raw) or _TOP_N.search(text):
        return None
    if not (_CATEGORY.search(raw) or _CATEGORY.search(text)):
        return None
    toks = _tokens(text)
    for i, tok in enumerate(toks):
        if not _is_rank_n(tok):
            continue
        if _is_direction(toks, i - 1) and _grain_len(toks, i + 1):
            return toks[i - 1]
        if _is_direction(toks, i + 1) and _grain_len(toks, i + 2):
            return toks[i + 1]
        glen = _grain_len(toks, i + 1)
        if glen and _is_direction(toks, i + 1 + glen):
            return toks[i + 1 + glen]
    return None


def _top_n_digit_span(text: str) -> tuple[int, int] | None:
    """The only digit a certified ask may contain: N in ``top N`` / ``N kategori``."""
    match = _TOP_N.search(text)
    if not match:
        return None
    group = 1 if match.group(1) else 2
    return match.start(group), match.end(group)


def _connector_pair(toks: list[str], i: int, stem: str) -> int:
    """How many tokens ``by`` / ``per`` / ``in`` consume when the next phrase is the entry.

    Returns 0 when it does not pair, and the caller abstains on that word.
    ``in`` + the workbook stem is the file-scope pair (``In {file}.xlsx``).
    ``in March`` and ``in WH-B`` return 0. ``by stock value`` returns 3.
    ``per 10023`` and ``by value`` return 0.
    """
    tok = toks[i]
    if tok == "in" and i + 1 < len(toks) and toks[i + 1] == stem:
        return 2
    n = _phrase_at(toks, i + 1)
    if n:
        return 1 + n
    return 0


def _sheet_measure(question: str) -> str:
    """``stock value`` / ``stock_value_myr`` read that column. Else sales."""
    if _STOCK_MEASURE.search(_grounding_text(question)):
        return "stock_value_myr"
    return "sales_value_myr"


def _first_ungrounded(
    question: str,
    *,
    workbook: str,
    sheet: str,
    extra: frozenset[str] = frozenset(),
) -> str | None:
    text = _grounding_text(question)
    spans = _token_spans(text)
    toks = [tok for _, _, tok in spans]
    allowed = _CERTIFIED_NO_GROUND | set(_tokens(workbook)) | set(_tokens(sheet)) | extra
    if _MALAY_FRAME.intersection(toks):
        allowed = allowed | _MALAY_FUNCTION
    stem = workbook.lower().removesuffix(".xlsx")
    top_digit = _top_n_digit_span(text)
    i = 0
    while i < len(spans):
        start, end, tok = spans[i]
        if tok in _CONNECTORS:
            taken = _connector_pair(toks, i, stem)
            if not taken:
                return tok
            i += taken
            continue
        # Any digit outside the matched top-N span abstains (year, SKU id, "excluding 3").
        if tok.isdigit():
            if top_digit == (start, end):
                i += 1
                continue
            return tok
        if tok in allowed:
            i += 1
            continue
        n = _phrase_at(toks, i)
        if n:
            i += n
            continue
        return tok
    return None


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
    if n_m:
        # Claim the lane even when the category word was replaced (RAW, parts).
        # Serving L0 still requires the category phrase; anything else abstains
        # inside maybe_bronze_sheet_ask before SQL.
        n = int(n_m.group(1) or n_m.group(2))
        if 1 <= n <= 50:
            return table
        return None
    # Not ``top N``. Still this lane, so live_ask cannot fall through.
    # maybe_bronze_sheet_ask abstains on the direction word before SQL.
    if _non_serving_rank_word(question):
        return table
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


def maybe_bronze_sheet_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any] | None:
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
    if n_m:
        n = int(n_m.group(1) or n_m.group(2))
        if n < 1 or n > 50:
            return None
        word = _first_ungrounded(question, workbook=workbook, sheet=sheet)
        if word:
            return bronze_grant_abstain(
                question,
                reason=f"ungrounded_qualifier:{word}",
                space_id=space_id,
                session_id=session_id,
            )
        if not _CATEGORY.search(question or ""):
            return None
        measure = _sheet_measure(question)
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

    word = _non_serving_rank_word(question)
    if word:
        return bronze_grant_abstain(
            question,
            reason=f"ungrounded_qualifier:{word}",
            space_id=space_id,
            session_id=session_id,
        )

    filt = _FOR_FILTER.search(question or "")
    measure_m = _MEASURE.search(question or "")
    if filt and measure_m and _TOTAL.search(question or ""):
        measure = _sheet_measure(question)
        col = filt.group(1).lower()
        value = filt.group(2).strip().strip("'\"")
        if not value or not _IDENT.match(col):
            return None
        word = _first_ungrounded(
            question,
            workbook=workbook,
            sheet=sheet,
            extra=frozenset([*(_tokens(col)), *(_tokens(value)), "total"]),
        )
        if word:
            return bronze_grant_abstain(
                question,
                reason=f"ungrounded_qualifier:{word}",
                space_id=space_id,
                session_id=session_id,
            )
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
